"""#689 a KRX ticker's 1-minute bars collected on demand (desk.prices with desk.demand, namespace "krx_1m"): the api cuts
the late tail of its KRX minute charts (_fill_krx) and the live chart's bars from the 200 the collector read into
Redis, and marks what its screens read so the collector keeps reading it.

Each "process" is its own PriceSource on one fake Redis server; desk.demand is per process, so a test configures it
for the role whose code runs next."""

import asyncio
import time
from dataclasses import asdict
from types import SimpleNamespace

import fakeredis
import pytest

from desk import demand
from desk import shared_cache as sc
from desk.broker import MockBroker
from desk.prices import BROKER_MAX_COUNT, MINUTES_NS, MINUTES_TTL_SEC, PriceSource
from tests.test_prices import _min

KRX = "005930.KS"
YAHOO = [_min("2026-11-13 09:00", 100), _min("2026-11-13 10:00", 101)]


class Toss(MockBroker):
    """Toss' 1-minute bars: the newest ``count`` of 200 from 09:00, each call recorded."""

    name = "toss"

    def __init__(self, shift: float = 0.0, bars: int = 200):
        self.shift, self.bars, self.calls = shift, bars, []

    async def candles(self, symbol, interval, count):
        self.calls.append((symbol, interval, count))
        minutes = [_min(f"2026-11-13 {9 + m // 60:02d}:{m % 60:02d}", 300 + m + self.shift) for m in range(self.bars)]
        return minutes[-count:] if count else []


def _yahoo(ticker, interval, count):
    return list(YAHOO)


class _Down:
    """A Redis client whose server refuses every call."""

    def pipeline(self, transaction=False):
        raise ConnectionError("refused")

    def set(self, *args, **kwargs):
        raise ConnectionError("refused")


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


async def _collected(ticker=KRX, shift=0.0) -> Toss:
    """The collector's demand job read ``ticker``'s 1-minute bars."""
    demand.configure("collector")
    broker = Toss(shift)
    await PriceSource(broker, yahoo_candles=_yahoo).refresh_minutes(ticker)
    return broker


def test_the_api_cuts_its_krx_minute_tail_and_live_bars_from_the_collectors(redis):
    api_broker = Toss(shift=-50)                     # its own read would differ: any bar from it shows

    async def go():
        collector = await _collected()
        shared = (await sc.aget(MINUTES_NS, KRX))[0]
        demand.configure("api")
        api = PriceSource(api_broker, yahoo_candles=_yahoo)
        chart = await api.candles(KRX, "5m", 50)
        live = await api.candles(KRX, "1m", 30)      # desk.live.frame's read
        demand.configure("")                         # one process: its own read, as before
        direct = await PriceSource(Toss(), yahoo_candles=_yahoo).candles(KRX, "5m", 50)
        return collector, shared, chart, live, direct

    collector, shared, chart, live, direct = asyncio.run(go())
    assert collector.calls == [("005930", "1m", BROKER_MAX_COUNT)]
    assert api_broker.calls == []                                  # no Toss 1m call in the api
    assert chart["source"] == direct["source"] == "yahoo+toss"
    assert chart["bars"] == direct["bars"]
    assert live["source"] == "toss" and live["bars"] == shared["bars"][-30:]
    assert live["fetched_at"] == shared["at"]                      # when the bars were read, not now
    assert sc.stats()["skipped"] == 0


def test_the_shared_bars_round_trip_as_plain_json(redis):
    async def go():
        await _collected()
        return await sc.aget(MINUTES_NS, KRX)

    value, age, left = asyncio.run(go())
    bars = asyncio.run(Toss().candles("005930", "1m", BROKER_MAX_COUNT))
    assert value["bars"] == [asdict(b) for b in bars] and len(value["bars"]) == BROKER_MAX_COUNT
    assert isinstance(value["at"], str) and 0 <= age < 5 and 0 < left <= MINUTES_TTL_SEC
    stats = sc.stats()
    assert stats["writes"] == 1 and stats["skipped"] == 0 and stats["rejected"] == 0
    [name] = redis.keys("td:krx_1m:*")
    assert "005930" not in name and "005930" not in redis.get(name)


def test_what_the_api_reads_the_collector_reads_and_the_api_reads_that_next(redis):
    api_broker, collector = Toss(shift=-50), Toss()

    async def go():
        demand.configure("api")
        api = PriceSource(api_broker, yahoo_candles=_yahoo)
        first = await api.candles(KRX, "1m", 30)     # nothing shared yet: its own read, as before
        await demand.flush()                         # the demand_wants job
        demand.configure("collector")
        served = await demand.serve({MINUTES_NS: PriceSource(collector).refresh_minutes})
        demand.configure("api")
        second = await PriceSource(api_broker, yahoo_candles=_yahoo).candles(KRX, "1m", 30)
        return first, served, second

    first, served, second = asyncio.run(go())
    assert served == 1 and collector.calls == [("005930", "1m", BROKER_MAX_COUNT)]
    assert api_broker.calls == [("005930", "1m", 30)]               # the first read only
    assert first["bars"][-1]["close"] == 300 + 199 - 50 and second["bars"][-1]["close"] == 300 + 199


