"""#580 Telegram · KakaoTalk alert channels: sending, retries, token refresh, isolation, the settings API."""

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from desk import notify, otp
from desk import notify_channels as nc
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine, migrate, notify_channels
from desk.runner import make_fake_runner
from desk.settings import Settings

TOKEN, CHAT = "123456:SECRET-bot-token", "987654321"
KEY = otp.new_key()
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
    return engine, nc.ChannelStore(engine, otp.SecretBox(KEY, "DESK_NOTIFY_KEY"))


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


def _kakao(store, http, clock=lambda: NOW):
    return nc.Kakao("rest-key", "client-secret", "https://desk.test/kakao-callback.html", store, "https://desk.test",
                    http.client(), sleep=lambda s: None, clock=clock)


def _tokens(access="acc-1", refresh="ref-1", refresh_in=5_000_000):
    return {"access_token": access, "refresh_token": refresh, "expires_in": 21599,
            "refresh_token_expires_in": refresh_in, "token_type": "bearer"}


def test_kakao_connects_seals_the_tokens_and_sends_to_me(db_url):
    engine, store = _store(db_url)
    http = _Http((200, _tokens()), (200, {"result_code": 0}))
    kakao = _kakao(store, http)
    assert "scope=talk_message" in kakao.authorize_url("st") and "state=st" in kakao.authorize_url("st")
    kakao.connect("the-code")
    form = dict(httpx.QueryParams(http.requests[0].content.decode()))
    assert form == {"client_id": "rest-key", "client_secret": "client-secret", "grant_type": "authorization_code",
                    "redirect_uri": "https://desk.test/kakao-callback.html", "code": "the-code"}
    with engine.connect() as conn:
        raw = conn.execute(notify_channels.select()).mappings().one()
    assert "acc-1" not in raw["secret"] and "ref-1" not in raw["secret"] and raw["enabled"] is True
    kakao.send(ALERT)
    memo = http.requests[1]
    assert memo.headers["Authorization"] == "Bearer acc-1"
    template = json.loads(dict(httpx.QueryParams(memo.content.decode()))["template_object"])
    assert template["object_type"] == "text" and template["text"].startswith("수급 흐름 변화\n")
    assert template["link"] == {"web_url": "https://desk.test/#/market", "mobile_web_url": "https://desk.test/#/market"}
    assert kakao.status() == {"connected": True, "needs_reconnect": False,
                              "refresh_expires_at": (NOW + timedelta(seconds=5_000_000)).isoformat(timespec="seconds")}
    engine.dispose()


def test_kakao_refreshes_before_expiry_and_after_a_401_keeping_the_refresh_token(db_url):
    engine, store = _store(db_url)
    clock = {"now": NOW}
    http = _Http((200, _tokens()))
    kakao = _kakao(store, http, clock=lambda: clock["now"])
    kakao.connect("c")
    clock["now"] = NOW + timedelta(hours=6)                 # 59 s left on the access token
    http.answers += [(200, {"access_token": "acc-2", "expires_in": 21599}), (200, {"result_code": 0})]
    kakao.send(ALERT)
    refresh = dict(httpx.QueryParams(http.requests[1].content.decode()))
    assert refresh["grant_type"] == "refresh_token" and refresh["refresh_token"] == "ref-1"
    assert http.requests[2].headers["Authorization"] == "Bearer acc-2"
    assert store.tokens("kakao") == {"access_token": "acc-2", "refresh_token": "ref-1"}   # no new one: kept
    http.answers += [(401, {"code": -401}), (200, {"access_token": "acc-3", "refresh_token": "ref-2",
                                                   "expires_in": 21599}), (200, {"result_code": 0})]
    kakao.send(ALERT)
    assert http.requests[-1].headers["Authorization"] == "Bearer acc-3" and store.tokens("kakao")["refresh_token"] == "ref-2"
    engine.dispose()


