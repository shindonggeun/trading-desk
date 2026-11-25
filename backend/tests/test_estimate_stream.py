"""#999 off-hours estimates collected every period: the collector computes the estimates a screen reads
(desk.estimate.BinanceEstimator.collect, desk.demand's "estimate" namespace), shares them with the api through
Redis (fakeredis) and pushes them on the market stream's quote channel in a slot of their own."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import fakeredis
import httpx
import pytest

from desk import demand, market_bus
from desk import shared_cache as sc
from desk.api import collect_estimate, estimate_row
from desk.estimate import (
    COLLECT_MARKS_SEC,
    SHARED_NS,
    STREAM_SOURCE,
    BinanceEstimator,
    shared_estimate,
)
from desk.market_bus import Bus
from desk.settings import Settings
from tests.test_estimate import CLOSES, MARK, SATURDAY_KST_10AM, WEDNESDAY_KST_10AM, Clock, FakeBinance

MARK_MS = 1_794_000_000_000          # Binance's stamp of the marks (premiumIndex "time")


class StampedBinance(FakeBinance):
    """premiumIndex rows carry Binance's own stamp, as the real one does."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/premiumIndex":
            self.calls.append(request.url.path)
            return httpx.Response(200, json=[{"symbol": k, "markPrice": v, "time": MARK_MS} for k, v in MARK.items()])
        return super().__call__(request)


def _estimator(now=SATURDAY_KST_10AM, closes=CLOSES, clock=None):
    binance = StampedBinance()
    http = httpx.AsyncClient(base_url="https://fapi.test", transport=httpx.MockTransport(binance))
    return BinanceEstimator(http=http, daily_closes=lambda t: closes[t], now=lambda: now,
                            clock=clock or Clock(), after_hours=None), binance


@pytest.fixture(autouse=True)
def demand_reset():
    token = demand.SCREEN.set(True)                  # these reads stand for a screen's (desk.demand.ScreenReads)
    yield
    demand.SCREEN.reset(token)
    demand.configure("")


