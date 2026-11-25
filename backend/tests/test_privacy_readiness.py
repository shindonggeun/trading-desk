"""Exercise the coordinated privacy policy with synthetic resources and roles."""

import asyncio
import time
from datetime import timedelta
from secrets import randbits
from types import SimpleNamespace

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from desk import market_bus, run_queue
from desk.account_owner import AccountOwnerStore
from desk.api import create_app
from desk.auth import Auth
from desk.broker.base import Broker, Holding
from desk.chat import ChatStore
from desk.db import account_owner_binding, alembic_config, make_engine, migrate, now, runs, users
from desk.privacy import (
    ISOLATED,
    LEGACY,
    PROTECTED,
    AttestedDestination,
    OwnerResources,
    PolicyChanged,
    PolicyEpoch,
    PrivacyPolicyStore,
)
from desk.runner import make_fake_runner
from desk.settings import Settings
from desk.store import Store
from tests.test_api import PASSWORD, _wait_done


class SyntheticBroker(Broker):
    name = "synthetic"

    def __init__(self):
        self.holdings_count = 0
        self.before_holdings_return = None

    async def holdings(self):
        self.holdings_count += 1
        if self.before_holdings_return is not None:
            self.before_holdings_return()
        return [Holding("SYNTHETIC", "SYNTHETIC", "Synthetic holding", "KR", "KRW", 3, 100, 120)]

    async def list_stocks(self, market):
        return []

    async def usd_krw(self):
        return 1300


@pytest.fixture
def desk_data(db_url, monkeypatch):
    monkeypatch.setattr(run_queue, "LOCK_KEY", randbits(63))
    migrate(db_url)
    engine = make_engine(db_url)
    try:
        auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
        auth.create_user("synthetic-owner", PASSWORD)
        auth.create_user("synthetic-peer", PASSWORD)
        with engine.connect() as conn:
            owner_id = conn.scalar(select(users.c.id).where(users.c.username == "synthetic-owner"))
        owners = AccountOwnerStore(engine)
        bound = owners.bind_verified(verified_user_id=owner_id, expected_vacancy=owners.binding())
        assert bound is not None
        yield engine, owners, bound
    finally:
        engine.dispose()


def _app(db_url, role="api", *, protected=False, resources=None, book=None, chatter=None):
    return create_app(
        Settings(database_url=db_url, role=role, fake_runs=True, fake_delay=0),
        runner=make_fake_runner(0), protect_run_reads=protected, owner_resources=resources, book=book, chatter=chatter,
    )


def _client(app):
    return TestClient(app, base_url="https://testserver")


def _login(client, username="synthetic-owner"):
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 204


def _protect(policy):
    assert policy.current() == PolicyEpoch(LEGACY, 0)
    isolated = policy.transition(policy.current(), ISOLATED)
    assert isolated == PolicyEpoch(ISOLATED, 1)
    assert policy.transition(PolicyEpoch(LEGACY, 0), ISOLATED) is None
    assert policy.transition(isolated, LEGACY) is None
    assert policy.transition(isolated, PROTECTED) == PolicyEpoch(PROTECTED, 2)