def test_a_refused_refresh_asks_to_reconnect_and_so_does_a_refresh_token_near_its_end(db_url):
    engine, store = _store(db_url)
    http = _Http((200, _tokens(refresh_in=3 * 86400)))
    kakao = _kakao(store, http)
    kakao.connect("c")
    assert kakao.status()["needs_reconnect"] is True                     # 3 days left
    http.answers += [(401, {"code": -401}), (400, {"error": "invalid_grant", "error_code": "KOE322"})]
    with pytest.raises(nc.ChannelError) as got:
        kakao.send(ALERT)
    assert str(got.value) == "kakao token invalid_grant" and store.get("kakao")["needs_reconnect"] is True
    store.forget("kakao")
    assert kakao.status()["connected"] is False
    with pytest.raises(nc.ChannelError, match="not connected"):
        kakao.send(ALERT)
    no_secret = nc.Kakao("rest-key", "", "https://desk.test/kakao-callback.html", store)
    no_key = nc.Kakao("rest-key", "s", "https://desk.test/kakao-callback.html", nc.ChannelStore(engine, otp.SecretBox("")))
    assert not no_secret.configured and not no_key.configured and "rest-key" not in repr(no_secret)
    engine.dispose()


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
    good, bad = _Channel("telegram"), _Channel("kakao", RuntimeError(f"https://x/bot{TOKEN}"))
    notify.install([good, bad], store)
    try:
        assert notify.send(_Pusher(), "flow_shift", payload=ALERT) == 2      # both off by default
        notify.drain()
        assert good.got == []
        store.set_prefs("telegram", True, {"nq_alert": False})
        store.set_prefs("kakao", True)
        assert notify.send(_Pusher(), "flow_shift", payload=ALERT) == 2
        assert notify.send(None, "nq_alert", payload=ALERT) == 0              # telegram has nq_alert off
        notify.drain()
        assert good.got == [ALERT]
        assert notify.test("kakao", ALERT) == "RuntimeError" and notify.test("telegram", ALERT) is None
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
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0, public_url="https://desk.test",
                        telegram_bot_token=TOKEN, telegram_chat_id=CHAT, **env)
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
                             ("POST", "/api/notify/channels/telegram/test"), ("POST", "/api/notify/kakao/connect"),
                             ("POST", "/api/notify/kakao/callback"), ("DELETE", "/api/notify/kakao")):
            assert client.request(method, path, json={}).status_code == 401, path
        _login(client, OTHER)
        body = client.get("/api/notify/channels").json()
        assert body["can_edit"] is False and TOKEN not in json.dumps(body) and CHAT not in json.dumps(body)
        tg = next(c for c in body["channels"] if c["id"] == "telegram")
        assert tg["configured"] is True and tg["enabled"] is False and tg["kinds"]["flow_shift"] is True
        kakao = next(c for c in body["channels"] if c["id"] == "kakao")
        assert kakao["configured"] is False and kakao["connected"] is False
        assert client.put("/api/notify/channels/telegram", json={"enabled": True}).status_code == 403
        assert client.post("/api/notify/channels/telegram/test").status_code == 403
        assert client.post("/api/notify/kakao/connect").status_code == 403
        _login(client, OWNER)
        assert client.put("/api/notify/channels/line", json={"enabled": True}).status_code == 404
        assert client.put("/api/notify/channels/telegram", json={"kinds": {"sms": True}}).status_code == 422
        got = client.put("/api/notify/channels/telegram", json={"enabled": True, "kinds": {"nq_alert": False}}).json()
        assert got["enabled"] is True and got["kinds"]["nq_alert"] is False and got["kinds"]["flow"] is True
        assert client.post("/api/notify/kakao/connect").status_code == 503              # not set up
        sent = []
        notify.channel("telegram").send = sent.append
        assert client.post("/api/notify/channels/telegram/test").json() == {"ok": True, "error": None}
        assert sent[0]["body"] == "시험 알림입니다."
        assert client.post("/api/notify/channels/telegram/test").status_code == 429       # one every few seconds
        assert client.post("/api/notify/channels/kakao/test").status_code == 429


def test_kakao_connect_and_callback_bind_the_state_to_the_owner_once(db_url, monkeypatch):
    app = _app(db_url, kakao_rest_key="rest-key", kakao_client_secret="client-secret", notify_key=KEY)
    with TestClient(app, base_url="https://testserver") as client:
        _login(client, OWNER)
        url = client.post("/api/notify/kakao/connect").json()["url"]
        assert url.startswith("https://kauth.kakao.com/oauth/authorize?")
        params = dict(httpx.QueryParams(url.split("?", 1)[1]))
        assert params["redirect_uri"] == "https://desk.test/kakao-callback.html" and len(params["state"]) >= 40
        codes = []
        monkeypatch.setattr(notify.channel("kakao"), "connect", codes.append)
        assert client.post("/api/notify/kakao/callback", json={"code": "c", "state": "forged"}).status_code == 400
        assert client.post("/api/notify/kakao/callback", json={"code": "c", "state": params["state"]},
                           headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.post("/api/notify/kakao/callback", json={"code": "c", "state": params["state"]}).status_code == 204
        assert codes == ["c"]
        assert client.post("/api/notify/kakao/callback", json={"code": "c", "state": params["state"]}).status_code == 400
        assert client.delete("/api/notify/kakao").status_code == 204
