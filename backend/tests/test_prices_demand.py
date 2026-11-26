"""#689 quotes collected on demand (desk.prices with desk.demand): the api answers a screen's quote from what the
collector fetched into Redis, and marks what it reads so that the collector keeps fetching it.

Each "process" is its own PriceSource on one fake Redis server; desk.demand is per process, so a test configures
it for the role whose code runs next."""

import asyncio
import time
from types import SimpleNamespace

import fakeredis
import pytest

from desk import demand, prices
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


# --- #994 the collector's quotes in one broker call ----------------------------------------------------------


class BatchBroker(Broker):
    """A broker with a many-symbol read: answers ``prices``' symbols, leaves the others out."""

    def __init__(self, prices: dict, alone: dict | None = None, fail=False):
        super().__init__()
        self.prices, self.alone, self.fail_batch = prices, alone or {}, fail
        self.batches: list[list[str]] = []
        self.singles: list[str] = []

    async def quotes(self, symbols):
        self.batches.append(list(symbols))
        if self.fail_batch:
            raise ConnectionError("429 rate limited")
        return {s: (self.prices[s], "USD") for s in symbols if s in self.prices}

    async def quote(self, symbol):
        self.singles.append(symbol)
        if symbol not in self.alone:
            raise ConnectionError("no price")
        return self.alone[symbol], "USD"


def _published(monkeypatch):
    from desk import market_bus
    events = []
    monkeypatch.setattr(market_bus, "publish", lambda kind, key, payload, source, *a: events.append(
        (kind, key, payload["price"], source)))
    return events


def test_a_batch_refresh_keeps_shares_and_publishes_as_one_tickers_refresh_does(redis, monkeypatch):
    events = _published(monkeypatch)

    async def go():
        demand.configure("collector")
        one = PriceSource(Broker(price=101.5), yahoo_quote=_no_yahoo)
        await one.refresh_quote("AAPL")
        broker = BatchBroker({"AAPL": 101.5, "BRK.B": 450.25, "005930": 74800.0})
        many = PriceSource(broker, yahoo_quote=_no_yahoo)
        reads = sc.stats()["hits"] + sc.stats()["misses"]
        failed = await many.refresh_quotes(["AAPL", "BRK-B", "005930.KS"])
        unread = sc.stats()["hits"] + sc.stats()["misses"] == reads             # never reads the shared tier
        shared = {t: await sc.aget("quote", t) for t in ("AAPL", "BRK-B", "005930.KS")}
        return one, many, broker, failed, unread, shared

    one, many, broker, failed, unread, shared = asyncio.run(go())
    assert failed == set() and unread
    assert broker.batches == [["AAPL", "BRK.B", "005930"]] and broker.singles == []
    for ticker, (value, _age, left) in shared.items():
        assert value["ticker"] == ticker and value["source"] == "toss"
        assert QUOTE_TTL_SEC < left <= DEMAND_TTL_SEC                            # refresh_quote's lifetime
    assert {k: v for k, v in shared["AAPL"][0].items() if k != "fetched_at"} == \
        {k: v for k, v in one._quotes["AAPL"][1].to_dict().items() if k != "fetched_at"}
    assert shared["005930.KS"][0]["price"] == 74800.0 and shared["BRK-B"][0]["price"] == 450.25
    assert set(many._quotes) == {"AAPL", "BRK-B", "005930.KS"}                   # kept in memory as well
    assert sorted(events) == sorted([("quote", "AAPL", 101.5, "toss"), ("quote", "AAPL", 101.5, "toss"),
                                     ("quote", "BRK-B", 450.25, "toss"), ("quote", "005930.KS", 74800.0, "toss")])


def test_tickers_the_broker_left_out_fail_without_a_second_read(redis):
    """Broker.quotes leaves out only what failed alone as well (Toss reads those itself): never read again here."""
    broker = BatchBroker({"AAPL": 101.5}, alone={"NVDA": 181.5})

    async def go():
        demand.configure("collector")
        failed = await PriceSource(broker, yahoo_quote=_no_yahoo).refresh_quotes(["AAPL", "NVDA", "GONE", "^KS11"])
        return failed, {t: await sc.aget("quote", t) for t in ("AAPL", "NVDA", "GONE", "^KS11")}

    failed, shared = asyncio.run(go())
    assert failed == {"NVDA", "GONE", "^KS11"}                        # an index is never a broker call
    assert broker.batches == [["AAPL", "NVDA", "GONE"]] and broker.singles == []
    assert shared["AAPL"][0]["price"] == 101.5 and shared["NVDA"] is None and shared["^KS11"] is None


