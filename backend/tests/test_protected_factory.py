"""The deployed factory reads its privacy mode and account approval from the database."""

import asyncio
import json
from datetime import timedelta
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from desk import api, privacy_admin
from desk.account_owner import AccountOwnerStore
from desk.auth import Auth
from desk.broker.toss import TossBroker, TossError
from desk.db import (
    account_owner_binding,
    broker_tokens,
    make_engine,
    migrate,
    owner_toss_attestations,
    privacy_policy,
    users,
)
from desk.owner_attestation import TossAttestationStore, TossSource
from desk.privacy import ISOLATED, PROTECTED, PolicyChanged, PrivacyPolicyStore
from desk.token_store import Issued, TokenStore
from tests.test_api import PASSWORD

SOURCE = TossSource("synthetic-client", "synthetic-secret", "000123")


@pytest.fixture
def desk(db_url, monkeypatch):
    for name, value in {
        "DESK_DATABASE_URL": db_url,
        "DESK_ROLE": "api",
        "DESK_FAKE_RUNS": "true",
        "DESK_BROKER": "toss",
        "TOSS_CLIENT_ID": SOURCE.client_id,
        "TOSS_CLIENT_SECRET": SOURCE.client_secret,
        "TOSS_ACCOUNT_SEQ": SOURCE.account_seq,
        "DESK_TELEGRAM_BOT_TOKEN": "synthetic-notification-token",
        "DESK_TELEGRAM_CHAT_ID": "synthetic-destination",
        "DESK_KIS_APP_KEY": "",
        "DESK_KIS_APP_SECRET": "",
    }.items():
        monkeypatch.setenv(name, value)
    migrate(db_url)
    engine = make_engine(db_url)
    try:
        auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
        auth.create_user("synthetic-peer", PASSWORD)
        auth.create_user("synthetic-owner", PASSWORD)
        with engine.connect() as conn:
            owner_id = conn.scalar(select(users.c.id).where(users.c.username == "synthetic-owner"))
        owners = AccountOwnerStore(engine)
        binding = owners.bind_verified(verified_user_id=owner_id, expected_vacancy=owners.binding())
        assert binding is not None
        yield engine, binding
    finally:
        engine.dispose()


def _advance(engine, mode):
    store = PrivacyPolicyStore(engine)
    new = store.transition(store.current(), mode)
    assert new is not None
    return new


def _protect(engine, binding, *, approve=True):
    isolation = _advance(engine, ISOLATED)
    if approve:
        assert TossAttestationStore(engine).approve(expected=isolation, binding=binding, source=SOURCE)
    return _advance(engine, PROTECTED)


def _login(client, user):
    assert client.post("/api/auth/login", json={"username": user, "password": PASSWORD}).status_code == 204


def _deny_before_broker(monkeypatch):
    def unexpected(*_args):
        pytest.fail("the factory must reject the policy before creating a broker")

    disposed = []

    def track_engine(url):
        engine = make_engine(url)
        event.listen(engine, "engine_disposed", disposed.append)
        return engine

    monkeypatch.setattr(api, "make_broker", unexpected)
    monkeypatch.setattr(api, "make_engine", track_engine)
    with pytest.raises(PolicyChanged, match=r"^process privacy (mode does not match the database|approval unavailable)$"):
        api.create_app()
    assert len(disposed) == 1


@pytest.mark.parametrize("reason", ["missing-policy", "invalid-policy", "missing-approval", "stale-approval",
                                   "bad-approval", "changed-binding", "client", "secret", "account", "unpinned", "mock"])