@pytest.fixture
def redis():
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    sc.use(sync, fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    yield sync
    sc.use(None, None)


def test_the_collector_reads_the_marks_once_a_period_for_all_its_tickers_and_shares_each_estimate(redis):
    demand.configure("collector")
    clock = Clock()
    est, binance = _estimator(clock=clock)

    async def go():
        first = [await est.collect(t) for t in ("NVDA", "BRK-B", "005930.KS")]
        clock.t += COLLECT_MARKS_SEC - 1          # the same period: the marks just read
        await est.collect("NVDA")
        calls = binance.calls.count("/fapi/v1/premiumIndex")
        clock.t += 2                              # the next period reads them again
        await est.collect("NVDA")
        shared = await sc.aget(SHARED_NS, "005930.KS")
        return first, calls, binance.calls.count("/fapi/v1/premiumIndex"), shared

    first, calls, later, shared = asyncio.run(go())
    assert calls == 1 and later == 2
    nvda, mark_at = first[0]
    assert nvda.ticker == "NVDA" and nvda.market_state == "closed" and nvda.contract == "NVDAUSDT"
    assert mark_at == datetime.fromtimestamp(MARK_MS / 1000, UTC).isoformat(timespec="seconds")
    assert shared is not None and shared[0] == first[2][0].to_dict()


def test_an_open_market_or_a_ticker_without_a_contract_is_neither_shared_nor_pushed(redis):
    demand.configure("collector")
    closes = {"005930.KS": [(date(2026, 11, 3), 210000.0), (date(2026, 11, 4), 999999.0)]}
    est, _ = _estimator(WEDNESDAY_KST_10AM, closes)

    async def go():
        return (await est.collect("005930.KS"), await est.collect("ZZZZ"),
                await sc.aget(SHARED_NS, "005930.KS"))

    assert asyncio.run(go()) == (None, None, None)


def test_the_api_answers_from_the_collectors_estimate_and_marks_it_read(redis):
    demand.configure("collector")
    collector, _ = _estimator()
    asyncio.run(collector.collect("NVDA"))
    demand.configure("api")
    api, binance = _estimator()

    got = asyncio.run(api.estimate("NVDA"))
    assert got is not None and got.estimate > 0 and binance.calls == []          # no Binance call of its own
    assert demand._current(demand.time.monotonic()) == {SHARED_NS: ["NVDA"]}


def test_a_shared_value_that_is_not_this_tickers_closed_estimate_is_computed_again(redis):
    demand.configure("collector")
    collector, _ = _estimator()
    nvda, _ = asyncio.run(collector.collect("NVDA"))
    good = nvda.to_dict()
    assert shared_estimate("NVDA", good) == nvda
    for bad in ({**good, "ticker": "AAPL"}, {**good, "market_state": "open"}, {**good, "estimate": float("nan")},
                {**good, "estimate": "209"}, {k: v for k, v in good.items() if k != "contract"},
                {**good, "extra": 1}, [good], {**good, "estimate": 0}, {**good, "anchor_price": float("inf")},
                {**good, "contract_move_pct": "1"}, {**good, "supported": False}, {**good, "method": "guess"},
                {**good, "anchor_source": "other"}, {**good, "contract": "nvda usdt"}, {**good, "basis": {"x": 1}},
                {**good, "basis": "<script>"}, {**good, "close_at": "x" * 41}, {**good, "anchor_at": 5}):
        assert shared_estimate("NVDA", bad) is None

    demand.configure("api")
    asyncio.run(sc.aput(SHARED_NS, "NVDA", {**good, "estimate": None}, 30))
    api, binance = _estimator()
    assert asyncio.run(api.estimate("NVDA")).estimate == nvda.estimate
    assert binance.calls.count("/fapi/v1/premiumIndex") == 1                    # it read the marks itself


def test_the_api_marks_only_a_closed_markets_estimate(redis):
    demand.configure("api")
    closes = {**CLOSES, "005930.KS": [(date(2026, 11, 3), 210000.0), (date(2026, 11, 4), 999999.0)]}
    est, _ = _estimator(WEDNESDAY_KST_10AM, closes)
    assert asyncio.run(est.estimate("005930.KS")).market_state == "open"
    assert asyncio.run(est.estimate("ZZZZ")) is None                       # no contract
    assert demand._current(demand.time.monotonic()) == {}
    saturday, _ = _estimator()
    asyncio.run(saturday.estimate("NVDA"))
    assert demand._current(demand.time.monotonic()) == {SHARED_NS: ["NVDA"]}


def test_the_collector_looks_up_tickers_only(redis):
    demand.configure("collector")
    est, binance = _estimator()
    assert asyncio.run(est.collect("../X")) is None and asyncio.run(est.collect("A B")) is None
    assert binance.calls == []


def test_one_process_or_no_redis_never_reads_a_shared_estimate(redis):
    demand.configure("all")
    asyncio.run(sc.aput(SHARED_NS, "NVDA", {"anything": 1}, 30))
    est, binance = _estimator()
    assert asyncio.run(est.estimate("NVDA")) is not None
    assert binance.calls.count("/fapi/v1/premiumIndex") == 1
    assert demand._current(demand.time.monotonic()) == {}


def _local_bus() -> Bus:
    bus = Bus()
    bus.configure(Settings(database_url="sqlite://"), "all")
    return bus


def _drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


def test_the_collected_estimate_goes_out_as_a_quote_with_the_boards_row(redis, monkeypatch):
    demand.configure("collector")
    est, _ = _estimator()

    async def session_of(ticker):
        return "closed"

    async def go():
        bus = _local_bus()
        monkeypatch.setattr(market_bus, "BUS", bus)
        sub = bus.subscribe(("quote",), frozenset({"NVDA"}), owner=False)
        await collect_estimate(est, session_of, "NVDA")
        got = _drain(sub)
        await bus.aclose()
        return got

    [event] = asyncio.run(go())
    nvda = shared_estimate("NVDA", asyncio.run(sc.aget(SHARED_NS, "NVDA"))[0])
    assert event["kind"] == "quote" and event["key"] == "NVDA" and event["source"] == STREAM_SOURCE
    assert event["source_at"] == datetime.fromtimestamp(MARK_MS / 1000, UTC).isoformat(timespec="seconds")
    assert event["payload"] == {**estimate_row("NVDA", "closed", nvda), "close_at": nvda.close_at}
    assert event["payload"]["state"] == "estimate" and event["payload"]["quote_source"] == "binance-estimate"
    assert set(event["payload"]) == {"ticker", "close", "close_date", "close_at", "session", "source", "state",
                                     "price", "change_pct", "quote_source", "fetched_at"}


def test_a_session_the_brokers_calendar_has_open_pushes_no_estimate(redis, monkeypatch):
    demand.configure("collector")
    est, _ = _estimator()

    async def regular(ticker):
        return "regular"

    async def go():
        bus = _local_bus()
        monkeypatch.setattr(market_bus, "BUS", bus)
        sub = bus.subscribe(("quote",), frozenset({"NVDA"}), owner=False)
        await collect_estimate(est, regular, "NVDA")
        got = _drain(sub)
        await bus.aclose()
        return got

    assert asyncio.run(go()) == []


def test_an_estimate_and_a_live_quote_of_one_ticker_keep_their_own_slots():
    async def go():
        bus = _local_bus()
        sub = bus.subscribe(("quote",), frozenset({"AAPL"}), owner=False)
        bus.publish("quote", "AAPL", {"price": 1.0}, "kis")
        bus.publish("quote", "AAPL", {"price": 2.0, "state": "estimate"}, STREAM_SOURCE)   # the same second
        sent = [(e["source"], e["payload"]["price"]) for e in _drain(sub)]
        bus.publish("quote", "AAPL", {"price": 3.0}, "kis")                              # waits its second
        latest = sorted((e["source"], e["payload"]["price"]) for e in bus.snapshot(sub))
        await bus.aclose()
        return sent, latest

    sent, latest = asyncio.run(go())
    assert sent == [("kis", 1.0), (STREAM_SOURCE, 2.0)]
    assert latest == [(STREAM_SOURCE, 2.0), ("kis", 1.0)]


def test_a_reconnecting_stream_gets_only_a_fresh_estimate(monkeypatch):
    async def go():
        bus = _local_bus()
        sub = bus.subscribe(("quote",), frozenset({"AAPL", "NVDA"}), owner=False)
        bus.publish("quote", "AAPL", {"price": 2.0}, STREAM_SOURCE)
        bus.publish("quote", "NVDA", {"price": 4.0}, "kis")
        bus.publish("quote", "NVDA", {"price": 3.0}, STREAM_SOURCE)
        old = bus._latest[("quote", "AAPL" + market_bus.ESTIMATE_SLOT)]
        stale = (datetime.now(UTC) - timedelta(seconds=market_bus.ESTIMATE_REPLAY_SEC + 1)).isoformat(
            timespec="milliseconds")
        bus._latest[("quote", "AAPL" + market_bus.ESTIMATE_SLOT)] = {**old, "observed_at": stale}
        got = sorted((e["key"], e["source"]) for e in bus.snapshot(sub))
        await bus.aclose()
        return got

    assert asyncio.run(go()) == [("NVDA", STREAM_SOURCE), ("NVDA", "kis")]


def test_off_hours_estimates_keep_a_ticker_off_the_stale_list():
    async def go():
        clock = [0.0]
        bus = Bus(clock=lambda: clock[0])
        bus.configure(Settings(database_url="sqlite://"), "all")
        sub = bus.subscribe(("quote",), frozenset({"AAPL"}), owner=False)
        before = bus.degraded(sub)
        bus.publish("quote", "AAPL", {"price": 2.0}, STREAM_SOURCE)
        after = bus.degraded(sub)
        clock[0] += market_bus.QUOTE_STALE_SEC + 1
        quiet = bus.degraded(sub)
        await bus.aclose()
        return before, after, quiet

    assert asyncio.run(go()) == ((["quote"], "stale"), ([], None), (["quote"], "stale"))


def test_a_market_stream_marks_its_off_hours_contract_tickers_read(db_url, monkeypatch):
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from tests.test_api import OWNER, PASSWORD
    from tests.test_market_stream import _frames

    est, binance = _estimator()
    est._us = {"AAPL": "AAPLUSDT", "NVDA": "NVDAUSDT"}       # the contract list as last read
    monkeypatch.setattr(market_bus, "BUS", Bus())
    monkeypatch.setattr(demand, "enabled", lambda: True)                       # the api of split roles
    monkeypatch.setattr("desk.api.trading_soon", lambda ticker, now, lead=None: ticker == "NVDA")  # NVDA trades
    monkeypatch.setattr("desk.sessions.kr_clock", lambda now: "closed")         # no NXT session now
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0),
                     estimator=est)
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with TestClient(app, base_url="https://testserver") as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        with client.stream("GET", "/api/stream/market?channels=flow&tickers=AAPL") as r:     # no quote channel
            _frames(r.iter_lines(), "ready")
        before = demand._current(demand.time.monotonic()).get(SHARED_NS, [])
        with client.stream("GET", "/api/stream/market?channels=quote&tickers=AAPL,NVDA,005930.KS,ZZZZ,SQQQ,"
                                  "%5ENDX") as r:
            _frames(r.iter_lines(), "ready")
        wanted = demand._current(demand.time.monotonic()).get(SHARED_NS, [])
    assert before == []
    # NVDA trades now; ZZZZ has no contract; SQQQ (QQQ x -3) and ^NDX (QQQ) follow a contract not in the list read
    assert sorted(wanted) == ["005930.KS", "AAPL"]
    assert binance.calls == []                               # the stream's loop never waits on Binance