class SlowBatch(MockBroker):
    """A many-symbol read held until ``release`` is set (``asked``: each call's symbols, when it starts) - reads
    under way side by side with no real wait; with ``hold``, only a call holding one of those symbols is held. A
    call holding a symbol of ``fail`` fails as a whole; ``leave_out`` are left out. ``singles``: each one-symbol
    read (refresh_quote's)."""
    name = "toss"

    def __init__(self, leave_out=(), fail=(), hold=None):
        self.leave_out, self.fail = set(leave_out), set(fail)
        self.hold = None if hold is None else set(hold)
        self.asked: list[list[str]] = []
        self.singles: list[str] = []
        self.release = asyncio.Event()

    async def quotes(self, symbols):
        self.asked.append(list(symbols))
        if self.hold is None or self.hold & set(symbols):
            await self.release.wait()
        if self.fail & set(symbols):
            raise ConnectionError("429 rate limited")
        return {s: (10.0, "USD") for s in symbols if s not in self.leave_out}

    async def quote(self, symbol):
        self.singles.append(symbol)
        return 10.0, "USD"


async def _until(done, tries: int = 1000) -> None:
    """Let the loop run until ``done()`` (each step one pass of the loop, no real wait)."""
    for _ in range(tries):
        if done():
            return
        await asyncio.sleep(0)
    raise AssertionError("never came to pass")


@pytest.mark.parametrize("outcome", ["read", "left_out", "failed", "cancelled"])
def test_a_ticker_another_batch_is_reading_is_not_read_again_and_takes_that_reads_outcome(redis, outcome):
    """#994 review: a ticker already under way is not read again, by a batch or by its own refresh_quote - each
    waits for that read and fails it when that read failed it (left out), failed as a whole or was cancelled."""
    broker = SlowBatch(leave_out=["NVDA"] if outcome == "left_out" else (),
                       fail=["AAPL"] if outcome == "failed" else ())

    async def go():
        demand.configure("collector")
        source = PriceSource(broker, yahoo_quote=_no_yahoo)
        first = asyncio.ensure_future(source.refresh_quotes(["AAPL", "NVDA"]))
        await _until(lambda: len(broker.asked) == 1)                 # the first one is under way
        reading = source._collecting["NVDA"]
        second = asyncio.ensure_future(source.refresh_quotes(["NVDA", "TSLA"]))   # TSLA read, NVDA joined
        third = asyncio.ensure_future(source.refresh_quotes(["NVDA"]))            # NVDA joined only
        own = asyncio.ensure_future(source.refresh_quote("NVDA"))                 # its own read joins it too
        await _until(lambda: len(broker.asked) == 2)                 # TSLA's read: every one above is waiting
        if outcome == "cancelled":
            reading.cancel()                     # by another: none of these calls is cancelled itself
        broker.release.set()
        done = await asyncio.gather(first, second, third, own, return_exceptions=True)
        await asyncio.sleep(0)                                       # the reads' done callbacks
        return done, set(source._collecting), [await sc.aget("quote", t) for t in ("AAPL", "NVDA", "TSLA")]

    (first, second, third, own), collecting, shared = asyncio.run(go())
    assert broker.asked == [["AAPL", "NVDA"], ["TSLA"]] and broker.singles == []   # NVDA's one read
    assert collecting == set() and shared[2] is not None
    if outcome == "read":
        assert first == second == third == set() and own is None
        assert all(hit is not None for hit in shared)
        return
    assert second == third == {"NVDA"} and shared[1] is None
    if outcome == "left_out":
        assert first == {"NVDA"} and shared[0] is not None and isinstance(own, LookupError)
    elif outcome == "failed":
        assert isinstance(first, ConnectionError) and isinstance(own, ConnectionError) and shared[0] is None
    else:                                                            # never a CancelledError of its own
        assert isinstance(first, LookupError) and isinstance(own, LookupError) and shared[0] is None


def test_calls_cancelled_with_the_read_they_wait_for_are_cancelled_themselves(redis):
    """#994 review (cancelled-both): only another's cancelling a read turns into a LookupError (a failure demand
    counts); a call cancelled together with it (the loop closing, the demand job stopped) raises its own
    CancelledError - the read's owner, a ticker's own refresh_quote and a batch that joined it alike."""
    broker = SlowBatch()

    async def go():
        demand.configure("collector")
        source = PriceSource(broker, yahoo_quote=_no_yahoo)
        first = asyncio.ensure_future(source.refresh_quotes(["AAPL", "NVDA"]))
        await _until(lambda: len(broker.asked) == 1)
        reading = source._collecting["NVDA"]
        own = asyncio.ensure_future(source.refresh_quote("NVDA"))
        joined = asyncio.ensure_future(source.refresh_quotes(["NVDA"]))
        for _ in range(3):
            await asyncio.sleep(0)                                   # both waiting for that read
        reading.cancel()
        for task in (first, own, joined):
            task.cancel()
        return await asyncio.gather(first, own, joined, return_exceptions=True)

    assert [type(result) for result in asyncio.run(go())] == [asyncio.CancelledError] * 3
    assert broker.asked == [["AAPL", "NVDA"]] and broker.singles == []


