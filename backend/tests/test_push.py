"""#361: web push subscriptions and foreign-flow turn alerts."""

import json
from datetime import timedelta

from fastapi.testclient import TestClient

from desk import flow_alerts, push
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine, migrate
from desk.runner import make_fake_runner
from desk.settings import Settings

OWNER, OTHER, PASSWORD = "owner@local", "other@local", "correct horse battery staple"
SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPk", "auth": "au"}, "device": "폰"}


def _turn(t, kind, market="futures", value=-620.0):
    return {"t": f"2026-11-13T{t}:00+09:00", "market": market, "who": "foreign", "kind": kind, "value": value}


class _Sent:
    def __init__(self, status=201):
        self.calls, self.status = [], status

    def __call__(self, sub, data):
        self.calls.append((sub["endpoint"], data))
        return self.status


def _pusher(engine, sent, keys=True):
    store = push.PushStore(engine)
    return push.Pusher(store, *(("pub", "priv", "mailto:x@y") if keys else ("", "", "")), send=sent)


def _store_with_sub(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OWNER, PASSWORD)
    user_id = auth.authenticate(OWNER, PASSWORD).id
    push.PushStore(engine).subscribe(user_id, SUB["endpoint"], SUB["keys"], "폰", {})
    return engine


def test_a_turn_pushes_once_and_the_turn_back_pushes_again(db_url):
    engine = _store_with_sub(db_url)
    sent = _Sent()
    alerts = flow_alerts.FlowAlerts(engine, _pusher(engine, sent))
    first = [_turn("10:32", "sell_turn")]
    assert [r["kind"] for r in alerts.dispatch(first, "2026-11-13")] == ["sell_turn"]
    assert alerts.dispatch(first, "2026-11-13") == []                          # judged again: nothing twice
    assert alerts.dispatch(first + [_turn("10:40", "sell_turn")], "2026-11-13") == []   # same direction
    back = alerts.dispatch(first + [_turn("11:05", "buy_turn", value=150)], "2026-11-13")
    assert [r["kind"] for r in back] == ["buy_turn"]
    assert len(sent.calls) == 2 and "순매도 전향" in sent.calls[0][1] and "10:32" in sent.calls[0][1]
    assert [a["kind"] for a in alerts.list("2026-11-13")] == ["sell_turn", "buy_turn"]
    engine.dispose()


def test_markets_are_separate_and_text_reads(db_url):
    engine = _store_with_sub(db_url)
    alerts = flow_alerts.FlowAlerts(engine, None)
    got = alerts.dispatch([_turn("10:32", "sell_turn"), _turn("10:33", "sell_turn", "kospi", -1200)], "2026-11-13")
    assert [r["market"] for r in got] == ["futures", "kospi"]
    assert got[0]["text"] == "외인 선물 순매도 전향 · 10:32 · 누적 -620계약"
    assert got[1]["text"] == "외인 코스피 현물 순매도 전향 · 10:33 · 누적 -1,200억"
    engine.dispose()


def test_a_market_toggle_or_the_master_switch_keeps_that_push_off(db_url):
    engine = _store_with_sub(db_url)                                   # the default: every kind on
    store = push.PushStore(engine)
    store.subscribe(1, "https://fcm.googleapis.com/fcm/send/nokospi", {"p256dh": "k", "auth": "a"}, "탭",
                    {"flow_kospi": False})
    store.subscribe(1, "https://fcm.googleapis.com/fcm/send/off", {"p256dh": "k", "auth": "a"}, "끔",
                    {"flow": False})
    sent = _Sent()
    alerts = flow_alerts.FlowAlerts(engine, _pusher(engine, sent))
    alerts.dispatch([_turn("10:32", "sell_turn"), _turn("10:33", "buy_turn", "kospi", 900)], "2026-11-13")
    to = [(c[0].rsplit("/", 1)[1], json.loads(c[1])["market"]) for c in sent.calls]
    assert to == [("abc", "futures"), ("nokospi", "futures"), ("abc", "kospi")]
    payload = json.loads(sent.calls[0][1])
    assert payload["url"] == flow_alerts.ALERT_URL and payload["kind"] == "sell_turn" and payload["tag"] == "flow-futures"
    assert store.of_user(1)[0]["kinds"] == {k: True for k in push.KINDS}
    # the same account sending the same endpoint again overwrites its kinds (a toggle in settings)
    store.subscribe(1, SUB["endpoint"], SUB["keys"], "폰", {"flow_futures": False})
    mine = next(r for r in store.of_user(1) if r["endpoint"] == SUB["endpoint"])
    assert mine["kinds"] == {**{k: True for k in push.KINDS}, "flow_futures": False}
    assert len(store.of_user(1)) == 3
    engine.dispose()