def test_protected_api_and_analyst_use_one_fence_and_exclude_legacy_derivatives(db_url, desk_data):
    engine, _, bound = desk_data
    policy = PrivacyPolicyStore(engine)
    history = Store(engine)
    broker = SyntheticBroker()
    resources = OwnerResources(bound, broker=broker)
    with _client(_app(db_url)) as legacy:
        _login(legacy)
        old_queued = legacy.post("/api/runs", json={"ticker": "OLDPENDING"}).json()["id"]
        old_shared = legacy.post("/api/runs", json={"ticker": "OLDPUBLIC"}).json()["id"]
        old_private = history.create_run(
            "OLDPRIVATE", "2026-11-20", {"use_portfolio": True}, guarded_writer=True,
            authenticated_submitter_id=bound.user_id, owner_generation=bound.generation,
            first_event={"type": "status", "agent": "system", "content": "queued"},
        )
        history.mark_done(old_shared, {"summary": "old derived data"}, None,
                          executed_use_portfolio=False, source_visibility=())
        history.mark_done(old_private, {"summary": "old account data"}, None, executed_use_portfolio=True)
        assert legacy.get(f"/api/runs/{old_shared}").json()["plan"]["summary"] == "old derived data"

        with pytest.raises(PolicyChanged):
            _app(db_url, protected=True)
        _protect(policy)
        assert legacy.get(f"/api/runs/{old_shared}").status_code == 503
        assert history.get_run(old_queued)["status"] == "queued"
        with pytest.raises(PolicyChanged):
            _app(db_url, "analyst")
        with _client(_app(db_url, protected=True, resources=resources)) as api:
            _login(api)
            assert api.get(f"/api/runs/{old_shared}").json() == {"id": old_shared, "status": "completed"}
            assert api.get(f"/api/runs/{old_shared}/stream").status_code == 404
            assert api.get(f"/api/runs/{old_private}").status_code == 404
            assert api.get(f"/api/runs/{old_private}/events").status_code == 404
            assert api.get(f"/api/runs/{old_shared}/explain").status_code == 404
            assert history.list_runs(public_only=True) == []
            assert api.app.state.paper._runs() == []

            new_private = api.post("/api/runs", json={"ticker": "ACCOUNT", "use_portfolio": True}).json()["id"]
            posted = api.post("/api/runs", json={"ticker": "PUBLIC"}).json()
            new_public = posted["id"]
            assert posted == {"id": new_public, "status": "progress"}
            with _client(_app(db_url, "analyst", protected=True, resources=resources)) as analyst:
                assert _wait_done(api, new_private)["status"] == "done"
                assert _wait_done(api, new_public)["status"] == "done"
                replay = api.get(f"/api/runs/{new_private}/stream")
                assert replay.status_code == 200 and '"content": "done"' in replay.text
                assert broker.holdings_count == 1
                assert history.get_run(old_queued)["status"] == "queued"
                visible = api.get(f"/api/runs/{new_public}").json()
                assert "_run_visibility_writer" not in visible["options"]
                assert "_run_visibility_worker" not in visible["options"]
                assert {row["id"] for row in history.list_runs(public_only=True)} == {new_public}

                assert policy.transition(PolicyEpoch(PROTECTED, 2), ISOLATED) == PolicyEpoch(ISOLATED, 3)
                assert api.get(f"/api/runs/{new_public}/events").status_code == 503
                assert analyst.get("/api/health").status_code == 503
                with pytest.raises(PolicyChanged):
                    analyst.app.state.manager.store.mark_failed(new_public, "synthetic")
                assert policy.transition(PolicyEpoch(ISOLATED, 3), PROTECTED) == PolicyEpoch(PROTECTED, 4)
                assert api.get(f"/api/runs/{new_public}").status_code == 503

    with pytest.raises(PolicyChanged):
        _app(db_url)
    with _client(_app(db_url, protected=True)) as restarted:
        _login(restarted)
        assert restarted.get(f"/api/runs/{new_public}").json()["status"] == "done"
        assert restarted.get("/api/portfolio").status_code == 403


def test_attested_broker_and_destination_do_not_follow_rebinding(db_url, desk_data):
    engine, owners, bound = desk_data
    _protect(PrivacyPolicyStore(engine))
    approved = SyntheticBroker()
    notices = []
    resources = OwnerResources(bound, broker=approved, destinations=(AttestedDestination("telegram", notices.append),))
    with _client(_app(db_url, protected=True, resources=resources)) as api:
        _login(api, "synthetic-peer")
        assert api.get("/api/portfolio").status_code == 403
        assert api.get("/api/portfolio/stream").status_code == 403
        assert api.post("/api/notify/channels/telegram/test").status_code == 403
        assert approved.holdings_count == 0 and notices == []
        assert api.post("/api/auth/logout").status_code == 204
        _login(api)
        assert api.get("/api/portfolio").json()["holdings"][0]["symbol"] == "SYNTHETIC"
        assert approved.holdings_count == 1
        channels = api.get("/api/notify/channels").json()
        assert not channels["can_edit"]
        assert {row["name"]: row["configured"] for row in channels["channels"]} == {"telegram": True}
        assert api.put("/api/notify/channels/telegram", json={"enabled": False}).status_code == 403
        assert api.post("/api/notify/kakao/connect").status_code == 404           # #986 retired
        assert api.post("/api/notify/channels/telegram/test").json()["ok"] is True
        assert len(notices) == 1

        with engine.begin() as conn:
            conn.execute(update(account_owner_binding).values(user_id=None))
        replacement = owners.bind_verified(verified_user_id=bound.user_id, expected_vacancy=owners.binding())
        assert replacement is not None and replacement != bound
        assert api.get("/api/portfolio").status_code == 403
        assert api.post("/api/notify/channels/telegram/test").status_code == 403
        assert all(not row["configured"] for row in api.get("/api/notify/channels").json()["channels"])
        assert approved.holdings_count == 1 and len(notices) == 1


