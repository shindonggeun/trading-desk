"""The authenticated settings keep legacy data and owner-only alert intent."""

from datetime import timedelta

from sqlalchemy import insert, select

from desk import line_signals, notify
from desk.auth import Auth
from desk.db import line_watch, make_engine, now, users
from tests.test_api import OWNER, PASSWORD, _client

URL = "/api/notify/line-watch"
EVENTS = {"touch": True, "break": False, "retest": False, "long": False}


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
        monkeypatch.setattr(watch, "recent", lambda: [{"ticker": "NQ=F", "text": "private signal"}])
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
