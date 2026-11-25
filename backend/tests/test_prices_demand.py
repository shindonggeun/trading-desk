"""#689 quotes collected on demand (desk.prices with desk.demand): the api answers a screen's quote from what the
collector fetched into Redis, and marks what it reads so that the collector keeps fetching it.

Each "process" is its own PriceSource on one fake Redis server; desk.demand is per process, so a test configures
it for the role whose code runs next."""

import asyncio
import time
from types import SimpleNamespace

import fakeredis
import pytest

from desk import demand
from desk import shared_cache as sc
from desk.broker import MockBroker
from desk.prices import DEMAND_TTL_SEC, QUOTE_TTL_SEC, PriceSource

POLL_SEC = 15.0   # the quote board's /api/estimates poll


class Broker(MockBroker):
    name = "toss"

    def __init__(self, price=101.5, fail=False):
        self.price, self.fail, self.calls = price, fail, 0

    async def quote(self, symbol):
        self.calls += 1
        if self.fail:
            raise ConnectionError("403 not an allowed IP")
        return self.price, "USD"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _no_yahoo(ticker):
    raise AssertionError(f"Yahoo asked for {ticker}")


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


def _written_ago(monkeypatch, seconds):
    """shared_cache stamps what it writes from here on ``seconds`` in the past (``_now`` undoes it)."""
    real = time.time
    monkeypatch.setattr(sc, "time", SimpleNamespace(time=lambda: real() - seconds, monotonic=time.monotonic))


def _now(monkeypatch):
    monkeypatch.setattr(sc, "time", time)


async def _collected(monkeypatch, ticker="AAPL", age=0.0, price=101.5):
    """The collector's demand job fetched ``ticker`` ``age`` seconds ago."""
    demand.configure("collector")
    _written_ago(monkeypatch, age)
    await PriceSource(Broker(price=price)).refresh_quote(ticker)
    _now(monkeypatch)


def test_the_api_polls_the_collectors_quote_without_calling_the_broker(redis, monkeypatch):
    api_broker, clock = Broker(price=99.0), Clock()

    async def go():
        await _collected(monkeypatch, age=QUOTE_TTL_SEC + 5)       # older than the api's own reads would be
        shared = (await sc.aget("quote", "AAPL"))[0]
        demand.configure("api")
        api = PriceSource(api_broker, yahoo_quote=_no_yahoo, clock=clock)
        got = []
        for _ in range(4):
            got.append(await api.quote("AAPL"))
            clock.t += POLL_SEC
        return shared, got

    shared, got = asyncio.run(go())
    assert api_broker.calls == 0
    assert {(q.price, q.source, q.fetched_at) for q in got} == {(101.5, "toss", shared["fetched_at"])}


def test_what_the_api_reads_the_collector_fetches_and_the_api_reads_that_next(redis):
    collector, api_broker, clock = Broker(price=101.5), Broker(price=99.0), Clock()

    async def go():
        demand.configure("api")
        api = PriceSource(api_broker, yahoo_quote=_no_yahoo, clock=clock)
        first = await api.quote("AAPL")                 # nothing shared yet: its own read, as before
        await demand.flush()                            # the demand_wants job
        demand.configure("collector")
        served = await demand.serve({"quote": PriceSource(collector).refresh_quote})
        demand.configure("api")
        clock.t += POLL_SEC
        second = await api.quote("AAPL")
        return first, served, second

    first, served, second = asyncio.run(go())
    assert (first.price, served, second.price) == (99.0, 1, 101.5)
    assert api_broker.calls == 1 and collector.calls == 1


def test_the_api_marks_the_broker_quotes_it_reads_on_a_hit_too(redis, monkeypatch):
    clock = Clock()

    async def go():
        await _collected(monkeypatch, "NVDA")
        demand.configure("api")
        api = PriceSource(Broker(price=99.0), yahoo_quote=lambda t: (2500.0, "USD"), clock=clock)
        await api.quote("NVDA")                         # the collector's value
        hit = set(demand._seen)
        await api.quote("005930.KS")                    # nothing shared: its own read
        for ticker in ("^KS11", "NQ=F", "BTC-USD"):     # Yahoo only: no broker to collect them from
            await api.quote(ticker)
        marked = set(demand._seen)
        demand.configure("analyst")                     # any other process marks nothing
        clock.t += POLL_SEC
        await PriceSource(Broker()).quote("NVDA")
        return hit, marked, set(demand._seen)

    hit, marked, other = asyncio.run(go())
    assert hit == {("quote", "NVDA")}
    assert marked == {("quote", "NVDA"), ("quote", "005930.KS")}
    assert other == set()


