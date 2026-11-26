"""#689 a KRX ticker's 1-minute bars collected on demand (desk.prices with desk.demand, namespace "krx_1m"): the api cuts
the late tail of its KRX minute charts (_fill_krx) and the live chart's bars from the 200 the collector read into
Redis. Only a screen reading the 1-minute bars on and on (the live chart, a 1m chart) during the session marks them
for the collector - a 5m-240m chart read once a minute or two must not cost more Toss calls than its own reads did.

Each "process" is its own PriceSource on one fake Redis server; desk.demand is per process, so a test configures it
for the role whose code runs next."""

import asyncio
import time
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

import fakeredis
import pytest

from desk import demand
from desk import prices as prices_mod
from desk import shared_cache as sc
from desk.broker import MockBroker
from desk.prices import BROKER_MAX_COUNT, MINUTES_NS, MINUTES_TTL_SEC, PriceSource, krx_minutes_move
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
def demand_reset(monkeypatch):
    token = demand.SCREEN.set(True)                  # these reads stand for a screen's (desk.demand.ScreenReads)
    monkeypatch.setattr(prices_mod, "krx_minutes_move", lambda now=None: True)   # KRX's session is on
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


def _minute_marks() -> set:
    return {k for k in demand._seen if k[0] == MINUTES_NS}


def test_only_a_screens_krx_1m_reads_mark_on_every_read(redis):
    async def go():
        demand.configure("api")
        api = PriceSource(Toss(), yahoo_candles=_yahoo)
        await api.candles(KRX, "1m", 30)             # the live chart's read
        marked = _minute_marks()
        demand._seen[(MINUTES_NS, KRX)] = 0.0
        await api.candles(KRX, "1m", 30)             # a memory hit marks too: it keeps the collection going
        again = demand._seen[(MINUTES_NS, KRX)] > 0.0
        return marked, again

    marked, again = asyncio.run(go())
    assert marked == {(MINUTES_NS, KRX)} and again


def test_krx_minute_charts_take_at_most_their_share_of_the_wanted_set(monkeypatch):
    monkeypatch.setattr(demand, "enabled", lambda: True)   # one account opening many charts keeps the quotes
    demand.configure("api")
    for i in range(demand.NS_MAX[MINUTES_NS] + 5):
        demand.want(MINUTES_NS, f"{i:06d}")
    demand.want("quote", "AAPL")
    wanted = demand._current(demand.time.monotonic())
    assert len(wanted[MINUTES_NS]) == demand.NS_MAX[MINUTES_NS] == 10 and wanted["quote"] == ["AAPL"]
    assert demand.stats()["dropped"] == 5


@pytest.mark.parametrize("interval", ["5m", "15m", "60m", "240m"])
def test_a_krx_minute_charts_tail_marks_nothing_but_takes_a_collected_value(redis, interval):
    api_broker = Toss(shift=-50)

    async def go():
        demand.configure("api")
        api = PriceSource(api_broker, yahoo_candles=_yahoo)
        await api.candles(KRX, interval, 50)         # nothing shared: its own 1m tail read, and no mark
        await api.candles(KRX, interval, 50)         # a memory hit: no mark either
        unmarked = _minute_marks()
        await _collected()                           # a live chart elsewhere keeps the 1m collected
        demand.configure("api")
        fresh = PriceSource(api_broker, yahoo_candles=_yahoo)
        chart = await fresh.candles(KRX, interval, 50)
        return unmarked, chart, _minute_marks()

    unmarked, chart, marks = asyncio.run(go())
    assert unmarked == set() and marks == set()
    assert api_broker.calls == [("005930", "1m", BROKER_MAX_COUNT)]   # the first chart's own read only
    assert chart["source"] == "yahoo+toss"


def test_no_mark_outside_a_screen_for_us_tickers_daily_bars_or_with_the_session_off(redis, monkeypatch):
    async def go():
        demand.configure("api")
        api = PriceSource(Toss(), yahoo_candles=_yahoo)
        for ticker, interval in (("NVDA", "1m"), ("NVDA", "15m"), (KRX, "1d")):
            await api.candles(ticker, interval, 30)
        token = demand.SCREEN.set(False)             # a background job's read
        try:
            await api.candles("000660.KS", "1m", 30)
        finally:
            demand.SCREEN.reset(token)
        monkeypatch.setattr(prices_mod, "krx_minutes_move", lambda now=None: False)   # after the close
        await api.candles("035420.KS", "1m", 30)
        return _minute_marks()

    assert asyncio.run(go()) == set()


def test_krx_1m_bars_move_in_the_regular_session_and_a_minute_after_it():
    kst = lambda s: datetime.fromisoformat(s + "+09:00").astimezone(UTC)
    assert krx_minutes_move(kst("2026-11-13T09:00:00")) and krx_minutes_move(kst("2026-11-13T15:30:59"))
    assert not krx_minutes_move(kst("2026-11-13T08:59:00"))      # before the open (Friday)
    assert not krx_minutes_move(kst("2026-11-13T15:32:00"))      # after the close
    assert not krx_minutes_move(kst("2026-11-14T10:00:00"))      # Saturday
    assert not krx_minutes_move(kst("2026-12-25T10:00:00"))      # a KRX holiday


