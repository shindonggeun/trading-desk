"""#580 the Telegram alert channel: sending, retries, isolation, the settings API (#986 Telegram only)."""

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from desk import notify
from desk import notify_channels as nc
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine, migrate, notify_channels
from desk.runner import make_fake_runner
from desk.settings import Settings

TOKEN, CHAT = "123456:SECRET-bot-token", "987654321"
NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)
ALERT = {"title": "수급 흐름 변화", "body": "🔵 기관 코스피 현물 매도 확대 · 10:42", "url": "/#/market"}


class _Http:
    """httpx.MockTransport answering from a queue of (status, json) per host; requests kept."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.answers.pop(0)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _store(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    return engine, nc.ChannelStore(engine)


def test_telegram_sends_plain_text_with_a_link_and_honours_retry_after():
    http = _Http((429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}}), (200, {"ok": True}))
    waits = []
    nc.Telegram(TOKEN, CHAT, "https://desk.test", http.client(), sleep=waits.append).send(ALERT)
    assert waits == [3.0] and len(http.requests) == 2
    sent = json.loads(http.requests[-1].content)
    assert sent == {"chat_id": CHAT, "text": "수급 흐름 변화\n🔵 기관 코스피 현물 매도 확대 · 10:42\nhttps://desk.test/#/market",
                    "disable_web_page_preview": True}
    assert "parse_mode" not in sent


def test_a_telegram_failure_is_a_short_code_without_the_token():
    http = _Http((400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}))
    with pytest.raises(nc.ChannelError) as got:
        nc.Telegram(TOKEN, CHAT, client=http.client(), sleep=lambda s: None).send(ALERT)
    assert str(got.value) == "telegram 400"
    down = _Http(*[(0, httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{TOKEN}/x"))] * 3)
    with pytest.raises(nc.ChannelError) as got:
        nc.Telegram(TOKEN, CHAT, client=down.client(), sleep=lambda s: None).send(ALERT)
    assert str(got.value) == "ConnectError" and TOKEN not in repr(got.value)
    assert not nc.Telegram("", CHAT).configured and "SECRET" not in repr(nc.Telegram(TOKEN, CHAT))


def test_httpx_request_lines_never_show_the_bot_token(caplog):
    nc.install_log_filter()
    logger = logging.getLogger("httpx")
    assert logger.level == logging.WARNING
    logger.setLevel(logging.INFO)                        # should anyone lower it again
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            logger.info('HTTP Request: %s %s "%s"', "POST", httpx.URL(f"https://api.telegram.org/bot{TOKEN}/sendMessage"),
                        "HTTP/1.1 200 OK")
        assert TOKEN not in caplog.text and "/bot***/sendMessage" in caplog.text
    finally:
        logger.setLevel(logging.WARNING)


class _Channel:
    def __init__(self, name, fail=None):
        self.name, self.fail, self.got, self.configured = name, fail, [], True

    def send(self, payload):
        if self.fail:
            raise self.fail
        self.got.append(payload)


class _Pusher:
    def broadcast(self, *kinds, payload):
        return 2


def test_notify_sends_to_the_channels_that_want_the_kind_and_isolates_failures(db_url):
    engine, store = _store(db_url)
    good, bad = _Channel("telegram"), _Channel("other", RuntimeError(f"https://x/bot{TOKEN}"))
    notify.install([good, bad], store)
    try:
        assert notify.send(_Pusher(), "flow_shift", payload=ALERT) == 2      # both off by default
        notify.drain()
        assert good.got == []
        store.set_prefs("telegram", True, {"nq_alert": False})
        store.set_prefs("other", True)
        assert notify.send(_Pusher(), "flow_shift", payload=ALERT) == 2
        assert notify.send(None, "nq_alert", payload=ALERT) == 0              # telegram has nq_alert off
        notify.drain()
        assert good.got == [ALERT]
        assert notify.test("other", ALERT) == "RuntimeError" and notify.test("telegram", ALERT) is None
        assert notify.test("line", ALERT) == "unknown channel"
    finally:
        notify.install([], None)
    engine.dispose()


def test_a_full_queue_drops_the_send_not_the_alert(db_url, monkeypatch):
    engine, store = _store(db_url)
    ch = _Channel("telegram")
    store.set_prefs("telegram", True)
    notify.install([ch], store)
    monkeypatch.setattr(notify, "_slots", __import__("threading").BoundedSemaphore(1))
    try:
        gate = __import__("threading").Event()
        notify._executor().submit(gate.wait)                    # the worker busy
        notify.send(None, "flow", payload=ALERT)                # takes the one slot
        notify.send(None, "flow", payload=ALERT)                # dropped
        gate.set()
        notify.drain()
        assert len(ch.got) == 1
    finally:
        notify.install([], None)
    engine.dispose()


OWNER, OTHER, PASSWORD = "owner@local", "other@local", "correct horse battery staple"


def _app(db_url, **env):
    env = {"public_url": "https://desk.test", "telegram_bot_token": TOKEN, "telegram_chat_id": CHAT, **env}
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0, **env)
    app = create_app(settings, runner=make_fake_runner(0))
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OWNER, PASSWORD)
    auth.create_user(OTHER, PASSWORD)
    engine.dispose()
    return app


def _login(client, who):
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": who, "password": PASSWORD}).status_code == 204


def test_the_channel_routes_need_a_session_and_only_the_owner_changes_them(db_url):
    app = _app(db_url)
    with TestClient(app, base_url="https://testserver") as client:
        for method, path in (("GET", "/api/notify/channels"), ("PUT", "/api/notify/channels/telegram"),
                             ("POST", "/api/notify/channels/telegram/test")):
            assert client.request(method, path, json={}).status_code == 401, path
        _login(client, OTHER)
        body = client.get("/api/notify/channels").json()
        assert body["can_edit"] is False and TOKEN not in json.dumps(body) and CHAT not in json.dumps(body)
        tg = next(c for c in body["channels"] if c["id"] == "telegram")
        assert tg["configured"] is True and tg["enabled"] is False and tg["kinds"]["flow_shift"] is True
        assert client.put("/api/notify/channels/telegram", json={"enabled": True}).status_code == 403
        assert client.post("/api/notify/channels/telegram/test").status_code == 403
        _login(client, OWNER)
        assert client.put("/api/notify/channels/line", json={"enabled": True}).status_code == 404
        assert client.put("/api/notify/channels/telegram", json={"kinds": {"sms": True}}).status_code == 422
        got = client.put("/api/notify/channels/telegram", json={"enabled": True, "kinds": {"nq_alert": False}}).json()
        assert got["enabled"] is True and got["kinds"]["nq_alert"] is False and got["kinds"]["flow"] is True
        sent = []
        notify.channel("telegram").send = sent.append
        assert client.post("/api/notify/channels/telegram/test").json() == {"ok": True, "error": None}
        assert sent[0]["body"] == "시험 알림입니다."
        assert client.post("/api/notify/channels/telegram/test").status_code == 429       # one every few seconds


def test_the_app_starts_with_no_channel_keys_and_the_channel_is_off(db_url):
    """The compose keys are optional (${KEY:-}): a deploy before the owner fills them must still start."""
    app = _app(db_url, telegram_bot_token="", telegram_chat_id="")
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/health").status_code == 200
        _login(client, OWNER)
        chans = client.get("/api/notify/channels").json()["channels"]
        assert [(c["id"], c["configured"], c["connected"]) for c in chans] == [("telegram", False, False)]
        assert client.post("/api/notify/channels/telegram/test").json() == {"ok": False, "error": "not configured"}


def test_an_upsert_that_loses_the_race_still_writes_its_values(db_url):
    engine, store = _store(db_url)
    store._upsert("telegram", {"enabled": False})
    store._upsert("telegram", {"enabled": True, "kinds": {"flow": False}})
    assert store.get("telegram")["enabled"] is True and store.get("telegram")["kinds"] == {"flow": False}
    engine.dispose()


def test_after_shutdown_nothing_is_queued_and_a_slow_test_send_answers(db_url, monkeypatch):
    engine, store = _store(db_url)
    ch = _Channel("telegram")
    store.set_prefs("telegram", True)
    notify.install([ch], store)
    notify.shutdown()
    notify.send(None, "flow", payload=ALERT)
    assert notify._pool is None and ch.got == []
    with pytest.raises(nc.ChannelError, match="shut down"):
        notify.run(lambda: None)
    notify.install([ch], store)
    monkeypatch.setattr(notify, "TEST_TIMEOUT_SEC", 0.05)
    slow = _Channel("other")
    slow.send = lambda payload: __import__("time").sleep(0.3)
    notify.install([ch, slow], store)
    with pytest.raises(nc.ChannelError, match="timeout"):
        notify.test("other", ALERT)
    engine.dispose()


def test_a_leftover_kakao_row_is_ignored_and_the_kakao_routes_are_gone(db_url):
    """#986 KakaoTalk was dropped: the row it left in notify_channels neither shows nor sends, its routes are gone."""
    migrate(db_url)
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(notify_channels.insert().values(
            channel="kakao", enabled=True, kinds={}, secret="sealed-by-a-retired-key", access_expires_at=NOW,
            refresh_expires_at=NOW + timedelta(days=60), needs_reconnect=True, updated_at=NOW))
    engine.dispose()
    app = _app(db_url)
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/health").status_code == 200
        _login(client, OWNER)
        body = client.get("/api/notify/channels").json()
        assert set(body) == {"kinds", "can_edit", "channels"} and [c["id"] for c in body["channels"]] == ["telegram"]
        tg = body["channels"][0]
        assert tg["connected"] is True and tg["needs_reconnect"] is False and tg["refresh_expires_at"] is None
        for method, path, payload in (("POST", "/api/notify/kakao/connect", None),
                                      ("POST", "/api/notify/kakao/callback", {"code": "c", "state": "s"}),
                                      ("DELETE", "/api/notify/kakao", None),
                                      ("PUT", "/api/notify/channels/kakao", {"enabled": True}),
                                      ("POST", "/api/notify/channels/kakao/test", None)):
            assert client.request(method, path, json=payload).status_code == 404, path
        assert notify.channel("kakao") is None
        assert client.put("/api/notify/channels/telegram", json={"enabled": True}).status_code == 200
        sent = []
        notify.channel("telegram").send = sent.append
        notify.send(None, "flow", payload=ALERT)
        notify.drain()
        assert sent == [ALERT]
