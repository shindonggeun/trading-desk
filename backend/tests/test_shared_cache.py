"""#689 the shared cache tier (desk.shared_cache) over fakeredis: what is shared, what is not, and that an
unreachable Redis leaves every cache working from memory as before."""

import asyncio
import json
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
                                            redis_url="redis://:secret-pw@h:6379/0", redis_cache_key="secret-pw"))


def test_no_cache_key_keeps_the_tier_off(caplog):
    sc.configure(Settings(database_url="sqlite://", redis_url="redis://127.0.0.1:1/0", redis_cache_key=""))
    assert sc.stats()["on"] is False
    assert any("DESK_REDIS_CACHE_KEY" in r.message for r in caplog.records)


def test_keys_are_prefixed_always_expire_and_round_trip(redis):
    sc.put("sr", ("NVDA", None, "swing"), {"zones": [1.5, 2], "label": "지지"}, 30)
    [name] = redis.keys("*")
    assert name == sc._key("sr", ("NVDA", None, "swing")) and name.startswith("td:sr:") and "NVDA" not in name
    assert "NVDA" not in redis.get(name) and "지지" not in redis.get(name)    # sealed: nothing readable
    assert 0 < redis.pttl(name) <= 30_000
    value, age, left = sc.get("sr", ("NVDA", None, "swing"))
    assert value == {"zones": [1.5, 2], "label": "지지"} and 0 <= age < 5 and 0 < left <= 30
    assert sc.stats() | {"down_sec": 0} == {"on": True, "down_sec": 0, "hits": 1, "misses": 0, "writes": 1,
                                             "skipped": 0, "rejected": 0, "errors": 0}


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
    assert blocked is None and awaited[0] == 1 and redis.keys(sc._key("ns", "other")) == []


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
        return await b.aget(("X", None, "day")), redis.pttl(sc._key("sr", ("X", None, "day")))
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
    sc.configure(Settings(database_url="sqlite://", redis_cache_key="k" * 32, **settings))
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
    assert redis.keys("td:market_flow:*") == [sc._key("market_flow", ("KOSPI", "1d"))]


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


# -- #689 security review: the server is shared on one password, so anyone may write under td:


def test_a_value_this_app_did_not_seal_is_a_miss(redis, caplog):
    fake = {"ticker": "005930.KS", "price": 1.0, "currency": "KRW", "source": "toss", "fetched_at": "x"}
    sc.use(redis, None, "another-apps-key-0123456789")
    sc.put("quote", "005930.KS", fake, 60)
    sealed_elsewhere = redis.get(sc._key("quote", "005930.KS"))
    sc.use(redis, None, "this-apps-key-0123456789")
    name = sc._key("quote", "005930.KS")
    for raw in (sealed_elsewhere,                                        # sealed with another key
                json.dumps({"v": fake, "at": time.time()}),               # plain JSON
                "[" * 200_000 + "]" * 200_000,                            # deep nesting
                "x" * (sc.MAX_RAW_CHARS + 1)):                            # too long to open
        redis.set(name, raw, px=3_600_000)
        assert sc.get("quote", "005930.KS") is None
    assert sc.stats()["rejected"] == 4 and sc.stats()["hits"] == 0
    assert sum("failed its seal" in r.message for r in caplog.records) == 1   # one line per process


def test_a_sealed_value_moved_to_another_key_or_replayed_is_a_miss(redis):
    sc.put("quote", "AAPL", {"p": 1}, 10)
    sealed = redis.get(sc._key("quote", "AAPL"))
    redis.set(sc._key("quote", "NVDA"), sealed, px=60_000)                 # moved: names AAPL inside
    assert sc.get("quote", "NVDA") is None
    stale = sc._box.encrypt(json.dumps({"k": "quote:MSFT", "v": {"p": 2}, "at": time.time() - 11,
                                        "ttl": 10.0}).encode()).decode()
    redis.set(sc._key("quote", "MSFT"), stale, px=3_600_000)             # an old value with a long expiry
    assert sc.get("quote", "MSFT") is None
    assert sc.get("quote", "AAPL")[0] == {"p": 1}


def test_what_is_left_comes_from_the_sealed_ttl_not_the_servers_expiry(redis):
    sc.put("quote", "AAPL", {"p": 1}, 10)
    redis.pexpire(sc._key("quote", "AAPL"), 3_600_000)
    assert sc.get("quote", "AAPL")[2] <= 10


def test_a_latest_value_entry_of_the_wrong_shape_is_a_miss():
    assert sc.latest(({"value": {"a": 1}, "ttl": 30}, 1.0, 12.0)) == ({"a": 1}, 30.0, 12.0)
    for bad in ([1, 2], {"value": [1], "ttl": 30}, {"value": {}, "ttl": "nan"}, {"value": {}, "ttl": -1},
                {"value": {}}, {"value": {}, "ttl": 10 ** 9}):
        assert sc.latest((bad, 0.0, 5.0)) is None


def test_a_shared_quote_must_be_for_the_ticker_with_a_real_price():
    from desk.prices import _shared_quote

    good = {"ticker": "AAPL", "price": 101.5, "currency": "USD", "source": "toss", "fetched_at": "t"}
    assert _shared_quote("AAPL", good).price == 101.5
    for bad in ({**good, "ticker": "MSFT"}, {**good, "price": "abc"}, {**good, "price": 0}, {**good, "price": True},
                {**good, "extra": 1}, [1, 2]):
        assert _shared_quote("AAPL", bad) is None
