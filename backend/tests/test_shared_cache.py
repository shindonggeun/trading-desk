"""#689 the shared cache tier (desk.shared_cache) over fakeredis: what is shared, what is not, and that an
unreachable Redis leaves every cache working from memory as before."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

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
                                             "skipped": 0, "rejected": 0, "errors": 0, "late": 0}


def test_a_value_json_would_change_stays_in_memory(redis, caplog):
    caplog.set_level(logging.INFO, "desk.shared_cache")
    sc.put("ns", "tuple", {"pair": (1, 2)}, 10)       # a tuple comes back a list
    sc.put("ns", "nan", float("nan"), 10)             # not JSON
    sc.put("ns", "nothing", {"a": 1}, 0)              # ttl 0 = keep nothing
    assert redis.keys("*") == [] and sc.stats()["skipped"] == 2
    assert sum("round-trip" in r.message for r in caplog.records) == 1   # once per namespace


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


def test_the_futures_card_preserves_the_collectors_completed_read_time(redis, monkeypatch):
    from desk import futures_flow as ff

    collector_calls, api_calls = [], []
    started = datetime(2026, 11, 6, 2, 0, tzinfo=UTC)
    completed = started + timedelta(seconds=8)
    clock = [started]
    now = lambda: clock[0]

    async def go():
        collector, api = _rest(collector_calls), _rest(api_calls)
        try:
            get = collector.get

            async def source(path, tr, params):
                response = await get(path, tr, params)
                if path == ff.INVESTOR_PATH:
                    clock[0] = completed
                return response

            monkeypatch.setattr(collector, "get", source)
            snapshot = await ff.FuturesFlow(collector, None, now=now).get(fresh=True)
            card = ff.FuturesFlow(api, None, now=now)
            clock[0] += timedelta(seconds=5)
            shared = await card.get()
            redis.flushall()  # the next read can only reuse L1
            clock[0] += timedelta(seconds=3)
            return snapshot, shared, await card.get()
        finally:
            await collector.aclose()
            await api.aclose()

    snapshot, shared, local = asyncio.run(go())
    assert collector_calls and not api_calls
    for age, value in zip((0, 5, 8), (snapshot, shared, local), strict=True):
        assert value["fetched_at"] == completed.astimezone(KST).isoformat()
        assert value["freshness"]["source_updated_at"] is None
        assert value["freshness"]["age_sec"] == age
        assert value["investors"] == snapshot["investors"]


@pytest.mark.parametrize("redis_failure", ["stale_replay", "unavailable"])
def test_the_futures_card_checks_the_source_when_redis_has_no_usable_value(redis, monkeypatch, redis_failure):
    from desk import futures_flow as ff

    now = lambda: datetime(2026, 11, 6, 2, 0, tzinfo=UTC)
    api_calls = []

    async def go():
        if redis_failure == "stale_replay":
            real_time = time.time
            with monkeypatch.context() as old:
                old.setattr(sc, "time", SimpleNamespace(time=lambda: real_time() - sc.LATEST_KEEP_SEC - 1,
                                                        monotonic=time.monotonic))
                await sc.aput(ff.SHARED_NS, "view", {"value": {"fetched_at": "stale"}, "ttl": ff.CACHE_OPEN_SEC},
                              sc.LATEST_KEEP_SEC)
            assert redis.pttl(sc._key(ff.SHARED_NS, "view")) > 0
        else:
            monkeypatch.setattr(sc._async, "pipeline", Mock(side_effect=ConnectionError("fake Redis down")))
        api = _rest(api_calls)
        try:
            return await ff.FuturesFlow(api, None, now=now).get()
        finally:
            await api.aclose()

    card = asyncio.run(go())
    assert any(call.url.path == ff.INVESTOR_PATH for call in api_calls)
    assert card["fetched_at"] == now().astimezone(KST).isoformat()
    assert card["freshness"]["source_updated_at"] is None


def test_the_futures_partial_error_keeps_the_last_shared_confirmation(redis, monkeypatch):
    from desk import futures_flow as ff

    started = datetime(2026, 11, 6, 2, 0, tzinfo=UTC)
    clock = [started]
    now = lambda: clock[0]
    api_calls = []

    async def go():
        collector, api = _rest([]), _rest(api_calls)
        try:
            flow = ff.FuturesFlow(collector, None, now=now)
            good = await flow.get(fresh=True)
            clock[0] += timedelta(seconds=10)
            get = collector.get

            async def source(path, tr, params):
                if path == ff.INVESTOR_PATH:
                    clock[0] += timedelta(seconds=2)
                    raise ff.KisError("synthetic investor failure")
                return await get(path, tr, params)

            monkeypatch.setattr(collector, "get", source)
            partial = await flow.get(fresh=True)
            card = await ff.FuturesFlow(api, None, now=now).get()
            return good, partial, card
        finally:
            await collector.aclose()
            await api.aclose()

    good, partial, card = asyncio.run(go())
    assert partial["price"] and partial["investors_error"] and partial["investors"] is None
    assert partial["fetched_at"] == (started + timedelta(seconds=12)).astimezone(KST).isoformat()
    assert not api_calls and card["fetched_at"] == good["fetched_at"]
    assert card["investors"] == good["investors"]
    assert partial["freshness"]["source_updated_at"] is None


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
    stale = sc._box.encrypt(json.dumps({"k": sc._plain("quote", "MSFT"), "v": {"p": 2}, "at": time.time() - 11,
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


def test_the_card_follows_what_is_left_of_the_shared_value_then_fetches_itself(redis, monkeypatch):
    clock = [1000.0]
    collector, api = CountingBroker(), CountingBroker()
    card = MarketFlow(api, clock=lambda: clock[0], now=lambda: NOW)
    real = time.time

    async def go():
        monkeypatch.setattr(sc, "time", SimpleNamespace(time=lambda: real() - 50, monotonic=time.monotonic))
        await MarketFlow(collector, now=lambda: NOW).get("KOSPI", "1d", 5, fresh=True)   # written 50 s ago
        kept = redis.pttl(redis.keys("td:market_flow:*")[0])
        monkeypatch.setattr(sc, "time", time)
        await card.get("KOSPI", "1d", 5)               # 90 - 50 = 40 s left: kept 40 s, not the card's 60
        redis.flushall()
        clock[0] += 39
        await card.get("KOSPI", "1d", 5)
        before = api.calls
        clock[0] += 2
        await card.get("KOSPI", "1d", 5)               # expired: its own read
        return kept, before

    kept, before = asyncio.run(go())
    assert before == 0 and api.calls == 1
    assert 60_000 < kept <= 90_000                      # shared past one snapshot gap, not only the card's 60 s


def test_a_failed_futures_read_is_not_shared(redis):
    from desk import futures_flow as ff

    now = lambda: datetime(2026, 11, 6, 2, 0, tzinfo=UTC)
    asyncio.run(ff.FuturesFlow(_rest([], token_status=500), None, now=now).get(fresh=True))
    assert redis.keys("td:futures_flow:*") == []


def test_another_build_never_reads_this_ones_values(redis, monkeypatch):
    sc.put("osc", ("A", "1d"), {"r": 1}, 60)
    monkeypatch.setattr(sc, "BUILD", "another-build")
    assert sc.get("osc", ("A", "1d")) is None and sc.stats()["rejected"] == 0   # another name: a plain miss


def test_build_keys_are_named_and_sealed_as_before_the_versioned_head(redis):
    """#993 the head moved into a parameter: a build's key name and sealed ``k`` are byte for byte what they were."""
    assert sc._plain("sr", ("NVDA", None, "swing")) == sc.BUILD + "|sr:NVDA::swing"
    assert sc._key("quote", "AAPL") == "td:quote:" + hmac.new(
        sc._name_key, f"{sc.BUILD}|quote:AAPL".encode(), hashlib.sha256).hexdigest()[:32]
    sc.put("quote", "AAPL", {"p": 1}, 10)
    env = json.loads(sc._box.decrypt(redis.get(sc._key("quote", "AAPL")).encode()))
    assert env["k"] == f"{sc.BUILD}|quote:AAPL" and env["v"] == {"p": 1} and env["ttl"] == 10.0
    assert sc._plain("quote", "AAPL", sc._versioned(1)) == "v1|quote:AAPL" != sc._plain("quote", "AAPL")


