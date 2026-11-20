"""#689 the shared cache tier (desk.shared_cache) over fakeredis: what is shared, what is not, and that an
unreachable Redis leaves every cache working from memory as before."""

import asyncio
import time
from datetime import UTC, datetime

import fakeredis
import pytest

from desk import outbound
from desk import shared_cache as sc
from desk.broker import MockBroker
from desk.market_flow import KST, MarketFlow
from desk.prices import PriceSource
from desk.settings import Settings
from desk.ttl_cache import TTLCache
from tests.test_futures_flow import _rest
from tests.test_market_flow import CountingBroker


@pytest.fixture
def redis():
    """Two clients on one fake server - what the sync (worker threads) and asyncio sides of one process,
    or two processes, see."""
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    sc.use(sync, fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    yield sync
    sc.use(None, None)


def test_off_unless_configured():
    sc.configure(Settings(database_url="sqlite://"))
    sc.put("ns", "k", {"a": 1}, 10)
    assert sc.get("ns", "k") is None and asyncio.run(sc.aget("ns", "k")) is None
    assert sc.stats()["on"] is False
    assert "secret-pw" not in repr(Settings(database_url="sqlite://", redis_password="secret-pw",
                                            redis_url="redis://:secret-pw@h:6379/0"))


def test_keys_are_prefixed_always_expire_and_round_trip(redis):
    sc.put("sr", ("NVDA", None, "swing"), {"zones": [1.5, 2], "label": "지지"}, 30)
    assert redis.keys("*") == ["td:sr:NVDA::swing"]
    assert 0 < redis.pttl("td:sr:NVDA::swing") <= 30_000
    value, age, left = sc.get("sr", ("NVDA", None, "swing"))
    assert value == {"zones": [1.5, 2], "label": "지지"} and 0 <= age < 5 and 0 < left <= 30
    assert sc.stats() | {"down_sec": 0} == {"on": True, "down_sec": 0, "hits": 1, "misses": 0, "writes": 1,
                                             "skipped": 0, "errors": 0}


def test_a_value_json_would_change_stays_in_memory(redis, caplog):
    sc.put("ns", "tuple", {"pair": (1, 2)}, 10)       # a tuple comes back a list
    sc.put("ns", "nan", float("nan"), 10)             # not JSON
    sc.put("ns", "nothing", {"a": 1}, 0)              # ttl 0 = keep nothing
    assert redis.keys("*") == [] and sc.stats()["skipped"] == 2
    assert sum("round-trip" in r.message for r in caplog.records) <= 1   # once per namespace


def test_sync_calls_never_block_the_event_loop_thread(redis):
    sc.put("ns", "k", 1, 10)

    async def on_loop():
        sc.put("ns", "other", 2, 10)                   # skipped here: aput is the loop's call
        return sc.get("ns", "k"), await sc.aget("ns", "k")

    blocked, awaited = asyncio.run(on_loop())
    assert blocked is None and awaited[0] == 1 and redis.keys("td:ns:other") == []


def test_a_tiered_cache_reads_what_another_process_wrote_and_then_keeps_it(redis):
    collector = sc.TieredCache("osc", TTLCache(8, 900))
    api = sc.TieredCache("osc", TTLCache(8, 900))     # another process: its own memory
    collector.put(("NVDA", "1d"), {"rsi": 55.0})
    assert api.get(("NVDA", "1d")) == {"rsi": 55.0}
    redis.flushall()
    assert api.get(("NVDA", "1d")) == {"rsi": 55.0}    # now from its memory, no round trip
    assert api.get(("AMD", "1d"), "none") == "none"


def test_a_shared_value_is_kept_only_for_what_is_left_of_its_ttl(redis):
    sc.put("osc", ("A", "1d"), {"r": 1}, 3)
    clock = [0.0]
    api = sc.TieredCache("osc", TTLCache(8, 900, clock=lambda: clock[0]))
    assert api.get(("A", "1d")) == {"r": 1}
    redis.flushall()
    clock[0] += 3.5
    assert api.get(("A", "1d")) is None
    sc.put("osc", ("B", "1d"), {"r": 2}, 3)
    assert api.get(("B", "1d"), ahead=5) is None       # the prewarm's "about to expire" counts the shared TTL


def test_async_tiered_calls(redis):
    async def go():
        a = sc.TieredCache("sr", TTLCache(8, 300))
        await a.aput(("X", None, "day"), {"z": [1]}, 120)
        b = sc.TieredCache("sr", TTLCache(8, 300))
        return await b.aget(("X", None, "day")), redis.pttl("td:sr:X::day")
    got, left_ms = asyncio.run(go())
    assert got == {"z": [1]} and 0 < left_ms <= 120_000


class _Broken:
    def __init__(self):
        self.calls = 0

    def pipeline(self, transaction=False):
        self.calls += 1
        raise ConnectionError("refused")

    def set(self, *args, **kwargs):
        self.calls += 1
        raise ConnectionError("refused")


def test_an_unreachable_server_is_skipped_for_a_while_with_one_warning(caplog):
    broken = _Broken()
    sc.use(broken, None)
    try:
        assert sc.get("ns", "k") is None
        sc.put("ns", "k", 1, 10)
        assert sc.get("ns", "k") is None
        assert broken.calls == 1                         # down: the next calls never reach the socket
        assert sc.stats()["errors"] == 1 and sc.stats()["down_sec"] > 0
        assert [r.levelname for r in caplog.records if "unreachable" in r.message] == ["WARNING"]
        assert "refused" not in caplog.text              # the exception type only
    finally:
        sc.use(None, None)


@pytest.mark.parametrize("settings", [{"redis_url": "redis://127.0.0.1:1/0"},
                                      {"redis_sentinels": ("127.0.0.1:1",), "redis_password": "pw-not-real"}])
def test_a_configured_redis_that_is_down_falls_back_fast(settings, caplog):
    sc.configure(Settings(database_url="sqlite://", **settings))
    try:
        started = time.monotonic()
        assert sc.get("ns", "k") is None
        assert asyncio.run(sc.aget("ns", "k")) is None   # already marked down: no second wait
        assert time.monotonic() - started < 3
        assert sc.stats()["on"] is True and sc.stats()["errors"] == 1
        assert "pw-not-real" not in caplog.text
    finally:
        sc.use(None, None)


NOW = datetime(2026, 11, 5, 10, tzinfo=KST).astimezone(UTC)   # Thursday, session open


def test_the_market_flow_card_reads_what_the_collector_fetched(redis):
    collector, api = CountingBroker(), CountingBroker()

    async def go():
        snap = await MarketFlow(collector, now=lambda: NOW).get("KOSPI", "1d", 5, fresh=True)
        card = await MarketFlow(api, now=lambda: NOW).get("KOSPI", "1d", 5)
        return snap, card

    snap, card = asyncio.run(go())
    assert collector.calls == 1 and api.calls == 0
    assert card["records"] == snap["records"] and card["fetched_at"] == snap["fetched_at"]
    assert card["freshness"] == snap["freshness"]


def test_a_failed_market_flow_read_is_not_shared(redis):
    api = CountingBroker()

    async def go():
        await MarketFlow(CountingBroker(fail=True), now=lambda: NOW).get("KOSPI", "1d", 5, fresh=True)
        after_failure = redis.keys("td:market_flow:*")
        await MarketFlow(api, now=lambda: NOW).get("KOSPI", "1d", 5)
        return after_failure

    assert asyncio.run(go()) == [] and api.calls == 1       # the api fetched itself (and then shared it)
    assert redis.keys("td:market_flow:*") == ["td:market_flow:KOSPI:1d"]


def test_the_futures_card_reads_what_the_snapshot_job_fetched(redis):
    from desk import futures_flow as ff

    collector_calls, api_calls = [], []
    now = lambda: datetime(2026, 11, 6, 2, 0, tzinfo=UTC)

    async def go():
        snap = await ff.FuturesFlow(_rest(collector_calls), None, now=now).get(fresh=True)
        card = await ff.FuturesFlow(_rest(api_calls), None, now=now).get()
        return snap, card

    snap, card = asyncio.run(go())
    assert collector_calls and not api_calls
    assert {k: v for k, v in card.items() if k != "freshness"} == {k: v for k, v in snap.items() if k != "freshness"}


class _QuoteBroker(MockBroker):
    def __init__(self):
        self.calls = 0

    async def quote(self, symbol):
        self.calls += 1
        return 101.5, "USD"


def test_a_quote_read_in_one_process_serves_the_others(redis):
    one, other = _QuoteBroker(), _QuoteBroker()

    async def go():
        first = await PriceSource(one).quote("AAPL")
        second = await PriceSource(other).quote("AAPL")
        return first, second

    first, second = asyncio.run(go())
    assert first == second and one.calls == 1 and other.calls == 0


def test_outbound_counts_and_status_shows_them(db_url):
    from tests.test_api import _client

    outbound.reset()
    outbound.count("toss")
    outbound.count("toss")
    outbound.count("kis")
    snap = outbound.snapshot()
    assert snap["toss"] == 2 and snap["kis"] == 1 and snap["since"].endswith("+00:00")
    with _client(db_url) as client:
        body = client.get("/api/status").json()
    assert body["shared_cache"]["on"] is False and "toss" in body["outbound"]