def test_the_demand_job_cancelled_with_its_batch_read_is_cancelled_and_counts_no_failure(redis):
    """#994 review (cancelled-both): the job's pass cancelled together with the batch read it waits for - the
    cancellation goes on out of demand.serve, and no key is backed off as if its fetch had failed."""
    broker = SlowBatch()

    async def go():
        demand.configure("api")
        for ticker in ("AAPL", "NVDA"):
            demand.want("quote", ticker)
        await demand.flush()
        demand.configure("collector")
        source = PriceSource(broker, yahoo_quote=_no_yahoo)
        job = asyncio.ensure_future(demand.serve({}, batch={"quote": source.refresh_quotes}))
        await _until(lambda: len(broker.asked) == 1)
        source._collecting["AAPL"].cancel()
        job.cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass
        return job.cancelled(), dict(demand._fails), dict(demand._due)

    cancelled, fails, due = asyncio.run(go())
    assert cancelled and fails == {} and due == {}


def test_a_batch_waiting_on_a_read_still_held_counts_the_tickers_it_read_itself(redis, monkeypatch):
    """#994 review: a read a demand timeout cut still held (Toss not answering) - the next batch reads its new
    ticker and waits JOIN_SHARE of HANDLER_SEC for the held ones: only those fail, before HANDLER_SEC would cut
    it and fail the new one as well."""
    monkeypatch.setattr(prices, "JOIN_SHARE", 0.0)                   # that wait runs out at once (no real wait)
    monkeypatch.setattr(demand, "HANDLER_SEC", 0.5)                  # a call still waiting would be cut here
    broker = SlowBatch(hold=["AAPL"])

    async def go():
        demand.configure("api")
        for ticker in ("AAPL", "NVDA", "TSLA"):
            demand.want("quote", ticker)
        await demand.flush()
        demand.configure("collector")
        source = PriceSource(broker, yahoo_quote=_no_yahoo)
        held = asyncio.ensure_future(source.refresh_quotes(["AAPL", "NVDA"]))   # a cut read, still held
        await _until(lambda: len(broker.asked) == 1)
        served = await demand.serve({}, batch={"quote": source.refresh_quotes})
        fails, tsla = set(demand._fails), await sc.aget("quote", "TSLA")
        broker.release.set()
        await held                                                   # still shares what it reads
        return served, fails, tsla, [await sc.aget("quote", t) for t in ("AAPL", "NVDA")]

    served, fails, tsla, held_shared = asyncio.run(go())
    assert served == 1 and fails == {("quote", "AAPL"), ("quote", "NVDA")} and tsla is not None
    assert all(hit is not None for hit in held_shared)
    assert broker.asked == [["AAPL", "NVDA"], ["TSLA"]]


def test_a_read_under_way_on_another_loop_is_never_joined(redis):
    """#994 review: as desk.single_flight.Flights - a read another event loop left (tests run one asyncio.run
    after another) is neither waited for nor taken as this one's."""
    broker = SlowBatch()
    broker.release.set()
    other = asyncio.new_event_loop()
    try:
        demand.configure("collector")
        source = PriceSource(broker, yahoo_quote=_no_yahoo)
        source._collecting["NVDA"] = other.create_future()
        failed = asyncio.run(source.refresh_quotes(["NVDA"]))
        source._collecting["NVDA"] = other.create_future()
        asyncio.run(source.refresh_quote("NVDA"))
    finally:
        other.close()
    assert failed == set() and broker.asked == [["NVDA"]] and broker.singles == ["NVDA"]


