"""#689 the USD/KRW rate through the shared tier (desk.portfolio with desk.shared_cache and desk.demand): the api's
"내 투자" panel takes the rate the collector read (its demand job, or its holding watch's snapshot) instead of
calling Toss itself; the holdings are account data and stay the api's own call.

Each "process" is its own Portfolio on one fake Redis server; desk.demand is per process, so a test configures it
for the role whose code runs next."""

import asyncio

import fakeredis
import pytest

from desk import demand
from desk import shared_cache as sc
from desk.portfolio import FX_SHARED_NS, FX_SHARED_TTL_SEC, FX_TTL_SEC
from tests.test_portfolio import Book, _book


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


def test_the_api_snapshot_takes_the_collectors_rate_and_still_reads_the_holdings_itself(redis):
    collector_broker, api_broker = Book(fx=1410.0), Book(fx=1300.0)

    async def go():
        demand.configure("collector")
        collector, _ = _book(collector_broker)
        await collector.refresh_fx("usd_krw")           # the collector's demand job for "fx"
        shared = (await sc.aget(FX_SHARED_NS, "usd_krw"))[0]
        demand.configure("api")
        api, clock = _book(api_broker)
        first = await api.snapshot()
        clock.t += FX_TTL_SEC + 1                       # past the api's own memory: Redis again, still no Toss
        second = await api.snapshot()
        return shared, first, second

    shared, first, second = asyncio.run(go())
    assert api_broker.calls == {"holdings": 2, "fx": 0}            # account data: its own call, every time
    assert collector_broker.calls == {"holdings": 0, "fx": 1}      # the collector never reads the holdings here
    assert first["fx"] == second["fx"] == shared == {"usd_krw": 1410.0, "source": "toss", "at": shared["at"]}
    assert first["error"] is None
    assert first["total"]["krw"] == round(12 * 190.0 * 1410.0) + 40 * 75000
    assert ("fx", "usd_krw") in demand._seen                       # the screen's read keeps the rate collected
    assert sc.stats()["skipped"] == 0


def test_what_the_api_reads_the_collector_reads_and_the_api_reads_that_next(redis):
    api_broker, collector_broker = Book(fx=1300.0), Book(fx=1410.0)

    async def go():
        demand.configure("api")
        api, clock = _book(api_broker)
        first = await api.snapshot()                    # nothing shared yet: its own read, shared for FX_TTL_SEC
        await demand.flush()                            # the demand_wants job
        demand.configure("collector")
        collector, _ = _book(collector_broker)
        served = await demand.serve({FX_SHARED_NS: collector.refresh_fx})
        demand.configure("api")
        clock.t += FX_TTL_SEC + 1
        second = await api.snapshot()
        return first, served, second

    first, served, second = asyncio.run(go())
    assert served == 1 and collector_broker.calls == {"holdings": 0, "fx": 1}
    assert (first["fx"]["usd_krw"], second["fx"]["usd_krw"]) == (1300.0, 1410.0)
    assert api_broker.calls == {"holdings": 2, "fx": 1}            # the first read only


def test_the_collectors_holding_watch_snapshot_shares_its_rate_for_the_collected_lifetime(redis):
    api_broker = Book(fx=1300.0)

    async def go():
        demand.configure("collector")
        watch_book, _ = _book(Book(fx=1420.5))
        await watch_book.snapshot()                    # desk.holding_watch's pass on the collector
        left = (await sc.aget(FX_SHARED_NS, "usd_krw"))[2]
        demand.configure("api")
        api, _ = _book(api_broker)
        return left, await api.snapshot()

    left, snap = asyncio.run(go())
    assert FX_TTL_SEC < left <= FX_SHARED_TTL_SEC
    assert snap["fx"]["usd_krw"] == 1420.5 and api_broker.calls["fx"] == 0


def test_with_nothing_shared_the_api_reads_the_rate_itself_and_shares_it(redis):
    api_broker = Book(fx=1300.0)

    async def go():
        demand.configure("api")
        api, _ = _book(api_broker)
        snap = await api.snapshot()
        return snap, await sc.aget(FX_SHARED_NS, "usd_krw")

    snap, hit = asyncio.run(go())
    assert api_broker.calls == {"holdings": 1, "fx": 1}
    assert snap["fx"]["usd_krw"] == 1300.0 and hit[0] == snap["fx"] and hit[2] <= FX_TTL_SEC


def test_redis_down_leaves_the_rate_on_its_own_path():
    sc.use(_Down(), _Down())
    try:
        demand.configure("api")
        broker = Book(fx=1305.0)
        book, _ = _book(broker, yahoo_fx=lambda: 1390.25)
        snap = asyncio.run(book.snapshot())
        assert snap["fx"]["usd_krw"] == 1305.0 and snap["fx"]["source"] == "toss" and snap["error"] is None
        assert broker.calls == {"holdings": 1, "fx": 1}
        broker.fx = None                                # and the Yahoo fallback still stands
        fallback, _ = _book(broker, yahoo_fx=lambda: 1390.25)
        assert asyncio.run(fallback.snapshot())["fx"]["source"] == "yahoo"
    finally:
        sc.use(None, None)


def test_a_shared_value_that_is_not_a_rate_is_a_miss(redis):
    api_broker = Book(fx=1300.0)

    async def go():
        for bad in ({"usd_krw": "1400", "source": "toss", "at": "x"}, {"usd_krw": -1.0, "source": "toss", "at": "x"},
                    {"usd_krw": 1400.0}, [1400.0]):
            await sc.aput(FX_SHARED_NS, "usd_krw", bad, 30)
            demand.configure("api")
            api, _ = _book(api_broker)
            assert (await api.snapshot())["fx"]["usd_krw"] == 1300.0
    asyncio.run(go())
    assert api_broker.calls["fx"] == 4


def test_the_collectors_fx_job_fails_loudly_so_demand_backs_off(redis):
    async def go():
        demand.configure("collector")
        book, _ = _book(Book(fx=None))                 # Toss and Yahoo both down
        with pytest.raises(ConnectionError):
            await book.refresh_fx("usd_krw")
        return await sc.aget(FX_SHARED_NS, "usd_krw")
    assert asyncio.run(go()) is None


def test_outside_a_screen_the_api_marks_nothing(redis):
    async def go():
        demand.configure("api")
        token = demand.SCREEN.set(False)               # a background job's snapshot
        try:
            api, _ = _book(Book())
            await api.snapshot()
        finally:
            demand.SCREEN.reset(token)
        return set(demand._seen)
    assert asyncio.run(go()) == set()