def test_a_contract_known_from_what_was_read():
    est, binance = _estimator()
    assert est.known_contract("005930.KS") and est.known_contract("CL=F") and est.known_contract("^KS200")
    assert not est.known_contract("NVDA")                    # the list is not read yet: no, and no call
    est._us = {"QQQ": "QQQUSDT", "BRKB": "BRKBUSDT"}
    assert est.known_contract("BRK-B") and est.known_contract("^NDX") and est.known_contract("SQQQ")
    assert not est.known_contract("ZZZZ") and not est.known_contract("^VIX") and binance.calls == []


def test_the_stream_and_the_collector_agree_on_the_estimates_source_and_life():
    from desk import estimate

    assert market_bus.ESTIMATE_SOURCE == estimate.STREAM_SOURCE
    assert market_bus.ESTIMATE_REPLAY_SEC == estimate.SHARED_KEEP_SEC
    assert demand.EVERY[estimate.SHARED_NS] < 10 < estimate.SHARED_KEEP_SEC


def test_an_estimate_older_than_the_tickers_live_quote_is_not_replayed():
    async def go():
        bus = _local_bus()
        sub = bus.subscribe(("quote",), frozenset({"AAPL", "NVDA"}), owner=False)
        bus.publish("quote", "AAPL", {"price": 1.0}, "kis")                # yesterday's session: first in
        bus.publish("quote", "AAPL", {"price": 2.0}, STREAM_SOURCE)        # off hours
        bus.publish("quote", "NVDA", {"price": 3.0}, STREAM_SOURCE)
        await asyncio.sleep(market_bus.QUOTE_GAP_SEC + 0.1)
        bus.publish("quote", "AAPL", {"price": 4.0}, "kis")                # the market opened
        await asyncio.sleep(market_bus.QUOTE_GAP_SEC + 0.1)                 # its limit let it out
        got = sorted((e["key"], e["source"], e["payload"]["price"]) for e in bus.snapshot(sub))
        await bus.aclose()
        return got

    assert asyncio.run(go()) == [("AAPL", "kis", 4.0), ("NVDA", STREAM_SOURCE, 3.0)]


