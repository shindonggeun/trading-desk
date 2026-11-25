import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select

from desk import notify, roles
from desk import nq_weekly_alerts as alerts
from desk.account_owner import AccountOwnerStore
from desk.auth import Auth
from desk.db import make_engine, migrate, nq_weekly_deliveries, users
from desk.notify_channels import ChannelRateLimited, ChannelStopped, ChannelUnknown, Telegram
from desk.nq_weekly_ledger import Ledger
from desk.nq_weekly_signal import DEFAULT_SETTINGS
from desk.privacy import (
    ISOLATED,
    PROTECTED,
    AttestedDestination,
    OwnerResources,
    PrivacyPolicyStore,
    ProcessPolicy,
)
from tests.test_favorites import OTHER, _app, _login

WEEK = date(2026, 11, 2)
CONFIRMED = datetime(2026, 11, 8, 23, tzinfo=UTC)
ENABLED = {**DEFAULT_SETTINGS, "enabled": True}


@pytest.fixture
def state(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user("owner", "synthetic password")
    ledger = Ledger(engine, ProcessPolicy.open(engine, False))
    owner = ledger.rule().owner
    rule = ledger.save(owner, ENABLED, CONFIRMED - timedelta(days=1))
    try:
        yield ledger, rule
    finally:
        engine.dispose()


def observed(offset, status):
    week = WEEK + timedelta(weeks=offset)
    confirms = CONFIRMED + timedelta(weeks=offset)
    return {"status": status, "week": {"start": week.isoformat(), "confirmed_at": confirms.isoformat()},
            "evidence": {"rsi": {"value": 26.0, "limit": 30.0}, "bollinger": {"value": 123.0}},
            "matched_conditions": ["bollinger"], "min_hits": 1}


def record(ledger, rule, offset, status, lag=timedelta(seconds=30)):
    observation = observed(offset, status)
    return ledger.record(rule, observation, alerts.message(observation) if status == "matched" else {},
                         CONFIRMED + timedelta(weeks=offset) + lag)


def test_concurrent_evaluation_revisions_and_gap_do_not_backfill(state):
    ledger, rule = state
    assert not record(ledger, rule, 0, "not_matched")
    with ThreadPoolExecutor(4) as executor:
        winners = list(executor.map(lambda _: record(ledger, rule, 1, "matched"), range(4)))
    assert winners.count(True) == 1
    assert {row["channel"] for row in ledger.latest(rule)} == {"telegram"}
    assert not record(ledger, rule, 1, "not_matched")  # revised old bar does not rearm
    assert not record(ledger, rule, 2, "unavailable")
    assert not record(ledger, rule, 3, "matched")
    assert not record(ledger, rule, 4, "not_matched")
    assert not record(ledger, rule, 5, "matched", timedelta(days=2))
    assert not record(ledger, rule, 5, "matched")
    assert not record(ledger, rule, 6, "not_matched")
    assert record(ledger, rule, 7, "matched")

    changed = CONFIRMED + timedelta(weeks=8)
    same = ledger.save(rule.owner, ENABLED, changed)
    assert same.revision == rule.revision
    replacement = ledger.save(rule.owner, {**ENABLED, "rsi_limit": 28}, changed)
    assert replacement.revision == rule.revision + 1
    assert not record(ledger, rule, 8, "matched")
    assert not record(ledger, replacement, 8, "matched")
    assert ledger.latest(replacement) == []


class FakeChannel:
    configured = True

    def __init__(self, outcomes=()):
        self.outcomes = iter(outcomes)
        self.sent = []

    def send_once(self, payload, *, allowed):
        assert allowed()
        self.sent.append(payload)
        error = next(self.outcomes, None)
        if error:
            raise error


def _service(ledger, monkeypatch, now, telegram, kakao=None):
    """``kakao``: a channel by the retired name, there only to prove nothing reaches it (#986)."""
    targets = {"telegram": telegram, "kakao": kakao}
    monkeypatch.setattr(notify, "channel", targets.get)

    def wants(name, kinds):
        assert name in targets and kinds == ("nq_alert", "tone_warning")
        return True

    store = SimpleNamespace(wants=wants)
    return alerts.Service(ledger, None, store, lambda: None, now=lambda: now[0])


def test_a_rate_limit_survives_service_restart(state, monkeypatch):
    ledger, rule = state
    assert not record(ledger, rule, 0, "not_matched")
    assert record(ledger, rule, 1, "matched")
    now = [CONFIRMED + timedelta(weeks=1, minutes=1)]
    telegram = FakeChannel([ChannelRateLimited(120)])
    svc = _service(ledger, monkeypatch, now, telegram)
    asyncio.run(svc.deliver(rule, "telegram"))
    assert {r["channel"]: r["status"] for r in ledger.latest(rule)} == {"telegram": "rate_limited"}

    restarted = _service(Ledger(ledger.engine, ledger.policy), monkeypatch, now, telegram)
    asyncio.run(restarted.deliver(rule, "telegram"))
    assert len(telegram.sent) == 1                     # not before retry_after
    now[0] += timedelta(minutes=2)
    asyncio.run(restarted.deliver(rule, "telegram"))
    assert len(telegram.sent) == 2
    assert all(row["status"] == "sent" for row in ledger.latest(rule))
    assert "26.00" in telegram.sent[0]["body"] and "123.00" in telegram.sent[0]["body"]
    assert telegram.sent[0]["url"] == "/#/charts?t=NQ%3DF&iv=1w"


def test_claimed_on_restart_becomes_unknown_without_replaying(state, monkeypatch):
    original, rule = state
    record(original, rule, 0, "not_matched")
    assert record(original, rule, 1, "matched")
    tick = CONFIRMED + timedelta(weeks=1, minutes=1)
    assert original.claim(rule, "telegram", tick)
    restarted = Ledger(original.engine, original.policy)
    now = [tick + timedelta(minutes=6)]
    telegram = FakeChannel()
    svc = _service(restarted, monkeypatch, now, telegram)
    asyncio.run(svc.deliver(rule, "telegram"))
    assert telegram.sent == []
    assert {r["channel"]: r["status"] for r in restarted.latest(rule)} == {"telegram": "unknown"}


def test_disabled_or_replaced_setting_stops_pending_delivery(state, monkeypatch):
    ledger, rule = state
    record(ledger, rule, 0, "not_matched")
    assert record(ledger, rule, 1, "matched")
    tick = CONFIRMED + timedelta(weeks=1, minutes=1)
    disabling = ledger.save(rule.owner, DEFAULT_SETTINGS, tick)
    telegram = FakeChannel()
    svc = _service(ledger, monkeypatch, [tick], telegram)
    asyncio.run(svc.deliver(rule, "telegram"))
    asyncio.run(svc.deliver(disabling, "telegram"))
    assert telegram.sent == []
    assert all(row["status"] == "superseded" for row in ledger.latest(rule))


def test_expiry_is_checked_again_after_the_claim(state, monkeypatch):
    ledger, rule = state
    record(ledger, rule, 0, "not_matched")
    assert record(ledger, rule, 1, "matched")
    now = [CONFIRMED + timedelta(weeks=1, hours=23, minutes=59)]
    telegram = FakeChannel()
    svc = _service(ledger, monkeypatch, now, telegram)
    claim = ledger.claim

    def claimed_just_before_expiry(*args):
        row = claim(*args)
        now[0] += timedelta(minutes=2)
        return row

    monkeypatch.setattr(ledger, "claim", claimed_just_before_expiry)
    asyncio.run(svc.deliver(rule, "telegram"))
    assert telegram.sent == []
    assert {r["channel"]: r["status"] for r in ledger.latest(rule)} == {"telegram": "expired"}


def test_single_post_respects_denial_and_does_not_retry_uncertain_provider_result():
    attempts = []

    def transport(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, json={"parameters": {"retry_after": 120}})
        if len(attempts) == 2:
            return httpx.Response(503)
        raise httpx.ReadTimeout("synthetic private detail")

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        telegram = Telegram("synthetic", "fake", client=client)
        with pytest.raises(ChannelStopped):
            telegram.send_once({}, allowed=lambda: False)
        assert attempts == []
        with pytest.raises(ChannelRateLimited) as limited:
            telegram.send_once({})
        assert len(attempts) == 1 and limited.value.retry_after == 120
        with pytest.raises(ChannelUnknown):
            telegram.send_once({})
        assert len(attempts) == 2
        with pytest.raises(ChannelUnknown) as timeout:
            telegram.send_once({})
        assert len(attempts) == 3 and "private detail" not in str(timeout.value)


def test_protected_scope_uses_only_current_owner_and_attested_destination(state, monkeypatch):
    old, legacy_rule = state
    engine = old.engine
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user("protected", "synthetic password")
    with engine.connect() as conn:
        user = conn.scalar(select(users.c.id).where(users.c.username == "protected"))
    owners = AccountOwnerStore(engine)
    binding = owners.bind_verified(verified_user_id=user, expected_vacancy=owners.binding())
    policy = PrivacyPolicyStore(engine)
    assert policy.transition(policy.current(), ISOLATED)
    assert policy.transition(policy.current(), PROTECTED)
    ledger = Ledger(engine, ProcessPolicy.open(engine, True))
    assert old.rule() is None
    assert ledger.rule().owner == user and ledger.rule().scope != legacy_rule.scope
    assert ledger.save(legacy_rule.owner, ENABLED, CONFIRMED) is None
    rule = ledger.save(user, ENABLED, CONFIRMED - timedelta(days=1))
    record(ledger, rule, 0, "not_matched")
    assert record(ledger, rule, 1, "matched")

    def unwanted(_name):
        pytest.fail("protected weekly alert consulted a legacy channel")

    monkeypatch.setattr(notify, "channel", unwanted)
    sent = []
    resource = OwnerResources(binding, destinations=(
        AttestedDestination("telegram", sent.append, frozenset(alerts.KINDS)),
    ))
    svc = alerts.Service(ledger, None, None, lambda: resource if resource.current(owners) else None,
                         now=lambda: CONFIRMED + timedelta(weeks=1, minutes=1))
    asyncio.run(svc.deliver(rule, "telegram"))
    assert len(sent) == 1
    assert {r["channel"]: r["status"] for r in ledger.latest(rule)} == {"telegram": "sent"}


def test_weekly_read_and_settings_use_the_existing_sessions_and_owner(db_url, monkeypatch):
    async def offline_weekly(_self):
        return None

    monkeypatch.setattr(alerts.Service, "weekly", offline_weekly)
    app = _app(db_url)
    with closing(TestClient(app, base_url="https://testserver")) as anonymous:
        assert anonymous.get(alerts.URL).status_code == 401
    with _login(app) as owner, closing(_login(app, OTHER)) as other:
        assert other.put(f"{alerts.URL}/settings", json=ENABLED).status_code == 403
        assert owner.put(f"{alerts.URL}/settings", json={**ENABLED, "rsi_limit": "x"}).status_code == 422
        save = owner.put(f"{alerts.URL}/settings", json=ENABLED)
        assert save.status_code == 200 and save.json()["revision"] == 1
        response = owner.get(alerts.URL)
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["can_edit"]
        assert response.json()["current"]["status"] == "unavailable"
        assert response.json()["channels"] == [{"channel": "telegram", "status": "unavailable",
                                                 "needs_reconnect": False}]
        public = other.get(alerts.URL).json()
        assert not public["can_edit"]
        assert public["channels"] == public["deliveries"] == []
        assert owner.get(alerts.URL, params={"weeks": 105}).status_code == 422
    assert roles.runs("collector", "nq_weekly") and roles.runs("all", "nq_weekly")
    assert not roles.runs("api", "nq_weekly") and not roles.runs("analyst", "nq_weekly")


def test_a_leftover_kakao_outbox_row_is_neither_listed_nor_sent(state, monkeypatch):
    """#986 KakaoTalk was dropped: the row it left for the same week stays as it was and the tick sends Telegram only."""
    ledger, rule = state
    record(ledger, rule, 0, "not_matched")
    assert record(ledger, rule, 1, "matched")
    now = [CONFIRMED + timedelta(weeks=1, minutes=1)]
    table = nq_weekly_deliveries
    with ledger.engine.begin() as conn:
        conn.execute(delete(table).where(table.c.channel == "kakao"))
        conn.execute(insert(table).values(
            scope=rule.scope, revision=rule.revision, week=WEEK + timedelta(weeks=1), channel="kakao", payload={},
            status="pending", attempts=0, updated_at=now[0], expires_at=now[0] + timedelta(hours=23)))
    telegram, kakao = FakeChannel(), FakeChannel()
    svc = _service(ledger, monkeypatch, now, telegram, kakao)

    async def offline(_self):
        return None

    monkeypatch.setattr(alerts.Service, "weekly", offline)
    assert asyncio.run(svc.step()) == alerts.TICK_SEC
    assert len(telegram.sent) == 1 and kakao.sent == []
    assert [(r["channel"], r["status"]) for r in ledger.latest(rule)] == [("telegram", "sent")]
    assert [c["channel"] for c in svc.channels(rule)] == ["telegram"]
    with ledger.engine.connect() as conn:
        assert conn.scalar(select(table.c.status).where(table.c.channel == "kakao")) == "pending"