def test_default_factory_denies_unverified_policy_or_source(desk, monkeypatch, reason):
    engine, binding = desk
    if reason == "missing-policy":
        with engine.begin() as conn:
            conn.execute(privacy_policy.delete())
    elif reason == "invalid-policy":
        with engine.begin() as conn:
            conn.execute(privacy_policy.update().values(mode=PROTECTED, epoch=1))
    else:
        _protect(engine, binding, approve=reason != "missing-approval")
        if reason == "stale-approval":
            _advance(engine, ISOLATED)
            _advance(engine, PROTECTED)
        elif reason == "bad-approval":
            with engine.begin() as conn:
                conn.execute(owner_toss_attestations.update().values(approval_fingerprint=b"z" * 32))
        elif reason == "changed-binding":
            with engine.begin() as conn:
                conn.execute(account_owner_binding.update().values(user_id=None))
            owners = AccountOwnerStore(engine)
            assert owners.bind_verified(verified_user_id=binding.user_id, expected_vacancy=owners.binding()) != binding
        elif reason in {"client", "secret", "account", "unpinned", "mock"}:
            name, value = {
                "client": ("TOSS_CLIENT_ID", "different-synthetic-client"),
                "secret": ("TOSS_CLIENT_SECRET", "different-synthetic-secret"),
                "account": ("TOSS_ACCOUNT_SEQ", SOURCE.account_seq.lstrip("0")),
                "unpinned": ("TOSS_ACCOUNT_SEQ", ""),
                "mock": ("DESK_BROKER", "mock"),
            }[reason]
            monkeypatch.setenv(name, value)
    _deny_before_broker(monkeypatch)


@pytest.fixture
def toss_get(monkeypatch):
    calls = []

    async def get(self, path, headers=None, params=None):
        calls.append((self, path, headers))
        if path == "/api/v1/holdings":
            return {"items": [{"symbol": "SYNTHETIC", "marketCountry": "US", "currency": "USD",
                               "quantity": "3", "averagePurchasePrice": "100", "lastPrice": "120"}]}
        if path == "/api/v1/prices":
            return [{"symbol": "SYNTHETIC", "lastPrice": "120", "currency": "USD"}]
        assert path == "/api/v1/stocks/all"
        return []

    async def fx(_self):
        return 1300

    monkeypatch.setattr(TossBroker, "_get", get)
    monkeypatch.setattr(TossBroker, "usd_krw", fx)
    return calls


@pytest.mark.parametrize("role", ["all", "api", "collector", "analyst"])
def test_default_roles_share_protected_source_and_fence_rollback(desk, monkeypatch, toss_get, capsys, role):
    engine, binding = desk
    protected = _protect(engine, binding)
    monkeypatch.setenv("DESK_ROLE", role)
    kept = Event()
    issuers = []
    keep_token = TossBroker.keep_token

    async def issue(broker):
        issuers.append(broker)
        return Issued("synthetic-token", 3600)

    async def keep(broker, ahead):
        await keep_token(broker, ahead)
        kept.set()

    monkeypatch.setattr(TossBroker, "_issue", issue)
    monkeypatch.setattr(TossBroker, "keep_token", keep)
    with TestClient(api.create_app(), base_url="https://testserver") as client:
        if role in {"all", "collector"}:
            assert kept.wait(5)
        _login(client, "synthetic-peer")
        assert client.get("/api/portfolio").status_code == 403
        assert all(path != "/api/v1/holdings" for _, path, _ in toss_get)
        _login(client, "synthetic-owner")
        response = client.get("/api/portfolio")
        assert response.status_code == 200
        assert response.json()["holdings"][0]["symbol"] == "SYNTHETIC"
        [(broker, _, headers)] = [call for call in toss_get if call[1] == "/api/v1/holdings"]
        assert headers == {"X-Tossinvest-Account": SOURCE.account_seq}
        assert all(source is broker for source, _, _ in toss_get)
        if role in {"all", "collector"}:
            assert issuers == [broker]
            with engine.connect() as conn:
                row = conn.execute(select(broker_tokens).where(broker_tokens.c.name == "toss")).mappings().one()
            assert broker._slot._open(row)[0] == "synthetic-token"
            assert TokenStore(engine, role="api").slot("toss", SOURCE.client_secret)._open(row) is None
        assert client.get("/api/review").status_code == 403
        channels = client.get("/api/notify/channels").json()
        assert not channels["can_edit"] and all(not ch["configured"] for ch in channels["channels"])
        assert client.post("/api/notify/channels/telegram/test").status_code == 403
        assert privacy_admin.main(["rollback", "--epoch", str(protected.epoch), "--confirm"]) == 0
        assert json.loads(capsys.readouterr().out) == {"mode": ISOLATED, "epoch": protected.epoch + 1}
        assert client.get("/api/health").status_code == client.get("/api/portfolio").status_code == 503
    _deny_before_broker(monkeypatch)