def test_a_versioned_value_outlives_a_deploy_and_a_version_bump_misses(redis, monkeypatch):
    bars = {"day": "2026-11-25", "at": ["2026-11-24T05:00:00+00:00"], "close": [101.25]}
    sc.put_versioned("sector_bars", 1, "SPY", bars, 3600)
    [name] = redis.keys("*")
    assert name == sc._key("sector_bars", "SPY", "v1") and name.startswith("td:sector_bars:") and "SPY" not in name
    assert "101.25" not in redis.get(name) and 0 < redis.pttl(name) <= 3_600_000
    monkeypatch.setattr(sc, "BUILD", "the-next-deploy")                 # every desk/*.py change rotates it
    value, age, left = sc.get_versioned("sector_bars", 1, "SPY")
    assert value == bars and 0 <= age < 5 and 3590 < left <= 3600
    assert sc.get_versioned("sector_bars", 2, "SPY") is None             # its shape changed: bumped, never read
    assert sc.get("sector_bars", "SPY") is None                           # nor a build's key
    sc.put("sector_bars", "QQQ", bars, 60)
    assert sc.get_versioned("sector_bars", 1, "QQQ") is None              # ...nor the other way
    assert sc.stats()["rejected"] == 0 and sc.stats()["hits"] == 1


def test_a_versioned_value_moved_replayed_or_from_the_future_is_a_miss(redis):
    sc.put_versioned("sector_bars", 1, "SPY", {"c": [1.0]}, 60)
    redis.set(sc._key("sector_bars", "QQQ", "v1"), redis.get(sc._key("sector_bars", "SPY", "v1")), px=60_000)
    assert sc.get_versioned("sector_bars", 1, "QQQ") is None              # moved: names SPY inside
    sc.put("sector_bars", "IWM", {"c": [2.0]}, 60)
    redis.set(sc._key("sector_bars", "IWM", "v1"), redis.get(sc._key("sector_bars", "IWM")), px=60_000)
    assert sc.get_versioned("sector_bars", 1, "IWM") is None              # a build's value under the version's name

    def sealed(key: str, at: float) -> str:
        return sc._box.encrypt(json.dumps({"k": f"v1|sector_bars:{key}", "v": {"c": [3.0]}, "at": at,
                                           "ttl": 10.0}).encode()).decode()

    redis.set(sc._key("sector_bars", "DIA", "v1"), sealed("DIA", time.time() - 11), px=3_600_000)
    assert sc.get_versioned("sector_bars", 1, "DIA") is None              # older than its own ttl
    redis.set(sc._key("sector_bars", "XLK", "v1"), sealed("XLK", time.time() + sc.SKEW_SEC + 30), px=3_600_000)
    assert sc.get_versioned("sector_bars", 1, "XLK") is None              # written "later" than clocks can skew
    assert sc.stats()["rejected"] == 4 and sc.get_versioned("sector_bars", 1, "SPY")[0] == {"c": [1.0]}


