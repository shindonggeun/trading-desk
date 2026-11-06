"""KIS websocket stream (#125) against an in-memory socket and a mocked approval endpoint."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from desk import live
from desk.broker import kis_stream
from desk.broker.kis_stream import KR, US, KisStream, market_of, parse_frame
from desk.broker.mock import MockBroker
from desk.prices import PriceSource
from desk.settings import Settings

APP_KEY, APP_SECRET, APPROVAL = "test-app-key-123", "test-app-secret-456", "approval-xyz-789"


def _kr(code="005930", price="71900", rate="1.27"):
    f = [""] * 46
    f[0], f[1], f[2], f[5] = code, "093354", price, rate
    return f


def _us(symbol="SOXL", price="31.25", rate="-2.10", rsym=False):
    f = [""] * 25
    f[0], f[10], f[13] = symbol, price, rate
    return ([f"DAMS{symbol}"] + f) if rsym else f


def _frame(tr_id, *records):
    return f"0|{tr_id}|{len(records):03d}|" + "^".join("^".join(r) for r in records)


def test_parse_domestic_and_overseas_frames():
    assert parse_frame(_frame(KR, _kr())) == [(KR, "005930", 71900.0, 1.27)]
    two = parse_frame(_frame(KR, _kr(), _kr("000660", "180000", "-0.5")))
    assert [r[1:3] for r in two] == [("005930", 71900.0), ("000660", 180000.0)]
    assert parse_frame(_frame(US, _us())) == [(US, "SOXL", 31.25, -2.1)]
    assert parse_frame(_frame(US, _us(rsym=True))) == [(US, "SOXL", 31.25, -2.1)]


@pytest.mark.parametrize("raw", ["1|H0STCNT0|001|cipher", "0|H0STCNT0|abc|x", "0|H0STCNT0|002|a^b^c",
                                 "0|H0STASP0|001|x", "garbage", _frame(KR, _kr(price="0"))])
def test_parse_ignores_encrypted_malformed_and_zero(raw):
    assert parse_frame(raw) == []


def test_only_plain_krx_and_us_symbols_stream():
    assert market_of("005930.KS") == "KR" and market_of("0091C0.KQ") == "KR"
    assert market_of("NVDA") == "US" and market_of("soxl") == "US"
    for t in ("^KS11", "BTC-USD", "KRW=X", "NQ=F", "BRK-B", "12345.KS", "TOOLONG"):
        assert market_of(t) is None


def test_off_without_both_keys():
    assert kis_stream.make(Settings(kis_app_key="", kis_app_secret="x")) is None
    assert kis_stream.make(Settings(kis_app_key="k", kis_app_secret="")) is None
    assert isinstance(kis_stream.make(Settings(kis_app_key="k", kis_app_secret="s")), KisStream)


# --- the connection -------------------------------------------------------------------------


class FakeSocket:
    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.pongs: list[bytes] = []

    async def recv(self):
        item = await self.inbox.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def pong(self, data):
        self.pongs.append(data)

    def subs(self, tr_type="1"):
        return [m["body"]["input"]["tr_key"] for m in self.sent if m["header"]["tr_type"] == tr_type]


class Harness:
    def __init__(self, exchange="AMS"):
        self.sockets: list[FakeSocket] = []
        self.approvals: list[dict] = []
        self.now = [1000.0]

        def handler(request: httpx.Request):
            self.approvals.append(json.loads(request.content))
            return httpx.Response(200, json={"approval_key": APPROVAL})

        async def exchange_of(ticker):
            return exchange

        self.stream = KisStream(APP_KEY, APP_SECRET, connect=self.connect,
                                http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                exchange_of=exchange_of, clock=lambda: self.now[0])

    def connect(self, url):
        harness = self

        class _Ctx:
            async def __aenter__(self):
                sock = FakeSocket()
                harness.sockets.append(sock)
                return sock

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(kis_stream, "RECONCILE_SEC", 0.01)
    monkeypatch.setattr(kis_stream, "BACKOFF_START_SEC", 0.01)


async def _until(cond, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def test_subscribes_on_demand_streams_trades_and_unsubscribes():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        await asyncio.sleep(0.05)
        assert not h.sockets                     # no demand, no connection
        h.stream.want("005930.KS")
        h.stream.want("SOXL")
        h.stream.want("^KS11")                   # not streamable: ignored
        await _until(lambda: h.sockets and len(h.sockets[0].subs()) == 2)
        sock = h.sockets[0]
        assert sorted(sock.subs()) == ["005930", "DAMSSOXL"]
        assert all(m["header"]["approval_key"] == APPROVAL and m["header"]["custtype"] == "P" for m in sock.sent)
        assert h.approvals == [{"grant_type": "client_credentials", "appkey": APP_KEY, "secretkey": APP_SECRET}]

        assert h.stream.latest("005930.KS") is None          # subscribed, nothing traded yet
        await sock.inbox.put(_frame(KR, _kr()))
        await sock.inbox.put(_frame(US, _us()))
        await _until(lambda: h.stream.latest("SOXL") is not None)
        assert h.stream.latest("005930.KS").price == 71900.0

        prices = PriceSource(MockBroker(), stream=h.stream)
        q = await prices.quote("005930.KS")
        assert (q.price, q.source, q.currency) == (71900.0, "kis", "KRW")
        assert (await prices.quote("SOXL")).currency == "USD"

        h.now[0] += kis_stream.WANT_SEC - 5
        h.stream.want("SOXL")                    # still watched; the KR one lapses
        h.now[0] += 10
        await _until(lambda: sock.subs("2") == ["005930"])
        assert h.stream.latest("005930.KS") is None and h.stream.latest("SOXL") is not None
        task.cancel()

    asyncio.run(go())


def test_pingpong_is_answered_and_refusal_stops_asking():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        h.stream.want("NVDA")
        await _until(lambda: h.sockets and h.sockets[0].subs())
        sock = h.sockets[0]
        ping = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "20261106093000"}})
        await sock.inbox.put(ping)
        await _until(lambda: sock.pongs)
        assert sock.pongs == [ping.encode()]

        await sock.inbox.put(json.dumps({"header": {"tr_id": US, "tr_key": "DAMSNVDA"},
                                         "body": {"rt_cd": "1", "msg1": "MAX SUBSCRIBE OVER"}}))
        await asyncio.sleep(0.1)
        assert sock.subs() == ["DAMSNVDA"]       # not asked again while refused
        assert h.stream.status()["subscribed"] == 0
        task.cancel()

    asyncio.run(go())


def test_no_exchange_keeps_polling():
    async def go():
        h = Harness(exchange=None)
        task = asyncio.create_task(h.stream.run())
        h.stream.want("ABCD")
        await _until(lambda: h.sockets)
        await asyncio.sleep(0.05)
        assert h.sockets[0].sent == [] and h.stream.latest("ABCD") is None
        task.cancel()

    asyncio.run(go())


def test_reconnects_resubscribes_and_reuses_the_approval_key(caplog):
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        h.stream.want("005930.KS")
        await _until(lambda: h.sockets and h.sockets[0].subs())
        await h.sockets[0].inbox.put(_frame(KR, _kr()))
        await _until(lambda: h.stream.latest("005930.KS") is not None)
        await h.sockets[0].inbox.put(ConnectionError(f"boom {APPROVAL}"))
        await _until(lambda: not h.stream.connected or len(h.sockets) > 1)
        assert h.stream.latest("005930.KS") is None          # a dead socket never answers
        h.stream.want("005930.KS")
        await _until(lambda: len(h.sockets) == 2 and h.sockets[1].subs())
        assert h.sockets[1].subs() == ["005930"]
        assert len(h.approvals) == 1
        task.cancel()

    with caplog.at_level(logging.DEBUG, logger="desk.broker.kis_stream"):
        asyncio.run(go())
    text = caplog.text
    assert "ConnectionError" in text
    for secret in (APP_KEY, APP_SECRET, APPROVAL):
        assert secret not in text


def test_failed_approval_is_not_retried_in_a_loop(caplog):
    async def go():
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(403, json={"error_description": "bad key"})

        now = [0.0]
        stream = KisStream(APP_KEY, APP_SECRET, connect=lambda url: None,
                           http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=lambda: now[0])
        task = asyncio.create_task(stream.run())
        stream.want("NVDA")
        await asyncio.sleep(0.3)                 # several backoff rounds, clock frozen
        assert calls == [1] and not stream.connected
        task.cancel()

    with caplog.at_level(logging.DEBUG, logger="desk.broker.kis_stream"):
        asyncio.run(go())
    assert APP_SECRET not in caplog.text and APP_KEY not in caplog.text


def test_live_frame_fast_round_carries_the_quote_only():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        prices = PriceSource(MockBroker(), stream=h.stream)
        assert live.watch(prices, "005930.KS") is False      # asked, nothing yet
        await _until(lambda: h.sockets and h.sockets[0].subs())
        await h.sockets[0].inbox.put(_frame(KR, _kr(price="72000")))
        await _until(lambda: live.watch(prices, "005930.KS"))
        out = await live.frame(prices, "005930.KS", bars=False)
        assert out["quote"]["source"] == "kis" and out["quote"]["price"] == 72000.0 and "bars" not in out
        task.cancel()

    asyncio.run(go())
    assert live.watch(PriceSource(MockBroker()), "005930.KS") is False   # no stream: polling


def test_rsym_is_told_by_its_shape_not_the_record_size():
    extra = _us() + ["X"]                       # 26 columns, but no RSYM in front
    assert parse_frame(_frame(US, extra)) == [(US, "SOXL", 31.25, -2.1)]


def test_make_refuses_plain_http_for_the_keys_and_quiets_websockets_debug():
    assert kis_stream.make(Settings(kis_app_key="k", kis_app_secret="s", kis_rest_url="http://x:9443")) is None
    assert kis_stream.make(Settings(kis_app_key="k", kis_app_secret="s", kis_ws_url="https://x")) is None
    logging.getLogger("websockets").setLevel(logging.NOTSET)
    assert kis_stream.make(Settings(kis_app_key="k", kis_app_secret="s")) is not None
    assert logging.getLogger("websockets").level == logging.INFO
    assert "s-secret" not in repr(Settings(kis_app_key="k-secret", kis_app_secret="s-secret"))
    assert "k-secret" not in repr(Settings(kis_app_key="k-secret", kis_app_secret="s-secret"))


def test_a_stale_trade_is_not_served():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        h.stream.want("005930.KS")
        await _until(lambda: h.sockets and h.sockets[0].subs())
        await h.sockets[0].inbox.put(_frame(KR, _kr()))
        await _until(lambda: h.stream.latest("005930.KS") is not None)
        h.now[0] += kis_stream.FRESH_SEC + 1
        h.stream.want("005930.KS")
        assert h.stream.latest("005930.KS") is None          # the caller polls Toss instead
        task.cancel()

    asyncio.run(go())


def test_one_krx_code_is_subscribed_once():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        h.stream.want("005930.KS")
        h.stream.want("005930.KQ")
        await _until(lambda: h.sockets and h.sockets[0].subs())
        await asyncio.sleep(0.05)
        assert h.sockets[0].subs() == ["005930"]
        task.cancel()

    asyncio.run(go())


def test_idle_closes_the_socket_and_the_next_watch_reopens_it():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        h.stream.want("005930.KS")
        await _until(lambda: h.sockets and h.sockets[0].subs())
        h.now[0] += kis_stream.WANT_SEC + 1
        await _until(lambda: h.sockets[0].subs("2") == ["005930"])
        h.now[0] += kis_stream.IDLE_SEC + 1
        await _until(lambda: not h.stream.connected)
        assert not h.stream._demand.is_set() and len(h.sockets) == 1
        h.stream.want("005930.KS")
        await _until(lambda: len(h.sockets) == 2 and h.sockets[1].subs())
        assert len(h.approvals) == 1                          # the key is reused
        task.cancel()

    asyncio.run(go())


def test_a_refused_approval_key_reconnects_with_a_new_one():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        h.stream.want("NVDA")
        await _until(lambda: h.sockets and h.sockets[0].subs())
        await h.sockets[0].inbox.put(json.dumps({"header": {"tr_id": US, "tr_key": "DAMSNVDA"},
                                                 "body": {"rt_cd": "1", "msg1": "invalid approval : NOT FOUND"}}))
        await _until(lambda: not h.stream.connected)
        h.now[0] += kis_stream.APPROVAL_MIN_GAP_SEC + 1
        h.stream.want("NVDA")
        await _until(lambda: len(h.sockets) == 2 and h.sockets[1].subs())
        assert len(h.approvals) == 2 and h.sockets[1].subs() == ["DAMSNVDA"]
        task.cancel()

    asyncio.run(go())


def test_failures_stop_once_nobody_watches():
    async def go():
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(403, json={})

        now = [0.0]
        stream = KisStream(APP_KEY, APP_SECRET, connect=lambda url: None,
                           http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=lambda: now[0])
        task = asyncio.create_task(stream.run())
        stream.want("NVDA")
        await _until(lambda: calls)
        now[0] += kis_stream.WANT_SEC + kis_stream.APPROVAL_MIN_GAP_SEC + 1
        await _until(lambda: not stream._demand.is_set())
        await asyncio.sleep(0.1)
        assert calls == [1] and not stream._demand.is_set()
        task.cancel()

    asyncio.run(go())


# --- #143 status ----------------------------------------------------------------------------

def test_status_shows_last_trade_per_market_refusals_and_drops_but_no_keys():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        empty = h.stream.status()
        assert empty["last_tick"] == {"KR": None, "US": None} and empty["last_drop"] is None
        h.stream.want("005930.KS")
        h.stream.want("SOXL")
        h.stream.want("NVDA")
        await _until(lambda: h.sockets and len(h.sockets[0].subs()) == 3)
        sock = h.sockets[0]
        await sock.inbox.put(_frame(KR, _kr()))
        await _until(lambda: h.stream.status()["last_tick"]["KR"] is not None)
        assert h.stream.status()["last_tick"]["US"] is None              # US not traded yet
        await sock.inbox.put(_frame(US, _us()))
        await _until(lambda: h.stream.status()["last_tick"]["US"] is not None)
        await sock.inbox.put(json.dumps({"header": {"tr_id": US, "tr_key": "DAMSNVDA"},
                                         "body": {"rt_cd": "1", "msg1": "MAX SUBSCRIBE OVER"}}))
        await _until(lambda: h.stream.status()["refused"] == ["NVDA"])
        s = h.stream.status()
        assert s["connected"] and s["tickers"] == ["005930.KS", "SOXL"] and s["connected_at"]
        assert s["approval_refused_at"] is None

        await sock.inbox.put(ConnectionError(f"boom {APPROVAL}"))
        await _until(lambda: h.stream.status()["last_drop"] is not None)
        s = h.stream.status()
        assert s["last_drop"]["error"] == "ConnectionError" and s["last_tick"]["KR"]   # kept across the drop
        text = json.dumps(s)
        for secret in (APP_KEY, APP_SECRET, APPROVAL):
            assert secret not in text
        assert s["connected_at"] is None or h.stream.connected   # only for the live session

        # a refused approval key stays visible after the reconnect resets the flag
        h.stream.want("005930.KS")
        await _until(lambda: len(h.sockets) >= 2 and h.sockets[-1].subs())
        await h.sockets[-1].inbox.put(json.dumps({"header": {"tr_id": KR, "tr_key": "005930"},
                                                  "body": {"rt_cd": "1", "msg1": "invalid approval key"}}))
        await _until(lambda: h.stream.status()["approval_refused_at"] is not None)
        await _until(lambda: h.stream.status()["last_drop"]["error"] == "ApprovalError")
        task.cancel()

    asyncio.run(go())


def test_a_planned_key_rotation_is_not_recorded_as_a_drop():
    async def go():
        h = Harness()
        task = asyncio.create_task(h.stream.run())
        h.stream.want("005930.KS")
        await _until(lambda: h.sockets and h.sockets[0].subs())
        h.now[0] += kis_stream.APPROVAL_TTL_SEC + 1
        h.stream.want("005930.KS")
        await _until(lambda: len(h.sockets) >= 2)
        assert h.stream.status()["last_drop"] is None
        task.cancel()

    asyncio.run(go())


def test_status_api_needs_a_session_and_never_returns_keys(db_url):
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner

    def client_for(**keys):
        app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0, **keys), runner=make_fake_runner(0))
        return TestClient(app, base_url="https://testserver")

    first = client_for()                        # migrates the database
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user("owner", "correct horse battery")
    engine.dispose()
    with first as client:
        assert client.get("/api/live/kis").status_code == 401
        client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
        assert client.get("/api/live/kis").json() == {"enabled": False}
    with client_for(kis_app_key=APP_KEY, kis_app_secret=APP_SECRET) as client:
        client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
        body = client.get("/api/live/kis")
        assert body.status_code == 200 and body.json()["enabled"] is True and body.json()["connected"] is False
        assert APP_KEY not in body.text and APP_SECRET not in body.text and APPROVAL not in body.text