def test_the_quote_source_lag_leaves_the_estimates_mark_age_out():
    async def go():
        bus = _local_bus()
        old = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
        bus.publish("quote", "AAPL", {"price": 2.0}, STREAM_SOURCE, old)
        stats = bus.stats()
        await bus.aclose()
        return stats

    stats = asyncio.run(go())
    assert stats["quote"] == 1 and "quote" not in stats["source_lag_sec"]


def test_estimates_take_at_most_their_share_of_the_wanted_set(monkeypatch):
    monkeypatch.setattr(demand, "enabled", lambda: True)
    demand.configure("api")
    for i in range(demand.NS_MAX[SHARED_NS] + 5):
        demand.want(SHARED_NS, f"T{i}")
    demand.want("quote", "AAPL")
    wanted = demand._current(demand.time.monotonic())
    assert len(wanted[SHARED_NS]) == demand.NS_MAX[SHARED_NS] and wanted["quote"] == ["AAPL"]
    assert demand.stats()["dropped"] == 5


def test_a_market_stream_marks_again_at_its_heartbeat_and_leaves_korean_names_while_nxt_trades(db_url, monkeypatch):
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from desk import sessions
    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from tests.test_api import OWNER, PASSWORD
    from tests.test_market_stream import _frames

    est, _ = _estimator()
    est._us = {"AAPL": "AAPLUSDT"}
    monkeypatch.setattr(market_bus, "BUS", Bus())
    monkeypatch.setattr(market_bus, "HEARTBEAT_SEC", 0.05)
    monkeypatch.setattr(demand, "enabled", lambda: True)
    monkeypatch.setattr("desk.api.trading_soon", lambda ticker, now, lead=None: False)
    monkeypatch.setattr(sessions, "kr_clock", lambda now: "post")              # NXT after-market trades
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0),
                     estimator=est)
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with TestClient(app, base_url="https://testserver") as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        with client.stream("GET", "/api/stream/market?channels=quote&tickers=AAPL,005930.KS") as r:
            lines = r.iter_lines()
            _frames(lines, "ready")
            first = demand._current(demand.time.monotonic()).get(SHARED_NS, [])
            demand.configure("api")                                             # forget: the heartbeat marks again
            _frames(lines, "heartbeat")
            again = demand._current(demand.time.monotonic()).get(SHARED_NS, [])
    assert first == again == ["AAPL"]