def test_a_gone_subscription_is_deleted_and_no_keys_means_off(db_url):
    engine = _store_with_sub(db_url)
    store = push.PushStore(engine)
    assert _pusher(engine, _Sent(), keys=False).broadcast("flow", payload={"body": "x"}) == 0
    assert len(store.for_kind("flow")) == 1
    assert _pusher(engine, _Sent(410)).broadcast("flow", payload={"body": "x"}) == 0
    assert store.for_kind("flow") == []
    engine.dispose()


def _app(db_url):
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0))
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OWNER, PASSWORD)
    auth.create_user(OTHER, PASSWORD)
    engine.dispose()
    return app


def _login(client, who):
    assert client.post("/api/auth/login", json={"username": who, "password": PASSWORD}).status_code == 204


def test_push_routes_need_a_session_and_are_off_without_keys(db_url):
    with TestClient(_app(db_url), base_url="https://testserver") as client:
        assert client.get("/api/push/key").status_code == 401
        assert client.post("/api/push/subscriptions", json=SUB).status_code == 401
        assert client.request("DELETE", "/api/push/subscriptions", json={"endpoint": SUB["endpoint"]}).status_code == 401
        assert client.post("/api/push/test").status_code == 401
        assert client.get("/api/alerts/flow").status_code == 401
        _login(client, OWNER)
        assert client.get("/api/push/key").json() == {"enabled": False, "public_key": None}
        assert client.post("/api/push/subscriptions", json=SUB).status_code == 503


def test_own_subscriptions_only(db_url):
    app = _app(db_url)
    sent = _Sent()
    with TestClient(app, base_url="https://testserver") as client:
        app.state.pusher = push.Pusher(app.state.pusher.store, "pub", "priv", "mailto:x@y", send=sent)
        _login(client, OWNER)
        assert client.get("/api/push/key").json() == {"enabled": True, "public_key": "pub"}
        for bad in ("http://fcm.googleapis.com/x", "https://192.168.0.9/x", "https://fcm.googleapis.com:8443/x",
                    "https://u@fcm.googleapis.com/x", "https://evil.example/x", "https://fcm.googleapis.com.evil.io/x",
                    "https://evil.com\\.notify.windows.com/x", "https://evil%2ecom.push.apple.com/x",
                    "https://fcm.googleapis.com./x"):
            assert client.post("/api/push/subscriptions", json={**SUB, "endpoint": bad}).status_code == 422, bad
        assert client.post("/api/push/subscriptions", json=SUB).status_code == 201
        got = client.post("/api/push/subscriptions", json={**SUB, "device": "폰\n가짜 로그 줄"}).json()
        assert got["device"] == "폰가짜 로그 줄"
        client.post("/api/auth/logout")
        _login(client, OTHER)
        assert client.request("DELETE", "/api/push/subscriptions", json={"endpoint": SUB["endpoint"]}).status_code == 204
        assert client.post("/api/push/subscriptions", json=SUB).status_code == 409   # not taken over
        assert client.post("/api/push/test").json() == {"sent": 0}          # OTHER has no device
        client.post("/api/auth/logout")
        _login(client, OWNER)
        assert client.post("/api/push/test").json() == {"sent": 1}          # still there
        assert client.get("/api/alerts/flow?date=2026-11-13").json() == {"date": "2026-11-13", "items": []}
        assert client.get("/api/alerts/flow?date=bad").status_code == 422


def test_a_stored_endpoint_off_the_push_services_is_never_called(db_url):
    engine = _store_with_sub(db_url)
    store = push.PushStore(engine)
    store.subscribe(1, "https://10.0.0.1/hook", {"p256dh": "k", "auth": "a"}, "lan", {})
    sent = _Sent()
    assert _pusher(engine, sent).broadcast("flow", payload={"body": "x"}) == 1
    assert [c[0] for c in sent.calls] == [SUB["endpoint"]]
    engine.dispose()


def test_after_snapshot_judges_each_market_on_todays_points(db_url, monkeypatch):
    from datetime import date

    from desk import flow_intraday

    engine = _store_with_sub(db_url)
    seen = []

    def turns(points, market):
        seen.append((points, market))
        return [_turn("10:32", "sell_turn")] if market == "futures" else []

    monkeypatch.setattr(flow_intraday, "turns", turns)
    alerts = flow_alerts.FlowAlerts(engine, None)
    got = alerts.after_snapshot(lambda day: [{"day": day}], day=date(2026, 11, 13))
    assert [m for _, m in seen] == ["futures", "kospi", "kosdaq"] and seen[0][0] == [{"day": date(2026, 11, 13)}]
    assert [(r["trade_date"], r["market"]) for r in got] == [("2026-11-13", "futures")]
    assert alerts.after_snapshot(lambda day: [], day=date(2026, 11, 13)) == []
    engine.dispose()