# -- #689 review: the Toss calls a screen costs, api and collector together, against one process (`all`) reading it
# itself; and how old the live chart's last bar is


def _simulate(monkeypatch, interval: str, count: int, poll: int, split: bool, horizon: int = 600,
              stamped: bool = False) -> tuple[int, int, list[float]]:
    """``horizon`` seconds on a fake clock: a screen reads ``interval`` bars every ``poll`` seconds; split, the api
    flushes its wants every 2 s and the collector serves every 5 s (desk.api DEMAND_WANTS_SEC · DEMAND_SERVE_SEC).
    Returns the api's and the collector's Toss 1m calls and, ``stamped``, the age of the last bar each read got."""
    now = [0.0]
    wall = time.time()
    monkeypatch.setattr(demand, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(sc, "time", SimpleNamespace(time=lambda: wall + now[0], monotonic=time.monotonic))
    server = fakeredis.FakeServer()
    sc.use(fakeredis.FakeRedis(server=server, decode_responses=True),
           fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    demand.configure("")

    class Stamped(Toss):
        async def candles(self, symbol, interval, count):
            if stamped:
                self.shift = now[0]                  # the last bar's close tells when it was read
            return await super().candles(symbol, interval, count)

    api_broker, collector_broker = Stamped(), Stamped()
    api = PriceSource(api_broker, yahoo_candles=_yahoo, clock=lambda: now[0])
    collector = PriceSource(collector_broker, yahoo_candles=_yahoo, clock=lambda: now[0])
    ages: list[float] = []

    async def go():
        for s in range(horizon):
            now[0] = float(s)
            if split and s % 2 == 0:
                demand._role = "api"
                await demand.flush()
            if split and s % 5 == 2:
                demand._role = "collector"
                await demand.serve({MINUTES_NS: collector.refresh_minutes})
            if s % poll == 0:
                demand._role = "api" if split else "all"
                got = await api.candles(KRX, interval, count)
                await asyncio.sleep(0.005)            # a background read the answer started
                if stamped and s > 60:
                    ages.append(s - (got["bars"][-1]["close"] - 499))
    try:
        asyncio.run(go())
    finally:
        sc.use(None, None)
    calls = lambda broker: sum(1 for c in broker.calls if c[1] == "1m")
    return calls(api_broker), calls(collector_broker), ages


@pytest.mark.parametrize(("interval", "count", "poll"), [
    ("5m", 78, 120),        # the favourites' spark (FavoritesPanel), every 2 minutes
    ("5m", 120, 60),        # a board chart every minute
    ("240m", 120, 60),      # BoardChart 240m every minute
    ("1m", 30, 5),          # desk.live.frame: the live chart every 5 s
])
def test_a_screen_never_costs_more_toss_calls_than_one_process_reading_itself(monkeypatch, interval, count, poll):
    before, _, _ = _simulate(monkeypatch, interval, count, poll, split=False)
    api, collector, _ = _simulate(monkeypatch, interval, count, poll, split=True)
    assert api + collector <= before, (interval, poll, before, api, collector)
    if interval == "1m":
        assert api <= 1                              # the live chart's first read only: the collector reads the rest


def test_the_live_charts_last_bar_is_no_older_than_the_collectors_period(monkeypatch):
    _, _, before = _simulate(monkeypatch, "1m", 30, 5, split=False, horizon=900, stamped=True)
    _, _, after = _simulate(monkeypatch, "1m", 30, 5, split=True, horizon=900, stamped=True)
    period = demand.EVERY[MINUTES_NS] + 5             # a key due waits for the next serve pass (5 s)
    assert max(after) <= period and sum(after) / len(after) <= period / 2 + 3
    assert max(before) <= prices_mod.CANDLE_TTL_SEC   # one process: its own TTL, as before


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
               {**good, "bars": [{**bar, "extra": 1}]}, {**good, "bars": [[1, 2]]}, [good],
               {**good, "bars": [{**bar, "volume": 10 ** 400}]}]     # JSON's int past any float: a miss, no error
        return [await calls(value) for value in bad], await calls(good)
    misses, hit = asyncio.run(go())
    assert misses == [1] * 10 and hit == 0


def test_a_shared_quote_past_any_float_is_a_miss_not_an_error():
    quote = {"ticker": "NVDA", "price": 10 ** 400, "currency": "USD", "source": "toss", "fetched_at": "x"}
    assert prices_mod._shared_quote("NVDA", quote) is None
    assert prices_mod._shared_quote("NVDA", {**quote, "price": 181.5}) is not None


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