def test_default_legacy_keeps_account_and_notification_configuration(desk, toss_get):
    with TestClient(api.create_app(), base_url="https://testserver") as client:
        _login(client, "synthetic-peer")
        assert client.get("/api/portfolio").json()["holdings"][0]["symbol"] == "SYNTHETIC"
        channels = client.get("/api/notify/channels").json()
        assert channels["can_edit"] and any(ch["id"] == "telegram" and ch["configured"] for ch in channels["channels"])


def test_protected_readers_use_the_collectors_toss_token(desk, monkeypatch, toss_get):
    engine, binding = desk
    _protect(engine, binding)
    kept = Event()
    issuers, holdings_reads = [], []
    mock_get, keep_token = TossBroker._get, TossBroker.keep_token

    async def issue(broker):
        issuers.append(broker)
        return Issued("collector-synthetic-token", 3600)

    async def keep(broker, ahead):
        await keep_token(broker, ahead)
        kept.set()

    async def get(broker, path, headers=None, params=None):
        if path == "/api/v1/holdings":
            holdings_reads.append((broker, await broker._access_token()))
        return await mock_get(broker, path, headers, params)

    monkeypatch.setattr(TossBroker, "_issue", issue)
    monkeypatch.setattr(TossBroker, "keep_token", keep)
    monkeypatch.setattr(TossBroker, "_get", get)
    monkeypatch.setenv("DESK_ROLE", "collector")
    with TestClient(api.create_app(), base_url="https://testserver"):
        assert kept.wait(5)

    for role in ("api", "analyst"):
        monkeypatch.setenv("DESK_ROLE", role)
        with TestClient(api.create_app(), base_url="https://testserver") as client:
            _login(client, "synthetic-owner")
            assert client.get("/api/portfolio").json()["holdings"][0]["symbol"] == "SYNTHETIC"

    [collector] = issuers
    assert len(holdings_reads) == 2
    assert all(reader is not collector and token == "collector-synthetic-token" for reader, token in holdings_reads)


def test_toss_seal_override_preserves_legacy_and_kis_slots(desk):
    engine, _ = desk
    legacy = TokenStore(engine, role="all")
    protected = TokenStore(engine, role="api", toss_seal_material="synthetic-protected-seal")
    assert "synthetic-protected-seal" not in repr(protected)

    async def issue():
        return Issued("synthetic-existing-token", 3600)

    for name in ("toss", "kis_rest", "kis_approval"):
        original = legacy.slot(name, SOURCE.client_secret)
        assert asyncio.run(original.get(issue))[0] == "synthetic-existing-token"
        with engine.connect() as conn:
            row = conn.execute(select(broker_tokens).where(broker_tokens.c.name == name)).mappings().one()
        current = protected.slot(name, SOURCE.client_secret)
        if name == "toss":
            assert current._open(row) is None
            assert original._open(row)[0] == "synthetic-existing-token"
        else:
            assert asyncio.run(current.get(issue))[0] == "synthetic-existing-token"