def test_isolation_blocks_a_portfolio_result_in_flight(db_url, desk_data):
    engine, _, bound = desk_data
    policy = PrivacyPolicyStore(engine)
    _protect(policy)
    broker = SyntheticBroker()

    def isolate():
        assert policy.transition(PolicyEpoch(PROTECTED, 2), ISOLATED) is not None

    broker.before_holdings_return = isolate
    with _client(_app(db_url, protected=True, resources=OwnerResources(bound, broker=broker))) as api:
        _login(api)
        response = api.get("/api/portfolio")
        assert response.status_code == 503
        assert response.headers["cache-control"] == "private, no-store"
        assert "SYNTHETIC" not in response.text


def test_isolation_closes_a_legacy_portfolio_stream_before_its_snapshot(db_url, desk_data):
    engine, _, _ = desk_data
    policy = PrivacyPolicyStore(engine)
    seen = []

    async def snapshot():
        seen.append(True)
        assert policy.transition(PolicyEpoch(LEGACY, 0), ISOLATED) is not None
        return {"holdings": [{"symbol": "isolated-synthetic"}]}

    with _client(_app(db_url, book=SimpleNamespace(snapshot=snapshot))) as api:
        _login(api)
        response = api.get("/api/portfolio/stream")
        assert seen == [True]
        assert response.status_code in (200, 503)
        assert "isolated-synthetic" not in response.text
        assert api.get("/api/portfolio/stream").status_code == 503


def test_isolation_stops_pending_legacy_market_frames(db_url, desk_data, monkeypatch):
    engine, _, _ = desk_data
    policy = PrivacyPolicyStore(engine)
    bus = market_bus.BUS
    original = bus.snapshot

    def snapshot(subscription):
        bus.publish("flow", "SYNTHETIC", {"text": "isolated-synthetic-frame"}, "desk")
        selected = original(subscription)
        assert any("isolated-synthetic-frame" in str(frame) for frame in selected)
        assert policy.transition(PolicyEpoch(LEGACY, 0), ISOLATED) is not None
        return selected

    with _client(_app(db_url)) as api:
        _login(api)
        monkeypatch.setattr(bus, "snapshot", snapshot)
        response = api.get("/api/stream/market?channels=flow")
        assert response.status_code in (200, 503)
        assert "isolated-synthetic-frame" not in response.text
        assert policy.current().mode == ISOLATED


def test_isolation_closes_a_legacy_chat_without_persisting_the_answer(db_url, desk_data):
    engine, _, bound = desk_data
    policy = PrivacyPolicyStore(engine)

    async def chatter(_messages, _tools, emit, *, model=None):
        assert policy.transition(PolicyEpoch(LEGACY, 0), ISOLATED) is not None
        emit({"type": "token", "text": "isolated-synthetic-chat"})
        return "isolated-synthetic-chat", []

    with _client(_app(db_url, chatter=chatter)) as api:
        _login(api)
        thread = api.post("/api/chat/threads", json={}).json()["id"]
        response = api.post(f"/api/chat/threads/{thread}/messages", json={"text": "Synthetic request"})
        assert response.status_code in (200, 503)
        assert "isolated-synthetic-chat" not in response.text
        assert [item["role"] for item in ChatStore(engine).messages(bound.user_id, thread)] == ["user"]


def test_migration_keeps_the_fence_after_the_first_transition(db_url, desk_data):
    engine, _, _ = desk_data
    cfg = alembic_config(db_url)
    with engine.connect() as conn:
        assert conn.scalar(select(runs.c.id)) is None
    command.downgrade(cfg, "0029")
    command.upgrade(cfg, "0030")
    policy = PrivacyPolicyStore(engine)
    assert policy.current() == PolicyEpoch(LEGACY, 0)
    with engine.begin() as conn:
        conn.execute(runs.insert().values(id="old-history", ticker="OLD", trade_date="2026-11-20",
                                          status="done", created_at=now(), options={}, is_shareable=True))
    _protect(policy)
    with pytest.raises(RuntimeError, match="privacy policy history must be retained"):
        command.downgrade(cfg, "0029")
    assert policy.current() == PolicyEpoch(PROTECTED, 2)
    assert Store(engine).get_run("old-history") is not None