def test_demand_passes_start_every_period_and_keys_fall_due_together(redis, monkeypatch):
    from desk.api import DEMAND_PAUSE_MIN_SEC, DEMAND_SERVE_SEC, demand_pause

    assert demand_pause(0.2) == DEMAND_SERVE_SEC - 0.2                     # a short pass: the rest of the period
    assert demand_pause(DEMAND_SERVE_SEC + 3) == DEMAND_PAUSE_MIN_SEC     # a long one: never back to back

    demand.configure("collector")
    monkeypatch.setattr(demand, "PARALLEL", 1)

    async def slow(ticker):
        await asyncio.sleep(0.3)

    async def go():
        await sc.aput(demand.WANT_NS, demand.WANT_KEY, {SHARED_NS: ["A", "B", "C"]}, demand.WANT_TTL)
        before = demand.time.monotonic()
        served = await demand.serve({SHARED_NS: slow})
        return before, served, dict(demand._due)

    before, served, due = asyncio.run(go())
    assert served == 3                                                     # one after another, ~0.9 s in all
    times = [due[(SHARED_NS, k)] for k in ("A", "B", "C")]
    assert max(times) - min(times) < 0.05                                  # due from the pass's start, not each end
    assert abs(times[0] - (before + demand.EVERY[SHARED_NS])) < 0.1