def test_the_api_marks_krx_minute_reads_on_every_read_and_only_for_a_screen(redis):
    async def go():
        demand.configure("api")
        api = PriceSource(Toss(), yahoo_candles=_yahoo)
        await api.candles(KRX, "15m", 10)
        marked = set(demand._seen)
        demand._seen[(MINUTES_NS, KRX)] = 0.0
        await api.candles(KRX, "15m", 10)            # a memory hit marks too: it keeps the collection going
        again = demand._seen[(MINUTES_NS, KRX)] > 0.0
        demand.configure("api")
        for ticker, interval in (("NVDA", "1m"), ("NVDA", "15m"), (KRX, "1d"), ("000660.KS", "1m")):
            token = demand.SCREEN.set(ticker != "000660.KS")    # the last one: a background job's read
            try:
                await api.candles(ticker, interval, 30)
            finally:
                demand.SCREEN.reset(token)
        return marked, again, set(demand._seen)

    marked, again, other = asyncio.run(go())
    assert (MINUTES_NS, KRX) in marked and again
    assert not {k for k in other if k[0] == MINUTES_NS}


def test_a_miss_or_redis_down_reads_toss_as_before(redis):
    async def go():
        demand.configure("api")
        broker = Toss()
        got = await PriceSource(broker, yahoo_candles=_yahoo).candles(KRX, "5m", 50)
        return broker, got

    broker, got = asyncio.run(go())
    assert broker.calls == [("005930", "1m", BROKER_MAX_COUNT)] and got["source"] == "yahoo+toss"
    sc.use(_Down(), _Down())
    broker, got = asyncio.run(go())
    assert broker.calls == [("005930", "1m", BROKER_MAX_COUNT)] and got["source"] == "yahoo+toss"


def test_a_shared_value_that_is_not_bars_from_this_broker_is_a_miss(redis):
    good = {"source": "toss", "at": "2026-11-13T01:00:00+00:00", "bars": [asdict(_min("2026-11-13 09:00", 1))]}
    bar = good["bars"][0]

    async def calls(value) -> int:
        await sc.aput(MINUTES_NS, KRX, value, 30)
        demand.configure("api")
        broker = Toss()
        await PriceSource(broker, yahoo_candles=_yahoo).candles(KRX, "1m", 30)
        return len(broker.calls)

    async def go():
        bad = [{**good, "bars": []}, {**good, "bars": [{**bar, "close": "1"}]}, {**good, "bars": [{**bar, "low": True}]},
               {**good, "at": 5}, {k: v for k, v in good.items() if k != "at"}, {**good, "source": "kis"},
               {**good, "bars": [{**bar, "extra": 1}]}, {**good, "bars": [[1, 2]]}, [good]]
        return [await calls(value) for value in bad], await calls(good)
    misses, hit = asyncio.run(go())
    assert misses == [1] * 9 and hit == 0


def test_an_old_shared_value_is_a_miss(redis, monkeypatch):
    real = time.time
    monkeypatch.setattr(sc, "time", SimpleNamespace(time=lambda: real() - MINUTES_TTL_SEC - 1, monotonic=time.monotonic))
    asyncio.run(_collected())
    monkeypatch.setattr(sc, "time", time)

    async def go():
        demand.configure("api")
        broker = Toss()
        await PriceSource(broker, yahoo_candles=_yahoo).candles(KRX, "1m", 30)
        return broker.calls
    assert asyncio.run(go()) == [("005930", "1m", 30)]


def test_the_collector_job_fails_loudly_for_no_bars_or_a_ticker_not_on_krx(redis):
    async def go():
        demand.configure("collector")
        with pytest.raises(ValueError):
            await PriceSource(Toss(bars=0)).refresh_minutes(KRX)
        broker = Toss()
        with pytest.raises(ValueError):
            await PriceSource(broker).refresh_minutes("NVDA")
        return broker.calls, redis.keys("td:krx_1m:*")
    assert asyncio.run(go()) == ([], [])


def test_us_tickers_never_touch_the_shared_minutes(redis):
    async def go():
        await _collected()                            # something shared, under the KRX ticker
        demand.configure("api")
        before = sc.stats()
        broker = Toss()
        got = await PriceSource(broker, yahoo_candles=_yahoo).candles("NVDA", "1m", 30)
        return before, sc.stats(), broker.calls, got
    before, after, calls, got = asyncio.run(go())
    assert calls == [("NVDA", "1m", 30)] and got["source"] == "toss"
    assert (after["hits"], after["misses"]) == (before["hits"], before["misses"])