@pytest.mark.parametrize("change", ["isolation", "owner", "issuer"])
def test_issuer_does_not_publish_when_approval_changes_during_issue(desk, monkeypatch, toss_get, change):
    engine, binding = desk
    _protect(engine, binding)
    monkeypatch.setenv("DESK_ROLE", "collector")
    entered, release, finished = Event(), Event(), Event()
    issuer_locks, brokers = [], []
    acquire_lock = api.BrokerIssuerLock.acquire
    keep_token = TossBroker.keep_token

    def acquire(issuer):
        issuer_locks.append(issuer)
        return acquire_lock(issuer)

    async def issue(broker):
        brokers.append(broker)
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        return Issued("synthetic-late-token", 3600)

    async def keep(broker, ahead):
        try:
            return await keep_token(broker, ahead)
        finally:
            finished.set()

    monkeypatch.setattr(api.BrokerIssuerLock, "acquire", acquire)
    monkeypatch.setattr(TossBroker, "_issue", issue)
    monkeypatch.setattr(TossBroker, "keep_token", keep)
    with TestClient(api.create_app(), base_url="https://testserver"):
        try:
            assert entered.wait(5)
            if change == "isolation":
                _advance(engine, ISOLATED)
            elif change == "owner":
                with engine.begin() as conn:
                    conn.execute(account_owner_binding.update().values(user_id=None))
            else:
                [issuer] = issuer_locks
                issuer.release()
        finally:
            release.set()
        assert finished.wait(5)
        with engine.connect() as conn:
            row = conn.execute(select(broker_tokens).where(broker_tokens.c.name == "toss")).one()
        assert row.asked_at is not None and row.sealed is None and row.lease_owner is None
        assert brokers[0]._token is None


PRIVATE_MARKER = "synthetic-private-provider-error"