def test_versioned_async_calls_and_the_loop_thread(redis, monkeypatch):
    async def go():
        await sc.aput_versioned("sector_bars", 1, "SPY", {"c": [1.5]}, 60)
        monkeypatch.setattr(sc, "BUILD", "the-next-deploy")
        sc.put_versioned("sector_bars", 1, "QQQ", {"c": [2.5]}, 60)    # skipped here: aput is the loop's call
        return (await sc.aget_versioned("sector_bars", 1, "SPY"), sc.get_versioned("sector_bars", 1, "SPY"),
                await sc.aget_versioned("sector_bars", 3, "SPY"))

    hit, blocked, bumped = asyncio.run(go())
    assert hit[0] == {"c": [1.5]} and blocked is None and bumped is None
    assert redis.keys(sc._key("sector_bars", "QQQ", "v1")) == []
    assert sc.get_versioned("sector_bars", 1, "SPY")[0] == {"c": [1.5]}  # a worker thread's read


@pytest.mark.parametrize("version", [0, -1, True, "1", 1.0, None])
def test_a_version_is_a_positive_int(redis, version):
    with pytest.raises(ValueError):
        sc.get_versioned("sector_bars", version, "SPY")
    with pytest.raises(ValueError):
        sc.put_versioned("sector_bars", version, "SPY", {"c": [1.0]}, 60)


