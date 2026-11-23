"""#690 the market stream (desk.market_bus, GET /api/stream/market): events in this process, the same over
Redis pub/sub between processes (fakeredis), and the SSE contract of docs/market-stream.md."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from desk import market_alerts, market_bus
from desk import shared_cache as sc
from desk.market_bus import Bus
from desk.settings import Settings


def _settings(**kw):
    return Settings(database_url="sqlite://", **kw)


def _run(coro):
    return asyncio.run(coro)


def _local(clock=None) -> Bus:
    bus = Bus(clock=clock) if clock else Bus()
    bus.configure(_settings(), "all")
    return bus


def test_owner_only_kinds_match_the_alerts():
    assert market_bus.OWNER_ONLY == frozenset(market_alerts.OWNER_ONLY)


def test_events_reach_the_streams_that_asked_for_them():
    async def go():
        bus = _local()
        flows = bus.subscribe(("flow",), frozenset(), owner=False)
        quotes = bus.subscribe(("quote", "alert"), frozenset({"AAPL"}), owner=False)
        owner = bus.subscribe(("alert",), frozenset(), owner=True)
        bus.publish("flow", "KOSPI:1d", {"records": []}, "toss_investors")
        bus.publish("quote", "AAPL", {"price": 1.0}, "toss")
        bus.publish("quote", "MSFT", {"price": 2.0}, "toss")              # not asked for
        bus.publish("alert", "holding_alert", {"text": "손절"}, "desk")    # the owner's only
        bus.publish("alert", "flow_shift", {"text": "수급"}, "desk")
        got = {name: [e["key"] for e in _drain(sub)] for name, sub in
               (("flows", flows), ("quotes", quotes), ("owner", owner))}
        event = bus.snapshot(flows)[0]
        await bus.aclose()
        return got, event

    got, event = _run(go())
    assert got == {"flows": ["KOSPI:1d"], "quotes": ["AAPL", "flow_shift"], "owner": ["holding_alert", "flow_shift"]}
    assert set(event) == {"v", "kind", "key", "event_id", "source", "source_at", "observed_at", "payload"}
    assert event["v"] == 1 and event["observed_at"].endswith("+00:00")


def _drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


def test_one_tickers_quote_goes_out_at_most_once_a_second_newest_first():
    async def go():
        bus = _local()
        sub = bus.subscribe(("quote",), frozenset({"AAPL"}), owner=False)
        for price in (1.0, 2.0, 3.0):
            bus.publish("quote", "AAPL", {"price": price}, "kis")
        first = [e["payload"]["price"] for e in _drain(sub)]
        await asyncio.sleep(market_bus.QUOTE_GAP_SEC + 0.2)
        later = [e["payload"]["price"] for e in _drain(sub)]
        await bus.aclose()
        return first, later

    assert _run(go()) == ([1.0], [3.0])


def test_distinct_alerts_observed_in_the_same_millisecond_have_distinct_ids(monkeypatch):
    from datetime import UTC, datetime

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 20, 1, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(market_bus, "datetime", FixedDatetime)

    async def go():
        bus = _local()
        sub = bus.subscribe(("alert",), frozenset(), owner=False)
        bus.publish("alert", "flow_shift", {"market": "KOSPI"}, "desk")
        bus.publish("alert", "flow_shift", {"market": "KOSDAQ"}, "desk")
        first, second = _drain(sub)
        await bus.aclose()
        assert first["observed_at"] == second["observed_at"]
        assert first["event_id"] != second["event_id"]

    _run(go())


def test_quote_fallback_depends_on_the_tickers_the_stream_requested():
    async def go():
        clock = [0.0]
        bus = _local(lambda: clock[0])
        sub = bus.subscribe(("quote",), frozenset({"AAPL", "NVDA"}), owner=False)
        bus.publish("quote", "AAPL", {"price": 1}, "kis")
        assert bus.degraded(sub) == (["quote"], "stale")
        bus.publish("quote", "NVDA", {"price": 2}, "kis")
        assert bus.degraded(sub) == ([], None)
        clock[0] += market_bus.QUOTE_STALE_SEC + 1
        bus.publish("quote", "AAPL", {"price": 3}, "kis")
        assert bus.degraded(sub) == (["quote"], "stale")
        await bus.aclose()

    _run(go())


def test_a_stream_that_falls_behind_is_marked_for_reset(monkeypatch):
    monkeypatch.setattr(market_bus, "QUEUE_MAX", 2)

    async def go():
        bus = _local()
        slow = bus.subscribe(("flow",), frozenset(), owner=False)
        for i in range(5):
            bus.publish("flow", f"K{i}:1d", {}, "toss_investors")
        stats = bus.stats()
        await bus.aclose()
        return slow.overflowed, stats["overflows"]

    assert _run(go()) == (True, 1)


def test_status_reports_source_lag_only_for_events_the_source_stamped():
    """#690 /api/status: source_lag_sec is the source's stamp -> observed, per kind, from stamped events only."""
    async def go():
        bus = _local()
        stamped = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
        naive = (datetime.now(UTC) - timedelta(seconds=10)).replace(tzinfo=None).isoformat()   # read as UTC
        bus.publish("flow", "KOSPI:1d", {}, "toss_investors", stamped)
        bus.publish("flow", "KOSDAQ:1d", {}, "toss_investors", naive)
        bus.publish("futures", "view", {}, "kis_futures")
        stats = bus.stats()
        await bus.aclose()
        return stats

    stats = _run(go())
    assert set(stats["source_lag_sec"]) == {"flow"} and stats["source_lag_sec"]["flow"]["n"] == 2
    assert 9 <= stats["source_lag_sec"]["flow"]["p50"] <= 11 and 29 <= stats["source_lag_sec"]["flow"]["p95"] <= 31
    assert set(stats["lag_sec"]) == {"flow", "futures"}


