"""#689 demand-driven collection (desk.demand): the api writes what its screens read, the collector serves it."""

import asyncio
from types import SimpleNamespace

import fakeredis
import pytest

from desk import demand
from desk import shared_cache as sc


@pytest.fixture
def redis():
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    sc.use(sync, fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    token = demand.SCREEN.set(True)                  # these reads stand for a screen's
    yield sync
    demand.SCREEN.reset(token)
    sc.use(None, None)
    demand.configure("")


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    # demand's own name only: patching time.monotonic itself would stop the event loop's clock too
    monkeypatch.setattr(demand, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_off_without_redis_or_outside_the_split():
    demand.configure("api")
    assert not demand.enabled()                       # no Redis: one process answers from its own fetches
    demand.want("quote", "005930")
    assert demand.stats()["wanted"] == 0
    demand.configure("")


def test_off_in_one_process(redis):
    demand.configure("all")
    demand.want("quote", "005930")
    asyncio.run(demand.flush())
    assert not demand.enabled() and not demand.serving() and redis.keys("td:want:*") == []


def test_the_api_writes_what_was_read_under_one_name(redis):
    demand.configure("api")
    demand.want("quote", "005930")
    demand.want("quote", "NVDA")
    demand.want("futures_quote", "101S3000:quote")
    demand.want("nope", "x")                          # not a collected namespace
    demand.want("quote", "")                          # nor an empty key
    asyncio.run(demand.flush())
    keys = redis.keys("td:*")
    assert len(keys) == 1 and keys[0].startswith("td:want:")
    assert "005930" not in keys[0] and "NVDA" not in keys[0]
    assert 0 < redis.pttl(keys[0]) <= demand.WANT_TTL * 1000
    demand.configure("collector")
    assert asyncio.run(demand.read()) == {"futures_quote": ["101S3000:quote"], "quote": ["005930", "NVDA"]}


def test_nothing_read_lately_writes_nothing(redis, clock):
    demand.configure("api")
    demand.want("quote", "005930")
    clock[0] += demand.KEEP_SEC + 1
    asyncio.run(demand.flush())
    assert redis.keys("td:want:*") == []


def test_an_unchanged_set_is_rewritten_only_every_flush_period(redis, clock, monkeypatch):
    demand.configure("api")
    writes = []
    real = sc.aput

    async def counted(*args, **kwargs):
        writes.append(args)
        await real(*args, **kwargs)

    monkeypatch.setattr(sc, "aput", counted)
    demand.want("quote", "005930")
    asyncio.run(demand.flush())
    clock[0] += 2
    demand.want("quote", "005930")
    asyncio.run(demand.flush())                       # same set, 2 s later: kept
    demand.want("quote", "NVDA")
    asyncio.run(demand.flush())                       # a new key: written at once
    clock[0] += demand.FLUSH_EVERY
    demand.want("quote", "NVDA")
    asyncio.run(demand.flush())                       # unchanged but due again
    assert len(writes) == 3


def test_the_wanted_set_is_capped(redis):
    demand.configure("api")
    for i in range(demand.MAX_KEYS + 5):
        demand.want("quote", f"T{i}")
    assert demand.stats()["wanted"] == demand.MAX_KEYS and demand.stats()["dropped"] == 5


def test_a_value_not_written_by_the_api_reads_as_nothing(redis):
    demand.configure("collector")
    for bad in ({"quote": "005930"}, {"orders": ["x"]}, {"quote": [1]}, ["005930"],
                {"quote": [f"T{i}" for i in range(demand.MAX_KEYS + 1)]}):
        asyncio.run(sc.aput(demand.WANT_NS, demand.WANT_KEY, bad, 20))
        assert asyncio.run(demand.read()) == {}


def _wanted(redis, wants: dict):
    demand.configure("api")
    for ns, keys in wants.items():
        for k in keys:
            demand.want(ns, k)
    asyncio.run(demand.flush())
    demand.configure("collector")


def test_the_collector_serves_each_key_on_its_period(redis, clock):
    _wanted(redis, {"quote": ["005930", "NVDA"], "futures_quote": ["F:quote"]})
    calls = []

    async def quote(key):
        calls.append(("quote", key))

    async def futures(key):
        calls.append(("futures_quote", key))

    handlers = {"quote": quote, "futures_quote": futures}
    assert asyncio.run(demand.serve(handlers)) == 3
    assert asyncio.run(demand.serve(handlers)) == 0                  # not due yet
    clock[0] += demand.EVERY["quote"]
    assert asyncio.run(demand.serve(handlers)) == 2                  # quotes due again, futures not
    assert sorted(calls[:3]) == [("futures_quote", "F:quote"), ("quote", "005930"), ("quote", "NVDA")]


def test_a_failing_key_backs_off_and_leaves_the_others(redis, clock):
    _wanted(redis, {"quote": ["BAD", "OK"]})
    seen = []

    async def quote(key):
        seen.append(key)
        if key == "BAD":
            raise RuntimeError("toss 500")

    assert asyncio.run(demand.serve({"quote": quote})) == 1
    clock[0] += demand.EVERY["quote"]
    asyncio.run(demand.serve({"quote": quote}))
    assert seen.count("OK") == 2 and seen.count("BAD") == 1           # BAD waits twice the period
    assert demand.stats()["failed"] == 1


def test_the_collector_stops_when_the_api_stops_writing(redis, clock):
    _wanted(redis, {"quote": ["005930"]})
    calls = []

    async def quote(key):
        calls.append(key)

    asyncio.run(demand.serve({"quote": quote}))
    redis.flushall()                                                 # the wanted set ran out
    clock[0] += demand.EVERY["quote"]
    assert asyncio.run(demand.serve({"quote": quote})) == 0 and calls == ["005930"]
    assert demand.stats()["collecting"] == 0


def test_the_api_never_serves(redis):
    demand.configure("api")
    assert asyncio.run(demand.serve({"quote": lambda k: None})) == 0


def test_a_background_read_marks_nothing(redis):
    demand.configure("api")
    token = demand.SCREEN.set(False)                 # prewarm's playbooks, every few minutes with no screen
    try:
        demand.want("quote", "005930")
    finally:
        demand.SCREEN.reset(token)
    assert demand.stats()["wanted"] == 0


def test_only_reads_inside_a_request_mark(redis):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def read(request):
        demand.want("quote", request.query_params["t"])
        return JSONResponse({})

    token = demand.SCREEN.set(False)                 # outside: the test's own context, like a background job
    try:
        demand.configure("api")
        app = Starlette(routes=[Route("/q", read)])
        app.add_middleware(demand.ScreenReads)
        with TestClient(app) as client:
            client.get("/q", params={"t": "NVDA"})
        demand.want("quote", "005930")
        assert demand.stats()["wanted"] == 1 and ("quote", "NVDA") in demand._seen
    finally:
        demand.SCREEN.reset(token)


def test_a_flush_lost_to_a_redis_outage_is_written_once_redis_is_back(redis, monkeypatch, clock):
    demand.configure("api")
    demand.want("quote", "005930")
    monkeypatch.setattr(sc, "_up", lambda: False)    # the write did not land
    asyncio.run(demand.flush())
    monkeypatch.setattr(sc, "_up", lambda: True)
    redis.flushall()
    clock[0] += 2
    asyncio.run(demand.flush())                      # same set 2 s later: written now, not FLUSH_EVERY later
    assert redis.keys("td:want:*")


def test_configure_starts_from_nothing_sent(redis):
    demand.configure("api")
    demand.want("quote", "005930")
    asyncio.run(demand.flush())
    demand.configure("api")
    assert demand._sent == (frozenset(), 0.0) and demand.stats()["flushes"] == 0


def test_a_slow_fetch_is_cut_and_backed_off(redis, clock, monkeypatch):
    monkeypatch.setattr(demand, "HANDLER_SEC", 0.05)
    _wanted(redis, {"quote": ["SLOW"]})

    async def slow(key):
        await asyncio.sleep(1)

    assert asyncio.run(demand.serve({"quote": slow})) == 0
    assert demand.stats()["failed"] == 1


def test_an_expired_want_is_ignored(redis):
    import time as real_time
    demand.configure("collector")
    asyncio.run(sc.aput(demand.WANT_NS, demand.WANT_KEY, {"quote": ["005930"]}, 0.05))
    real_time.sleep(0.1)
    assert asyncio.run(demand.read()) == {}