@pytest.mark.parametrize(("protected", "failure"), [(True, "status"), (False, "status"), (True, "shape")])
def test_toss_provider_errors_in_facade_use_the_factory_policy(desk, monkeypatch, caplog, protected, failure):
    engine, binding = desk
    if protected:
        _protect(engine, binding)
    monkeypatch.setenv("DESK_ROLE", "all")
    refreshed = Event()
    init = TossBroker.__init__
    refresh = api.Universe.refresh_if_stale

    def respond(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "synthetic-token", "expires_in": 3600})
        if request.url.path == "/api/v1/exchange-rate":
            return httpx.Response(200, json={"result": {"midRate": "1300"}})
        if failure == "shape":
            if request.url.path == "/api/v1/holdings":
                items = [{"symbol": "SYNTHETIC", "currency": {"reason": PRIVATE_MARKER}}]
                return httpx.Response(200, json={"result": {"items": items}})
            return httpx.Response(200, json={"result": [{"symbol": "SYNTHETIC", "name": {"reason": PRIVATE_MARKER}}]})
        return httpx.Response(403, json={"error": {"code": PRIVATE_MARKER, "message": PRIVATE_MARKER}})

    def bind_transport(broker, *args, **kwargs):
        client = httpx.AsyncClient(base_url="https://toss.test", transport=httpx.MockTransport(respond))
        init(broker, *args, http=client, **kwargs)

    async def observe_refresh(universe, broker):
        try:
            await refresh(universe, broker)
        finally:
            refreshed.set()

    async def one_snapshot(scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/portfolio/stream":
            return await app(scope, receive, send)
        sent = asyncio.Event()

        async def receive_until_snapshot():
            await asyncio.wait_for(sent.wait(), 5)
            return {"type": "http.disconnect"}

        async def send_snapshot(message):
            await send(message)
            if message.get("body", b"").startswith(b"data: "):
                sent.set()

        await app(scope, receive_until_snapshot, send_snapshot)

    monkeypatch.setattr(TossBroker, "__init__", bind_transport)
    monkeypatch.setattr(api.Universe, "refresh_if_stale", observe_refresh)
    app = api.create_app()
    with TestClient(one_snapshot, base_url="https://testserver") as client:
        assert refreshed.wait(5)
        _login(client, "synthetic-owner" if protected else "synthetic-peer")
        snapshot = client.get("/api/portfolio")
        stream = client.get("/api/portfolio/stream")
        _login(client, "synthetic-peer")
        public = client.get("/api/instruments/status")
        assert snapshot.status_code == stream.status_code == public.status_code == 200
        assert "holdings:" in snapshot.text and "data: " in stream.text
        frame = json.loads(next(line[6:] for line in stream.text.splitlines() if line.startswith("data: ")))
        assert frame["error"] == snapshot.json()["error"]
        assert ("403" if failure == "status" else "malformed") in public.json()["error"]
        for surface in (snapshot.text, stream.text, public.text, caplog.text):
            assert (PRIVATE_MARKER in surface) is not protected


@pytest.mark.parametrize("failure", ["token-status", "token-parse", "transport", "bad-json", "candles", "calendar",
                                     "malformed-list"])
def test_protected_toss_redacts_provider_status_and_parse_errors(caplog, failure):
    def respond(request):
        if request.url.path == "/oauth2/token":
            if failure == "token-status":
                return httpx.Response(401, json={"error": {"code": PRIVATE_MARKER}})
            expires = PRIVATE_MARKER if failure == "token-parse" else 3600
            return httpx.Response(200, json={"access_token": "synthetic-token", "expires_in": expires})
        if failure == "transport":
            raise httpx.ReadError(PRIVATE_MARKER, request=request)
        if failure == "bad-json":
            return httpx.Response(200, text=PRIVATE_MARKER)
        if failure == "candles":
            return httpx.Response(200, json={"result": [{"timestamp": PRIVATE_MARKER, "openPrice": 1,
                                                       "highPrice": 1, "lowPrice": 1, "closePrice": 1}]})
        if failure == "calendar":
            return httpx.Response(200, json={"result": {"today": {"date": PRIVATE_MARKER}}})
        return httpx.Response(200, json={"result": [{"symbol": "SYNTHETIC", "name": {"reason": PRIVATE_MARKER}}]})

    async def exercise():
        async with httpx.AsyncClient(base_url="https://toss.test", transport=httpx.MockTransport(respond)) as http:
            broker = TossBroker(SOURCE.client_id, SOURCE.client_secret, SOURCE.account_seq,
                                http=http, redact_errors=True)
            if failure == "token-status":
                legacy = TossBroker(SOURCE.client_id, SOURCE.client_secret, SOURCE.account_seq, http=http)
                with pytest.raises(TossError, match=rf"401 {PRIVATE_MARKER}"):
                    await legacy._issue()
            if failure == "calendar":
                assert await broker.market_calendar("US") == []
            else:
                with pytest.raises(TossError) as caught:
                    if failure.startswith("token"):
                        await broker._issue()
                    elif failure == "candles":
                        await broker.candles("SYNTHETIC", "day", 1)
                    elif failure == "malformed-list":
                        await broker.list_stocks("NASDAQ")
                    else:
                        await broker.holdings()
                assert PRIVATE_MARKER not in str(caught.value)
                assert caught.value.__cause__ is None
                assert caught.value.__context__ is None or caught.value.__suppress_context__

    asyncio.run(exercise())
    assert PRIVATE_MARKER not in caplog.text


def test_protected_toss_renews_on_401_retries_on_429_and_keeps_403(monkeypatch):
    tokens, requests, delays = [], [], []
    answers = iter((401, 429, 403))

    def respond(request):
        if request.url.path == "/oauth2/token":
            token = f"synthetic-token-{len(tokens)}"
            tokens.append(token)
            return httpx.Response(200, json={"access_token": token})
        requests.append(request.headers["Authorization"])
        return httpx.Response(next(answers), text=PRIVATE_MARKER, headers={"Retry-After": "1"})

    async def wait(delay):
        delays.append(delay)

    async def exercise():
        async with httpx.AsyncClient(base_url="https://toss.test", transport=httpx.MockTransport(respond)) as http:
            broker = TossBroker(SOURCE.client_id, SOURCE.client_secret, SOURCE.account_seq,
                                http=http, redact_errors=True)
            with pytest.raises(TossError, match=r"^Toss GET request failed 403$"):
                await broker.holdings()

    monkeypatch.setattr(asyncio, "sleep", wait)
    asyncio.run(exercise())
    assert len(tokens) == 2 and requests == [f"Bearer {tokens[0]}", f"Bearer {tokens[1]}", f"Bearer {tokens[1]}"]
    assert delays == [1]