def test_a_batch_cut_by_the_demand_timeout_still_shares_what_it_reads(redis, monkeypatch):
    monkeypatch.setattr(demand, "HANDLER_SEC", 0.01)
    broker = SlowBatch()

    async def go():
        demand.configure("api")
        for ticker in ("AAPL", "NVDA"):
            demand.want("quote", ticker)
        await demand.flush()
        demand.configure("collector")
        source = PriceSource(broker, yahoo_quote=_no_yahoo)
        served = await demand.serve({}, batch={"quote": source.refresh_quotes})   # held past HANDLER_SEC: cut
        cut, during = dict(demand._fails), set(source._collecting)
        reading = source._collecting["AAPL"]
        broker.release.set()                                         # the read goes on past the cut
        await reading
        await asyncio.sleep(0)                                       # its done callback
        return served, cut, during, set(source._collecting), [await sc.aget("quote", t) for t in ("AAPL", "NVDA")]

    served, cut, during, after, shared = asyncio.run(go())
    assert served == 0 and set(cut) == {("quote", "AAPL"), ("quote", "NVDA")}
    assert during == {"AAPL", "NVDA"} and after == set()
    assert [hit[0]["price"] for hit in shared] == [10.0, 10.0]
    assert [sorted(a) for a in broker.asked] == [["AAPL", "NVDA"]]


def test_a_tickers_own_read_joins_the_batch_a_demand_timeout_cut(redis, monkeypatch):
    """#994 review: a batch refused, then cut by HANDLER_SEC, the batch since off - the next pass's per-ticker
    reads wait for that batch read instead of a second GET each."""
    monkeypatch.setattr(demand, "HANDLER_SEC", 0.01)
    broker = SlowBatch()
    joined = []
    real = prices._outcome

    async def outcome(reading):
        joined.append(reading)
        return await real(reading)

    monkeypatch.setattr(prices, "_outcome", outcome)

    async def go():
        demand.configure("api")
        for ticker in ("AAPL", "NVDA"):
            demand.want("quote", ticker)
        await demand.flush()
        demand.configure("collector")
        source = PriceSource(broker, yahoo_quote=_no_yahoo)
        await demand.serve({}, batch={"quote": source.refresh_quotes})            # cut, still reading
        reading = source._collecting["AAPL"]
        demand._due.clear()                                          # both due again
        monkeypatch.setattr(demand, "HANDLER_SEC", 5.0)
        joined.clear()
        each = asyncio.ensure_future(demand.serve({"quote": source.refresh_quote}))   # the batch off
        await _until(lambda: len(joined) == 2)                       # both tickers wait for that read
        broker.release.set()
        return await each, reading.result(), dict(demand._fails)

    served, theirs, fails = asyncio.run(go())
    assert served == 2 and theirs == set() and fails == {}
    assert [sorted(a) for a in broker.asked] == [["AAPL", "NVDA"]] and broker.singles == []   # one GET in all


def test_a_batch_writes_the_shared_tier_two_at_a_time(redis, monkeypatch):
    """Past shared_cache.TIMEOUT_SEC one write takes the tier down for DOWN_SEC: no burst of 60 at once."""
    real, active, peak = sc.aput, [0], [0]

    async def aput(*args):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.005)
        active[0] -= 1
        await real(*args)

    monkeypatch.setattr(sc, "aput", aput)
    tickers = [f"T{i:02d}" for i in range(10)]
    demand.configure("collector")
    failed = asyncio.run(PriceSource(BatchBroker({t: 1.0 for t in tickers}), yahoo_quote=_no_yahoo)
                         .refresh_quotes(tickers))
    assert failed == set() and peak[0] == 2 and len(redis.keys("td:quote:*")) == 10


def test_a_failed_batch_call_propagates_and_shares_nothing(redis):
    broker = BatchBroker({"AAPL": 101.5}, alone={"AAPL": 101.5}, fail=True)
    demand.configure("collector")
    with pytest.raises(ConnectionError):
        asyncio.run(PriceSource(broker, yahoo_quote=_no_yahoo).refresh_quotes(["AAPL", "NVDA"]))
    assert broker.singles == [] and redis.keys("td:quote:*") == []   # never one by one against a refusing source


def test_what_the_api_reads_the_collector_fetches_in_one_call(redis):
    collector, api_broker, clock = BatchBroker({"AAPL": 101.5, "NVDA": 181.5}), Broker(price=99.0), Clock()

    async def go():
        demand.configure("api")
        api = PriceSource(api_broker, yahoo_quote=_no_yahoo, clock=clock)
        await api.quote("AAPL")
        await api.quote("NVDA")
        await demand.flush()
        demand.configure("collector")
        source = PriceSource(collector)
        served = await demand.serve({"quote": source.refresh_quote}, batch={"quote": source.refresh_quotes})
        demand.configure("api")
        clock.t += POLL_SEC
        return served, [(await api.quote(t)).price for t in ("AAPL", "NVDA")]

    served, prices = asyncio.run(go())
    assert (served, prices) == (2, [101.5, 181.5])
    assert [sorted(b) for b in collector.batches] == [["AAPL", "NVDA"]] and collector.singles == []
    assert api_broker.calls == 2                                     # its own first reads only