def test_versioned_calls_skip_a_down_server_and_keep_the_size_cap(redis):
    sc.put_versioned("sector_bars", 1, "BIG", {"c": [1.0] * (sc.MAX_VALUE_BYTES // 4)}, 60)
    assert redis.keys("*") == [] and sc.stats()["skipped"] == 1          # too big: memory only
    broken = _Broken()
    sc.use(broken, None)
    try:
        assert sc.get_versioned("sector_bars", 1, "SPY") is None
        sc.put_versioned("sector_bars", 1, "SPY", {"c": [1.0]}, 60)
        assert broken.calls == 1 and sc.stats()["errors"] == 1            # down: the next call never reaches it
    finally:
        sc.use(None, None)


def test_a_slow_server_costs_at_most_the_timeout_on_the_loop():
    class Slow:
        def pipeline(self, transaction=False):
            return self

        def get(self, *_):
            pass

        pttl = get

        async def execute(self):
            await asyncio.sleep(5)

        async def set(self, *args, **kwargs):
            await asyncio.sleep(5)

    sc.use(None, Slow())
    try:
        async def go():
            started = time.monotonic()
            got = await sc.aget("ns", "k")
            return got, time.monotonic() - started
        got, took = asyncio.run(go())
        assert got is None and took < 1 and sc.stats()["errors"] == 1
    finally:
        sc.use(None, None)


def test_broker_calls_are_counted_where_they_are_made():
    from tests.test_toss_token import FakeToss, _broker

    outbound.reset()
    asyncio.run(_broker(FakeToss()).quote("NVDA"))
    calls = []

    async def kis():
        await _rest(calls).get("/x", "TR", {})
    asyncio.run(kis())
    snap = outbound.snapshot()
    assert snap["toss"] == 1 and snap["kis"] == 1


class _Silent:
    """An async client that never answers (a dead socket): every call waits past the bound."""

    def pipeline(self, transaction=False):
        return self

    def get(self, *args):
        return self

    def pttl(self, *args):
        return self

    async def execute(self):
        await asyncio.sleep(10)

    async def set(self, *args, **kwargs):
        await asyncio.sleep(10)


def test_a_timeout_the_blocked_loop_caused_is_a_miss_not_an_outage(monkeypatch):
    """#993 right after a restart the event loop can be blocked for seconds (the first holiday build): a read
    that wakes that late is a miss, and the tier stays up for every other path."""
    sc.use(None, _Silent(), secret="0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(sc, "_down_until", 0.0)
    before = dict(sc.stats())

    async def blocked():
        task = asyncio.create_task(sc.aget("ns", "k"))
        await asyncio.sleep(0)                               # the read is waiting on Redis ...
        time.sleep(sc.TIMEOUT_SEC * (sc.LATE_FACTOR + 1))  # noqa: ASYNC251 - the loop blocked past the bound is the case
        return await task

    try:
        assert asyncio.run(blocked()) is None
        after = sc.stats()
        assert after["late"] == before["late"] + 1 and after["errors"] == before["errors"] and sc._up()
        assert asyncio.run(sc.aget("ns", "k")) is None       # a free loop: the silence is Redis - down
        assert sc.stats()["errors"] == before["errors"] + 1 and not sc._up()
    finally:
        sc.use(None, None)
        monkeypatch.setattr(sc, "_down_until", 0.0)


def test_the_holiday_tables_are_built_ahead():
    from datetime import date

    from desk import market_calendar
    market_calendar._closed.cache_clear()
    market_calendar.warm(date(2026, 11, 25))
    assert market_calendar._closed.cache_info().currsize == 6
    assert market_calendar.holiday("kr", date(2026, 12, 25)) is not None


def test_a_failed_holiday_build_does_not_stop_the_start(db_url, monkeypatch, caplog):
    from desk import market_calendar
    from tests.test_api import _client

    def broken():
        raise RuntimeError("holidays: secret-ish detail")

    monkeypatch.setattr(market_calendar, "warm", broken)
    with _client(db_url) as client:
        assert client.get("/api/health").status_code == 200
    assert "holiday tables not built ahead: RuntimeError" in caplog.text
    assert "secret-ish detail" not in caplog.text                # the type name only


SECRET = "0123456789abcdef0123456789abcdef"


class _Blocking:
    """An async client whose calls block the event loop past the bound (a GIL-heavy worker on a busy restart),
    then wait for an answer that never comes - or raise ``fail``. ``ok``: answer at once instead."""

    def __init__(self, fail: BaseException | None = None):
        self.fail, self.ok = fail, False

    def pipeline(self, transaction=False):
        return self

    def get(self, *args):
        return self

    def pttl(self, *args):
        return self

    async def _late(self, answer):
        if self.ok:
            return answer
        time.sleep(sc.TIMEOUT_SEC * (sc.LATE_FACTOR + 1))   # noqa: ASYNC251 - the loop blocked past the bound
        if self.fail is not None:
            raise self.fail
        await asyncio.sleep(10)

    async def execute(self):
        return await self._late([None, -2])

    async def set(self, *args, **kwargs):
        return await self._late(True)


@pytest.fixture
def blocking(monkeypatch):
    monkeypatch.setattr(sc, "TIMEOUT_SEC", 0.02)            # a late call costs ~60 ms here
    client = _Blocking()
    sc.use(None, client, SECRET)
    yield client
    sc.use(None, None)


def test_late_timeouts_in_a_row_mark_the_tier_down_after_all(blocking):
    """#993 리뷰: 늦게 깨는 타임아웃이 계속되면 막힌 루프가 아니라 늦게만 답하는 Redis 와 같다 - 결국 down."""
    async def go():
        for _ in range(sc.LATE_STREAK - 1):
            assert await sc.aget("ns", "k") is None
        up_before = sc._up()
        assert await sc.aget("ns", "k") is None
        return up_before

    assert asyncio.run(go()) is True
    assert sc.stats()["late"] == sc.LATE_STREAK and sc.stats()["errors"] == 1 and not sc._up()


@pytest.mark.parametrize("landed", ["aget", "aput", "aput_newer"])
def test_a_call_that_lands_starts_the_late_streak_again(blocking, monkeypatch, landed):
    fake = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)

    async def go():
        for step in ("late", "late", landed, "late", "late"):
            if step == "late":
                assert await sc.aget("ns", "k") is None
                continue
            monkeypatch.setattr(sc, "_async", fake)              # this one call answers in time
            if step == "aget":
                await sc.aget("ns", "k")
            elif step == "aput":
                assert await sc.aput("ns", "k", {"a": 1}, 10) is True
            else:
                await sc.aput_newer("ns", "k", {"t": 1}, 10, rank=lambda v: v["t"])
            monkeypatch.setattr(sc, "_async", blocking)

    asyncio.run(go())
    assert sc.stats()["late"] == 4 and sc.stats()["errors"] == 0 and sc._up()


def test_a_late_write_is_counted_late_and_reported_unwritten(blocking):
    assert asyncio.run(sc.aput("ns", "k", {"a": 1}, 10)) is False
    assert sc.stats()["late"] == 1 and sc.stats()["errors"] == 0 and sc.stats()["writes"] == 0 and sc._up()
    blocking.ok = True
    assert asyncio.run(sc.aput("ns", "k", {"a": 1}, 10)) is True and sc.stats()["writes"] == 1


def test_a_late_redis_timeout_is_still_an_outage(blocking):
    """redis' own TimeoutError (its socket timed out) is Redis not answering, however late the loop woke."""
    from redis.exceptions import TimeoutError as RedisTimeout

    blocking.fail = RedisTimeout("socket")
    assert asyncio.run(sc.aget("ns", "k")) is None
    assert sc.stats()["late"] == 0 and sc.stats()["errors"] == 1 and not sc._up()


def test_aput_says_whether_it_wrote(redis):
    async def go():
        return (await sc.aput("ns", "k", {"a": 1}, 10), await sc.aput("ns", "k", {"a": 1}, 0),
                await sc.aput("ns", "k", {"pair": (1, 2)}, 10),
                await sc.aput_versioned("sector_bars", 1, "SPY", {"c": [1.0]}, 10))

    assert asyncio.run(go()) == (True, False, False, True)
    sc.use(None, None)
    assert asyncio.run(sc.aput("ns", "k", {"a": 1}, 10)) is False           # off


def test_a_pttl_reply_that_is_no_number_is_a_miss(redis):
    sc.put("quote", "AAPL", {"p": 1}, 10)
    raw = redis.get(sc._key("quote", "AAPL"))
    assert sc._decode("quote", "AAPL", raw, "soon") is None and sc.stats()["rejected"] == 1
    assert sc._decode("quote", "AAPL", raw, [5000]) is None and sc.stats()["rejected"] == 2
    assert sc._decode("quote", "AAPL", raw, 5000)[0] == {"p": 1}