@pytest.mark.parametrize(("role", "age", "own_read"), [
    ("api", QUOTE_TTL_SEC - 5, False),
    ("api", QUOTE_TTL_SEC + 10, False),
    ("api", DEMAND_TTL_SEC + 5, True),      # past the collector's ttl: gone
    ("analyst", QUOTE_TTL_SEC - 5, False),
    ("analyst", QUOTE_TTL_SEC + 10, True),
    ("collector", QUOTE_TTL_SEC + 10, True),
    ("all", QUOTE_TTL_SEC + 10, True),
])
def test_only_the_api_takes_a_collected_quote_older_than_its_own_ttl(redis, monkeypatch, role, age, own_read):
    reader = Broker(price=99.0)

    async def go():
        await _collected(monkeypatch, age=age)
        demand.configure(role)
        return await PriceSource(reader, yahoo_quote=_no_yahoo, clock=Clock()).quote("AAPL")

    got = asyncio.run(go())
    assert reader.calls == (1 if own_read else 0)
    assert got.price == (99.0 if own_read else 101.5)


@pytest.mark.parametrize(("role", "age", "kept"), [
    ("all", 4.0, 6.0),                      # until 10 s after the fetch, not the 26 s left of the collector's ttl
    ("api", 4.0, 6.0),
    ("api", QUOTE_TTL_SEC + 10, 1.0),       # an old one the api took: a moment, then Redis again
])
def test_a_shared_quote_stays_in_memory_until_ten_seconds_after_its_fetch(redis, monkeypatch, role, age, kept):
    clock = Clock()

    async def go():
        await _collected(monkeypatch, age=age)
        demand.configure(role)
        reader = PriceSource(Broker(price=99.0), yahoo_quote=_no_yahoo, clock=clock)
        hits = []
        for step in (0.0, kept - 0.5, 1.0):
            clock.t += step
            await reader.quote("AAPL")
            hits.append(sc.stats()["hits"])
        return hits

    assert asyncio.run(go()) == [1, 1, 2]           # read, from memory, from Redis again


def test_a_kis_tick_answers_first_and_marks_nothing(redis):
    demand.configure("api")
    tick = SimpleNamespace(price=101.25, received_at="2026-11-25T01:00:00+00:00")
    broker = Broker()
    got = asyncio.run(PriceSource(broker, stream=SimpleNamespace(latest=lambda t: tick)).quote("NVDA"))
    assert (got.source, got.price, broker.calls) == ("kis", 101.25, 0)
    assert demand._seen == {} and sc.stats()["hits"] + sc.stats()["misses"] == 0


def test_the_collector_fetches_even_with_a_value_shared_and_shares_it_longer(redis):
    collector = Broker(price=101.5)

    async def go():
        demand.configure("analyst")
        await PriceSource(Broker(price=99.0)).quote("AAPL")      # another process's 10 s value
        demand.configure("collector")
        source = PriceSource(collector, yahoo_quote=_no_yahoo)
        reads = sc.stats()["hits"] + sc.stats()["misses"]
        await source.refresh_quote("AAPL")
        await source.refresh_quote("AAPL")                       # nor from its own memory
        unread = sc.stats()["hits"] + sc.stats()["misses"] == reads
        return unread, await sc.aget("quote", "AAPL")

    unread, (value, _age, left) = asyncio.run(go())
    assert unread and collector.calls == 2
    assert (value["price"], value["source"]) == (101.5, "toss") and QUOTE_TTL_SEC < left <= DEMAND_TTL_SEC


def test_a_failed_collection_propagates_and_shares_nothing(redis):
    demand.configure("collector")
    with pytest.raises(ConnectionError):
        asyncio.run(PriceSource(Broker(fail=True), yahoo_quote=_no_yahoo).refresh_quote("AAPL"))
    broker = Broker()
    with pytest.raises(ValueError):                                  # an index is never a broker call
        asyncio.run(PriceSource(broker, yahoo_quote=_no_yahoo).refresh_quote("^KS11"))
    assert broker.calls == 0 and redis.keys("td:quote:*") == []


def test_redis_down_leaves_the_api_on_its_own_broker_reads():
    sc.use(_Down(), _Down())
    try:
        demand.configure("api")
        broker, clock = Broker(), Clock()
        source = PriceSource(broker, yahoo_quote=_no_yahoo, clock=clock)

        async def go():
            first = await source.quote("AAPL")
            clock.t += POLL_SEC
            await source.quote("AAPL")
            await demand.flush()                        # the wanted set goes nowhere, quietly
            return first

        first = asyncio.run(go())
        assert (first.price, first.source, broker.calls) == (101.5, "toss", 2)
    finally:
        sc.use(None, None)


def test_the_collectors_own_reads_keep_the_collected_lifetime(redis):
    """The collector's holdings watch reads quote() too: its write must not cut a collected value to 10 s."""
    async def go(role):
        sc.use(*_fresh())
        demand.configure(role)
        await PriceSource(Broker(price=101.5), yahoo_quote=_no_yahoo).quote("AAPL")
        return (await sc.aget("quote", "AAPL"))[2]

    assert QUOTE_TTL_SEC < asyncio.run(go("collector")) <= DEMAND_TTL_SEC
    assert asyncio.run(go("analyst")) <= QUOTE_TTL_SEC


def _fresh():
    server = fakeredis.FakeServer()
    return (fakeredis.FakeRedis(server=server, decode_responses=True),
            fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
