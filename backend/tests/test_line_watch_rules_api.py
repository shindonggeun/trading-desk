"""The authenticated settings keep legacy data and owner-only alert intent."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, update

from desk import line_signals, notify
from desk.account_owner import AccountOwnerStore
from desk.api import create_app
from desk.auth import Auth
from desk.db import account_owner_binding, line_watch, make_engine, migrate, now, users
from desk.db import line_signals as signal_rows
from desk.privacy import ISOLATED, LEGACY, PROTECTED, OwnerResources, PolicyEpoch, PrivacyPolicyStore
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _client

URL = "/api/notify/line-watch"
EVENTS = {"touch": True, "break": False, "retest": False, "long": False}


def test_exact_candles_are_sampled_only_for_alert_context(db_url, monkeypatch):
    fetched = "2026-11-24T18:00:00+00:00"
    calls = []

    async def candle_source(self, ticker, interval, count, indicators=()):
        calls.append((ticker, interval, count, indicators))
        hour_starts = ("14:30", "15:30", "16:30")
        five_starts = ("14:30", "14:35", "14:40", "14:50")
        starts = hour_starts if interval == "60m" else five_starts
        return {"ticker": ticker, "interval": interval, "fetched_at": fetched, "source": "yahoo", "bars": [
            {"at": f"2026-11-24T{start}:00+00:00", "open": 100, "high": 104, "low": 99, "close": 103, "volume": 7}
            for start in starts
        ]}

    from desk import line_watch_bars
    from desk.prices import PriceSource

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 24, 18, tzinfo=UTC)

    monkeypatch.setattr(PriceSource, "candles", candle_source)
    monkeypatch.setattr(line_watch_bars, "datetime", Clock)
    with _client(db_url) as client:
        default = {"ticker": "SOXL", "interval": "120m", "count": "20"}
        assert client.get("/api/candles", params=default).status_code == 200
        assert calls[-1][:2] == ("SOXL", "120m")
        for interval, source_tf, source_count, volume in (
            ("10m", "5m", 800, 14), ("120m", "60m", 820, 14),
        ):
            response = client.get("/api/candles", params={**default, "interval": interval, "line_alert": "true"})
            assert response.status_code == 200, response.text
            assert calls[-1] == ("SOXL", source_tf, source_count, ())
            data = response.json()
            assert data["interval"] == interval
            assert data["delay_sec"] == 900 and data["truncated"] is True
            assert [(r["at"], r["volume"]) for r in data["bars"]] == [("2026-11-24T14:30:00+00:00", volume)]
        assert client.get("/api/candles", params={**default, "interval": "10m"}).status_code == 422
        assert client.get("/api/candles", params={**default, "interval": "5m", "line_alert": "true"}).status_code == 422
        assert client.get("/api/candles", params={**default, "ticker": "BTC-USD", "line_alert": "true"}).json()["bars"] == []


def test_selected_signal_id_is_bounded_and_not_cached(db_url):
    with _client(db_url, login=False) as client:
        assert client.get(URL, params={"signal_id": 1}).status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        for invalid in (0, -1, 2147483648, "invalid"):
            assert client.get(URL, params={"signal_id": invalid}).status_code == 422
        response = client.get(URL, params={"signal_id": 1})
        assert response.json() == {"signal": None}
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("protected", [False, True])
def test_exact_notice_follows_recorded_owner_and_generation(db_url, monkeypatch, protected):
    migrate(db_url)
    engine = make_engine(db_url)
    captured = []

    def capture(pusher, *kinds, payload, user_id, allowed, **options):
        if allowed():
            captured.append((payload["url"], user_id))
        return 1

    monkeypatch.setattr(notify, "send", capture)
    try:
        auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
        auth.create_user(OWNER, PASSWORD)
        auth.create_user("next-owner", PASSWORD)
        with engine.connect() as conn:
            owner_id = conn.scalar(select(users.c.id).where(users.c.username == OWNER))
            other_id = conn.scalar(select(users.c.id).where(users.c.username == "next-owner"))
        store = AccountOwnerStore(engine)
        resources = None
        if protected:
            binding = store.bind_verified(verified_user_id=owner_id, expected_vacancy=store.binding())
            assert binding is not None
            resources = OwnerResources(binding)
            policy = PrivacyPolicyStore(engine)
            isolated = policy.transition(PolicyEpoch(LEGACY, 0), ISOLATED)
            policy.transition(isolated, PROTECTED)
        app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0),
                         protect_run_reads=protected, owner_resources=resources)
        with TestClient(app, base_url="https://testserver") as client:
            assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
            watch = app.state.line_watch
            owner = watch.owner_snapshot()
            assert owner is not None and owner.user_id == owner_id
            signal = {"at": "2026-11-24T14:30:00+00:00", "event": "touch", "rule": "touch_resistance",
                      "line_id": "upper", "price": 101.0, "line_price": 102.0}
            monkeypatch.setattr(line_signals, "text", lambda *args: ("Title", "Body"))
            assert len(watch._deliver("SOXL", "10m", [signal], None, owner=owner)) == 1
            with engine.begin() as conn:
                saved = conn.execute(select(signal_rows)).mappings().one()
                assert (saved["signal_owner_id"], saved["signal_owner_generation"]) == (owner_id, owner.generation)
                # Existing links cannot discover an owner by guessing the row number.
                row = {key: saved[key] for key in ("trade_date", "ticker", "timeframe", "event", "rule",
                                                  "at", "price", "line_price", "text", "created_at")}
                unowned = conn.execute(insert(signal_rows).values(**row, line_id="older")).inserted_primary_key[0]
                typical = {**row, "timeframe": "60m"}
                conn.execute(insert(signal_rows).values(**typical, line_id="ordinary"))
            signal_id = saved["id"]
            assert captured == [(f"/#/charts?t=SOXL&iv=10m&signal_tf=10m&signal_id={signal_id}", owner_id)]
            assert client.get(URL, params={"signal_id": signal_id}).json() == {"signal": {
                "ticker": "SOXL", "timeframe": "10m", "line_price": 102.0, "at": signal["at"],
            }}
            assert client.get(URL, params={"signal_id": unowned}).json() == {"signal": None}
            recent = {s["line_id"]: s for s in client.get(URL).json()["recent"]}
            assert recent.keys() == ({"upper"} if protected else {"upper", "ordinary"})
            assert recent["upper"]["signal_id"] == signal_id and "signal_id" not in recent.get("ordinary", {})

            assert client.post("/api/auth/logout").status_code == 204
            assert client.post("/api/auth/login", json={"username": "next-owner", "password": PASSWORD}).status_code == 204
            assert client.get(URL, params={"signal_id": signal_id}).json() == {"signal": None}
            assert client.get(URL).json()["recent"] == []
            if protected:
                # Model a completed owner transfer without changing the production verification path.
                with engine.begin() as conn:
                    conn.execute(update(account_owner_binding).values(user_id=None, generation="a" * 32))
                assert store.bind_verified(verified_user_id=other_id, expected_vacancy=store.binding())
                assert client.get(URL, params={"signal_id": signal_id}).json() == {"signal": None}
                assert client.get(URL).json()["recent"] == []
                with engine.begin() as conn:
                    conn.execute(update(account_owner_binding).values(user_id=None, generation="b" * 32))
                assert store.bind_verified(verified_user_id=owner_id, expected_vacancy=store.binding())
                assert client.post("/api/auth/logout").status_code == 204
                assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
                assert client.get(URL, params={"signal_id": signal_id}).json() == {"signal": None}
                assert client.get(URL).json()["recent"] == []
                assert watch._deliver("SOXL", "10m", [{**signal, "line_id": "stale"}], None, owner=owner) == []
    finally:
        engine.dispose()


def test_reused_legacy_sqlite_account_id_does_not_inherit_exact_signal(tmp_path, monkeypatch):
    db_url = f"sqlite:///{(tmp_path / 'reused.db').as_posix()}"
    monkeypatch.setattr(notify, "send", lambda *args, **kwargs: 0)
    from desk import auth as auth_module
    monkeypatch.setattr(auth_module, "now", lambda: datetime(2026, 11, 24, 6, tzinfo=UTC))
    with _client(db_url) as client:
        watch = client.app.state.line_watch
        owner = watch.owner_snapshot()
        signal = {"at": "2026-11-24T14:30:00+00:00", "event": "touch", "rule": "touch_resistance",
                  "line_id": "upper", "price": 101.0, "line_price": 102.0}
        monkeypatch.setattr(line_signals, "text", lambda *args: ("Title", "Body"))
        assert len(watch._deliver("SOXL", "120m", [signal], None, owner=owner)) == 1
        assert client.get(URL, params={"signal_id": 1}).json()["signal"]["line_price"] == 102.0
        assert client.post("/api/auth/logout").status_code == 204
        with watch.engine.begin() as conn:
            conn.execute(delete(users).where(users.c.id == owner.user_id))
        auth = Auth(watch.engine, timedelta(hours=1), timedelta(hours=1))
        # Same ID, username, password and timestamp. The new salted verifier still changes the identity.
        auth.create_user(OWNER, PASSWORD)
        replacement = watch.owner_snapshot()
        assert replacement.user_id == owner.user_id and replacement.generation != owner.generation
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        assert client.get(URL, params={"signal_id": 1}).json() == {"signal": None}
        assert client.get(URL).json()["recent"] == []


def test_owner_can_save_symbol_rules_and_restore_common_settings(db_url):
    with _client(db_url) as client:
        with client.app.state.line_watch.engine.begin() as conn:
            conn.execute(insert(line_watch).values(id=1, updated_at=now(), body={
                "enabled": True, "tickers": ["NQ=F", "SOXL"], "timeframes": ["60m"],
                "events": {"touch": True, "break": False, "retest": False},
            }))
        initial = client.get(URL)
        assert initial.headers["cache-control"] == "private, no-store"
        previous = initial.json()
        assert previous["rules"] is None
        assert previous["events"]["long"] is True
        assert set(previous["allowed"]["support"]["NQ=F"]["supported"]) == set(line_signals.TIMEFRAMES)
        assert set(previous["allowed"]["support"]) >= {"NQ=F", "^KS11", "005930.KS", "000660.KS", "SOXL", "KORU"}
        unsupported = client.get(URL, params={"ticker": "BTC-USD"}).json()["allowed"]["support"]["BTC-USD"]
        assert unsupported == {"market": None, "supported": [],
                               "unsupported": dict.fromkeys(line_signals.TIMEFRAMES, "unsupported_market")}
        rules = [
            {"ticker": "nq=f", "enabled": True, "timeframes": ["2h"], "side": "resistance", "events": EVENTS},
            {"ticker": "005930.KS", "enabled": False, "timeframes": ["1d", "1w"], "side": "both", "events": EVENTS},
        ]
        response = client.put(URL, json={"rules": rules})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        sent = response.json()["rules"]
        assert sent == [{**rules[0], "ticker": "NQ=F", "timeframes": ["120m"]}, rules[1]]
        assert client.get(URL).json()["rules"] == sent
        assert client.put(URL, json={"enabled": False}).json()["rules"] == sent
        assert client.put(URL, json={"rules": [rules[0], {**rules[0], "ticker": "NQ=F"}]}).status_code == 422
        restored = client.put(URL, json={"rules": None, "enabled": True}).json()
        assert restored["rules"] is None
        for key in ("enabled", "tickers", "timeframes", "events"):
            assert restored[key] == previous[key]


def test_another_user_cannot_read_rules_recent_signals_or_receive_notice(db_url, monkeypatch):
    deliveries = []

    def capture(pusher, *kinds, payload, user_id=None, **options):
        deliveries.append((kinds, payload, user_id))
        return 1

    monkeypatch.setattr(notify, "send", capture)
    with _client(db_url, login=False) as client:
        assert client.get(URL).status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        private_rule = {"ticker": "NQ=F", "enabled": True, "timeframes": ["10m"],
                        "side": "resistance", "events": EVENTS}
        assert client.put(URL, json={"rules": [private_rule], "enabled": True}).status_code == 200
        watch = client.app.state.line_watch
        monkeypatch.setattr(watch, "recent", lambda *, owner: [{"ticker": "NQ=F", "text": "private signal"}])
        assert client.get(URL).json()["recent"][0]["text"] == "private signal"

        engine = make_engine(db_url)
        try:
            with engine.connect() as conn:
                owner_id = conn.execute(select(users.c.id)).scalar_one()
            Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user("another-user", PASSWORD)
        finally:
            engine.dispose()
        watch.send_notice(None, line_signals.KIND, payload={"title": "synthetic notice"})
        assert deliveries == [((line_signals.KIND,), {"title": "synthetic notice"}, owner_id)]

        assert client.post("/api/auth/logout").status_code == 204
        assert client.post("/api/auth/login", json={"username": "another-user", "password": PASSWORD}).status_code == 204
        view = client.get(URL).json()
        assert not view["can_edit"] and not view["enabled"]
        assert view["rules"] == view["tickers"] == view["timeframes"] == view["recent"] == []
        assert client.put(URL, json={"rules": []}).status_code == 403