def test_a_channel_quiet_while_its_session_is_open_is_degraded():
    clock = [1000.0]

    async def go():
        bus = _local(clock=lambda: clock[0])
        live = [True]
        bus.expect("flow", lambda: live[0])
        out = [bus.degraded()]
        clock[0] += market_bus.STALE_SEC + 1
        out.append(bus.degraded())
        bus.publish("flow", "KOSPI:1d", {}, "toss_investors")
        out.append(bus.degraded())
        clock[0] += market_bus.STALE_SEC + 1
        live[0] = False                                   # closed: quiet is normal
        out.append(bus.degraded())
        await bus.aclose()
        return out

    assert _run(go()) == [([], None), (["flow"], "stale"), ([], None), ([], None)]


def test_off_publishes_nothing():
    async def go():
        bus = Bus()
        bus.configure(_settings(market_stream=False), "all")
        sub = bus.subscribe(("flow",), frozenset(), owner=False)
        bus.publish("flow", "KOSPI:1d", {}, "toss_investors")
        return bus.on, sub.queue.qsize()

    assert _run(go()) == (False, 0)
    assert Settings(database_url="sqlite://").market_stream is True


def test_a_publish_from_a_worker_thread_reaches_the_loop():
    async def go():
        bus = _local()
        sub = bus.subscribe(("alert",), frozenset(), owner=False)
        await asyncio.to_thread(bus.publish, "alert", "flow_shift", {"text": "x"}, "desk")
        event = await asyncio.wait_for(sub.queue.get(), 1)
        await bus.aclose()
        return event["key"]

    assert _run(go()) == "flow_shift"


# -- split processes over Redis pub/sub -----------------------------------------------------------