def _settled(history, run_id, timeout=30.0):
    """The stored run once done or failed (a stale account run is no API reader's to poll)."""
    deadline = time.monotonic() + timeout
    while (run := history.get_run(run_id))["status"] not in ("done", "failed"):
        assert time.monotonic() < deadline, f"run {run_id} still {run['status']}"
        time.sleep(0.05)
    return run


def test_a_separate_analyst_refuses_an_account_run_queued_under_a_replaced_binding(db_url, desk_data):
    """#775 the binding a run was queued under is checked where it runs: an analyst still holding the old
    binding's broker reads nothing for it, and the rebound owner does not inherit it."""
    engine, owners, bound = desk_data
    _protect(PrivacyPolicyStore(engine))
    broker = SyntheticBroker()
    resources = OwnerResources(bound, broker=broker)
    history = Store(engine)
    with _client(_app(db_url, protected=True, resources=resources)) as api:
        _login(api)
        stale = api.post("/api/runs", json={"ticker": "ACCOUNT", "use_portfolio": True}).json()["id"]
        assert history.get_run(stale)["options"]["use_portfolio"] is True
        with engine.begin() as conn:
            conn.execute(update(account_owner_binding).values(user_id=None))
        replacement = owners.bind_verified(verified_user_id=bound.user_id, expected_vacancy=owners.binding())
        assert replacement is not None and replacement.generation != bound.generation
        public = api.post("/api/runs", json={"ticker": "PUBLIC"}).json()["id"]
        with _client(_app(db_url, "analyst", protected=True, resources=resources)):
            refused, completed = _settled(history, stale), _settled(history, public)
        assert (refused["status"], refused["error"]) == ("failed", "RuntimeError: account authorization changed")
        assert completed["status"] == "done"
        assert broker.holdings_count == 0
        for path in (f"/api/runs/{stale}", f"/api/runs/{stale}/events", f"/api/runs/{stale}/stream"):
            assert api.get(path).status_code == 404, path
        listed = {item["id"] for item in api.get("/api/runs").json()["items"]}
        assert stale not in listed and public in listed


def test_an_analyst_from_an_older_policy_epoch_claims_nothing(db_url, desk_data):
    """An analyst started before an isolate-and-protect cycle claims nothing, by its loop or by hand; one
    started at the current epoch runs the same queue."""
    engine, _, bound = desk_data
    policy = PrivacyPolicyStore(engine)
    _protect(policy)
    broker = SyntheticBroker()
    resources = OwnerResources(bound, broker=broker)
    history = Store(engine)
    with _client(_app(db_url, "analyst", protected=True, resources=resources)) as analyst:
        assert policy.transition(PolicyEpoch(PROTECTED, 2), ISOLATED) == PolicyEpoch(ISOLATED, 3)
        assert policy.transition(PolicyEpoch(ISOLATED, 3), PROTECTED) == PolicyEpoch(PROTECTED, 4)
        with _client(_app(db_url, protected=True, resources=resources)) as api:
            _login(api)
            public = api.post("/api/runs", json={"ticker": "PUBLIC"}).json()["id"]
            account = api.post("/api/runs", json={"ticker": "ACCOUNT", "use_portfolio": True}).json()["id"]
        manager = analyst.app.state.manager
        with pytest.raises(PolicyChanged):
            manager.store.claim_queued()
        assert asyncio.run(run_queue.RunQueue(manager.store, manager, run_queue.Holder(engine)).take()) == 0
        time.sleep(run_queue.POLL_SEC * 1.5)          # its own loop polled meanwhile
        assert [history.run_status(run_id) for run_id in (public, account)] == ["queued", "queued"]
        assert broker.holdings_count == 0
        assert analyst.get("/api/health").status_code == 503
    with _client(_app(db_url, "analyst", protected=True, resources=resources)):
        assert _settled(history, public)["status"] == "done"
        assert _settled(history, account)["status"] == "done"
    assert broker.holdings_count == 1