@pytest.fixture
def redis_pair(monkeypatch):
    server = fakeredis.FakeServer()
    sc.use(fakeredis.FakeRedis(server=server, decode_responses=True),
           fakeredis.FakeAsyncRedis(server=server, decode_responses=True), "bus-test-key-0123456789")
    monkeypatch.setattr(sc, "listener", lambda settings: fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    monkeypatch.setattr(market_bus, "LISTEN_POLL_SEC", 0.01)   # fakeredis waits for a message without yielding
    yield server
    sc.use(None, None)


async def _wait(predicate, seconds=3.0):
    for _ in range(int(seconds / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def test_the_collectors_events_reach_the_api_over_redis_sealed(redis_pair):
    async def go():
        api, collector = Bus(), Bus()
        api.configure(_settings(), "api")
        collector.configure(_settings(), "collector")
        assert (api.mode, api.subscribe_remote, collector.mode, collector.subscribe_remote) == \
            ("redis", True, "redis", False)
        sub = api.subscribe(("flow", "alert"), frozenset(), owner=True)
        await _wait(lambda: api.stats()["subscribed"])
        await asyncio.sleep(0.1)
        spy = fakeredis.FakeAsyncRedis(server=redis_pair, decode_responses=True).pubsub()
        await spy.subscribe(sc.channel("market"))
        collector.publish("flow", "KOSPI:1d", {"records": [{"date": "2026-11-20"}]}, "toss_investors")
        collector.publish("alert", "holding_alert", {"text": "005930 손절"}, "desk")
        assert await _wait(lambda: sub.queue.qsize() == 2)
        got = _drain(sub)
        raw = [m async for m in _messages(spy, 2)]
        stats = api.stats()
        await api.aclose()
        await collector.aclose()
        return got, raw, stats

    got, raw, stats = _run(go())
    assert [e["key"] for e in got] == ["KOSPI:1d", "holding_alert"] and "origin" not in got[0]
    assert got[0]["payload"] == {"records": [{"date": "2026-11-20"}]}
    assert raw and all("005930" not in r and "손절" not in r for r in raw)       # sealed on the shared server
    assert sc.channel("market").startswith("td:bus:") and "market" not in sc.channel("market")
    assert stats["received_remote"] == 2 and stats["lag_sec"]["flow"]["n"] == 1


async def _messages(pubsub, n):
    seen = 0
    for _ in range(100):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
        if message:
            seen += 1
            yield message["data"]
            if seen == n:
                return


def test_the_api_ignores_its_own_echo_and_anything_not_sealed_by_this_app(redis_pair):
    async def go():
        api = Bus()
        api.configure(_settings(), "api")
        sub = api.subscribe(("flow", "quote"), frozenset({"AAPL"}), owner=False)
        await _wait(lambda: api.stats()["subscribed"])
        await asyncio.sleep(0.1)
        api.publish("quote", "AAPL", {"price": 1.0}, "kis")            # local delivery once, echo dropped
        writer = fakeredis.FakeAsyncRedis(server=redis_pair, decode_responses=True)
        fake = {"v": 1, "kind": "flow", "key": "KOSPI:1d", "event_id": "x", "source": "toss_investors",
                "source_at": None, "observed_at": "2026-11-20T00:00:00+00:00", "payload": {"records": []}}
        await writer.publish(sc.channel("market"), json.dumps(fake))           # plain JSON from someone else
        await writer.publish(sc.channel("market"), sc.seal({**fake, "v": 9}))  # sealed, unknown version
        assert await _wait(lambda: api.stats()["dropped"] == 2)
        await asyncio.sleep(0.1)
        keys = [e["key"] for e in _drain(sub)]
        await api.aclose()
        return keys

    assert _run(go()) == ["AAPL"]


def test_redis_and_local_quotes_share_the_same_limit_without_relaying_received_quotes(redis_pair):
    async def go():
        clock = [0.0]
        api, collector = Bus(clock=lambda: clock[0]), Bus()
        api.configure(_settings(), "api")
        collector.configure(_settings(), "collector")
        sub = api.subscribe(("quote",), frozenset({"AAPL"}), owner=False)
        try:
            assert await _wait(lambda: api.stats()["subscribed"])
            api.publish("quote", "AAPL", {"price": 1}, "kis")
            collector.publish("quote", "AAPL", {"price": 2}, "toss")
            assert await _wait(lambda: api.stats()["received_remote"] == 1)
            assert [e["payload"]["price"] for e in _drain(sub)] == [1]
            event = await asyncio.wait_for(sub.queue.get(), 3)
            assert event["payload"]["price"] == 2
            assert api.stats()["published_remote"] == 1
            clock[0] += market_bus.QUOTE_GAP_SEC + 1
            delayed = {**event, "observed_at": "2000-01-01T00:00:00.000+00:00", "payload": {"price": 0.5}}
            await sc.publisher().publish(sc.channel("market"), sc.seal(delayed))
            assert await _wait(lambda: api.stats()["received_remote"] == 2)
            assert sub.queue.empty()
            assert api.snapshot(sub) == [event]
        finally:
            await api.aclose()
            await collector.aclose()

    _run(go())


def test_a_split_api_without_redis_keeps_the_remote_channels_in_fallback():
    async def go():
        api = Bus()
        api.configure(_settings(), "api")
        try:
            assert api.mode == "local"
            assert api.degraded() == (["alert", "flow", "futures"], "bus_down")
        finally:
            await api.aclose()

    _run(go())


def test_a_lost_subscription_degrades_the_remote_channels_and_comes_back(redis_pair, monkeypatch):
    class Down:
        def pubsub(self, **_):
            raise ConnectionError("refused")

        async def aclose(self):
            pass

    monkeypatch.setattr(sc, "listener", lambda settings: Down())
    monkeypatch.setattr(market_bus, "RECONNECT_MAX_SEC", 0.05)

    async def go():
        api = Bus()
        api.configure(_settings(), "api")
        assert await _wait(lambda: api.stats()["reconnects"] >= 1)
        degraded = api.degraded()
        await api.aclose()
        return degraded

    assert _run(go()) == (["alert", "flow", "futures"], "bus_down")


def test_one_process_never_touches_redis_even_when_it_is_configured(redis_pair):
    async def go():
        bus = Bus()
        bus.configure(_settings(), "all")
        return bus.mode, bus.subscribe_remote

    assert _run(go()) == ("local", False)


# -- the SSE route -----------------------------------------------------------------------------------


def _frames(lines, until):
    """(event, data) pairs up to and including the first ``until`` event (``lines``: one response's iterator)."""
    out, event = [], None
    for line in lines:
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[6:])))
            if event == until:
                return out
    return out


def test_the_route_needs_a_session_and_checks_its_query(db_url):
    from tests.test_api import OWNER, PASSWORD
    from tests.test_live import Prices
    from tests.test_prices import _client

    with _client(db_url, Prices(), fake_runs=True) as client:
        assert client.get("/api/stream/market").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/stream/market?channels=quote,trades").status_code == 422
        assert client.get("/api/stream/market?channels=quote&tickers=NV%20DA").status_code == 422


def test_the_route_is_503_when_the_stream_is_off(db_url):
    from tests.test_api import OWNER, PASSWORD
    from tests.test_live import Prices
    from tests.test_prices import _client

    with _client(db_url, Prices(), fake_runs=True, market_stream=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/stream/market").status_code == 503


def test_concurrent_market_streams_share_the_session_limit(db_url, monkeypatch):
    from threading import Barrier
    from types import SimpleNamespace

    from fastapi import HTTPException, Request
    from starlette.responses import StreamingResponse

    from desk.auth import COOKIE
    from tests.test_live import Prices
    from tests.test_prices import _client

    with _client(db_url, Prices(), fake_runs=True) as client:
        owner_checks = Barrier(2, timeout=5)
        monkeypatch.setattr(client.app.state.market_alerts, "owner", lambda: owner_checks.wait() + 10)
        monkeypatch.setattr("desk.api.MARKET_STREAMS_PER_SESSION", 1)
        endpoint = next(route.endpoint for route in client.app.routes if route.path == "/api/stream/market")

        async def open_stream():
            request = Request({"type": "http", "headers": [(b"cookie", f"{COOKIE}=same-session".encode())]})
            request.state.user = SimpleNamespace(id=1)
            return await endpoint(request, channels="alert")

        async def go():
            results = await asyncio.gather(open_stream(), open_stream(), return_exceptions=True)
            responses = [result for result in results if isinstance(result, StreamingResponse)]
            try:
                assert len(responses) == 1
                assert [r.status_code for r in results if isinstance(r, HTTPException)] == [429]
            finally:
                for response in responses:
                    await anext(response.body_iterator)
                    await response.body_iterator.aclose()

        client.portal.call(go)


def test_the_route_sends_the_latest_values_then_ready_then_events(db_url, monkeypatch):
    from tests.test_api import OWNER, PASSWORD
    from tests.test_live import Prices
    from tests.test_prices import _client

    monkeypatch.setattr(market_bus, "BUS", Bus())
    monkeypatch.setattr(market_bus, "HEARTBEAT_SEC", 0.05)
    with _client(db_url, Prices(), fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        market_bus.publish("futures", "view", {"price": 400.5}, "kis_futures")
        market_bus.publish("quote", "NVDA", {"price": 200.0}, "kis")
        market_bus.publish("quote", "AAPL", {"price": 100.0}, "kis")
        assert _wait_sync(lambda: market_bus.BUS.stats()["quote"] == 2)
        with client.stream("GET", "/api/stream/market?channels=futures,quote,alert&tickers=NVDA") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-accel-buffering"] == "no"
            lines = response.iter_lines()
            first = _frames(lines, "ready")
            market_bus.publish("alert", "flow_shift", {"text": "외국인 순매도 전환"}, "desk")
            after = _frames(lines, "alert")
            assert _frames(lines, "heartbeat")[-1][0] == "heartbeat"
        status = client.get("/api/status").json()["market_stream"]
        monkeypatch.setattr("desk.api.MARKET_STREAMS_PER_SESSION", 0)
        assert client.get("/api/stream/market").status_code == 429
    kinds = [e for e, _ in first]
    assert sorted(kinds[:-1]) == ["futures", "quote"] and kinds[-1] == "ready"     # AAPL was not asked for
    ready = first[-1][1]
    assert ready["v"] == 1 and ready["channels"] == ["futures", "quote", "alert"] and ready["bus"] == "local"
    assert after[-1][1]["payload"] == {"text": "외국인 순매도 전환"} and after[-1][1]["kind"] == "alert"
    assert status["on"] is True and status["mode"] == "local" and status["alert"] == 1


def _wait_sync(predicate, seconds=3.0):
    import time

    for _ in range(int(seconds / 0.02)):
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_a_market_alert_is_pushed_after_it_is_recorded(db_url):
    from datetime import UTC, datetime

    from desk.db import make_engine, metadata

    engine = make_engine(db_url)
    metadata.create_all(engine)

    async def go():
        bus = market_bus.BUS
        bus.configure(_settings(), "all")
        sub = bus.subscribe(("alert",), frozenset(), owner=False)
        alerts = market_alerts.MarketAlerts(engine, None)
        alert = {"kind": "nq_alert", "rule": "nq_drop", "market": "nq", "who": "all", "severity": "caution",
                 "t": datetime(2026, 11, 20, 1, 0, tzinfo=UTC).isoformat(), "value": -0.5, "prev": -0.3}
        await asyncio.to_thread(alerts.dispatch, [alert])
        await asyncio.to_thread(alerts.dispatch, [alert])            # refused the second time: not pushed again
        await asyncio.sleep(0.05)
        got = _drain(sub)
        await bus.aclose()
        return got

    got = _run(go())
    engine.dispose()
    assert len(got) == 1 and got[0]["key"] == "nq_alert"
    assert set(got[0]["payload"]) == set(market_alerts.STREAM_FIELDS) and got[0]["payload"]["text"]


# -- where events come from ---------------------------------------------------------------------------


def test_a_fresh_flow_futures_and_quote_read_are_published_a_failed_one_is_not(monkeypatch):
    from datetime import UTC, datetime

    from desk import futures_flow as ff
    from desk.market_flow import MarketFlow
    from desk.prices import PriceSource
    from tests.test_futures_flow import _rest
    from tests.test_market_flow import CountingBroker
    from tests.test_shared_cache import NOW, _QuoteBroker

    async def go():
        bus = _local()
        monkeypatch.setattr(market_bus, "BUS", bus)
        sub = bus.subscribe(("flow", "futures", "quote"), frozenset({"AAPL"}), owner=False)
        await MarketFlow(CountingBroker(), now=lambda: NOW).get("KOSPI", "1d", 1, fresh=True)
        await MarketFlow(CountingBroker(fail=True), now=lambda: NOW).get("KOSDAQ", "1d", 1, fresh=True)
        now = lambda: datetime(2026, 11, 6, 2, 0, tzinfo=UTC)
        await ff.FuturesFlow(_rest([]), None, now=now).get(fresh=True)
        await PriceSource(_QuoteBroker()).quote("AAPL")
        got = _drain(sub)
        await bus.aclose()
        return got

    got = _run(go())
    assert [(e["kind"], e["key"]) for e in got] == [("flow", "KOSPI:1d"), ("futures", "view"), ("quote", "AAPL")]
    flow = got[0]
    assert flow["source"] == "toss_investors" and "freshness" in flow["payload"] and "premarket" not in flow["payload"]
    assert len(flow["payload"]["records"]) <= 20
    assert got[2]["payload"]["price"] == 101.5 and got[2]["source"] == "mock"


def test_a_kis_socket_trade_reaches_the_quote_callback():
    from desk.prices import kis_quote
    from tests.test_kis_stream import KR, Harness, _frame, _kr, _until

    async def go():
        harness = Harness()
        stream = harness.stream
        seen = []
        stream.on_tick = lambda ticker, tick: seen.append(kis_quote(ticker, tick).to_dict())
        task = asyncio.create_task(stream.run())
        try:
            stream.want("005930.KS")
            await _until(lambda: harness.sockets and harness.sockets[0].subs())
            socket = harness.sockets[0]
            await socket.inbox.put(_frame(KR, _kr(price="71000"), _kr("000660")))
            await _until(lambda: stream.latest("005930.KS") is not None)
            tick = stream.latest("005930.KS")
            assert seen == [{"ticker": "005930.KS", "price": 71000.0, "currency": "KRW", "source": "kis",
                             "fetched_at": tick.received_at}]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    _run(go())


def test_a_holding_alert_goes_to_the_owner_only_without_its_figures(db_url):
    from desk import holding_watch
    from desk.db import make_engine, metadata
    from tests.test_holding_watch import NOW, _lines, _range

    engine = make_engine(db_url)
    metadata.create_all(engine)
    _, lines = _lines(_range(), average=97.0)
    candidates = holding_watch.judge("AAA", lines["stop"] * 0.99, 97.0, lines, NOW)
    assert [alert["rule"] for alert in candidates] == ["hold_stop"]
    assert candidates[0]["value"] is not None and candidates[0]["prev"] is not None

    async def go():
        bus = market_bus.BUS
        bus.configure(_settings(), "all")
        owner = bus.subscribe(("alert",), frozenset(), owner=True)
        other = bus.subscribe(("alert",), frozenset(), owner=False)
        alerts = market_alerts.MarketAlerts(engine, None)
        await asyncio.to_thread(alerts.dispatch, candidates)
        await asyncio.sleep(0.05)
        got = _drain(owner), _drain(other)
        await bus.aclose()
        return got

    mine, theirs = _run(go())
    engine.dispose()
    assert [e["key"] for e in mine] == ["holding_alert"] and theirs == []
    assert mine[0]["payload"]["value"] is None and mine[0]["payload"]["prev"] is None
