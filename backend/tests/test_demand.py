"""#689 demand-driven collection (desk.demand): the api writes what its screens read, the collector serves it."""

import asyncio
import time
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
        return await real(*args, **kwargs)

    monkeypatch.setattr(sc, "aput", counted)
    demand.want("quote", "005930")
    asyncio.run(demand.flush())
    assert redis.keys("td:want:*")
    clock[0] += 2
    demand.want("quote", "005930")
    asyncio.run(demand.flush())                       # same set, 2 s later: kept
    demand.want("quote", "NVDA")
    asyncio.run(demand.flush())                       # a new key: written at once
    clock[0] += demand.FLUSH_EVERY
    demand.want("quote", "NVDA")
    asyncio.run(demand.flush())                       # unchanged but due again
    assert len(writes) == 3


def test_a_write_that_woke_late_is_sent_again_at_the_next_flush(redis, clock, monkeypatch):
    """#993 a late timeout (the loop was blocked) leaves the tier up but the set unwritten: not counted as sent."""
    class LateOnce:
        def __init__(self, real):
            self.real, self.late = real, True

        def __getattr__(self, name):
            return getattr(self.real, name)

        async def set(self, *args, **kwargs):
            if self.late:
                self.late = False
                time.sleep(sc.TIMEOUT_SEC * (sc.LATE_FACTOR + 2))   # noqa: ASYNC251 - the loop blocked meanwhile
                await asyncio.sleep(10)
            return await self.real.set(*args, **kwargs)

    demand.configure("api")
    monkeypatch.setattr(sc, "TIMEOUT_SEC", 0.02)
    monkeypatch.setattr(sc, "_async", LateOnce(sc._async))
    demand.want("quote", "005930")
    asyncio.run(demand.flush())
    assert redis.keys("td:want:*") == [] and sc._up() and sc.stats()["late"] == 1
    clock[0] += 2
    demand.want("quote", "005930")
    asyncio.run(demand.flush())                       # the same set 2 s later: written now, not "already sent"
    assert redis.keys("td:want:*") and demand.stats()["flushes"] == 1


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


# --- #994 a namespace's due keys in one batch call ---------------------------------------------------------


def test_a_batch_namespace_is_served_in_one_call_beside_the_per_key_handlers(redis, clock):
    _wanted(redis, {"quote": ["005930", "NVDA", "AAPL"], "futures_quote": ["F:quote"]})
    calls = []

    async def per_key(key):
        calls.append(("quote", key))                                 # the batch takes the namespace

    async def futures(key):
        calls.append(("futures_quote", key))

    async def quotes(keys):
        calls.append(("batch", tuple(sorted(keys))))
        return set()

    handlers, batch = {"quote": per_key, "futures_quote": futures}, {"quote": quotes}
    assert asyncio.run(demand.serve(handlers, batch=batch)) == 4
    assert sorted(calls) == [("batch", ("005930", "AAPL", "NVDA")), ("futures_quote", "F:quote")]
    assert asyncio.run(demand.serve(handlers, batch=batch)) == 0      # not due yet
    clock[0] += demand.EVERY["quote"]
    assert asyncio.run(demand.serve(handlers, batch=batch)) == 3      # every quote again, in one call
    assert calls[-1] == ("batch", ("005930", "AAPL", "NVDA"))
    assert demand.stats()["served"] == 7 and demand.stats()["failed"] == 0


def test_keys_a_batch_answers_as_failed_back_off_alone(redis, clock):
    _wanted(redis, {"quote": ["BAD", "OK1", "OK2"]})
    asked = []

    async def quotes(keys):
        asked.append(sorted(keys))
        return {"BAD", "NOT-ASKED"}                                  # a key it was not given counts for nothing

    assert asyncio.run(demand.serve({}, batch={"quote": quotes})) == 2
    assert demand.stats()["served"] == 2 and demand.stats()["failed"] == 1
    clock[0] += demand.EVERY["quote"]
    asyncio.run(demand.serve({}, batch={"quote": quotes}))
    assert asked[-1] == ["OK1", "OK2"]                                # BAD waits twice the period
    clock[0] += demand.EVERY["quote"]
    asyncio.run(demand.serve({}, batch={"quote": quotes}))
    assert asked[-1] == ["BAD", "OK1", "OK2"]


@pytest.mark.parametrize("failure", ["raises", "slow"])
def test_a_failed_or_slow_batch_call_backs_off_every_key_in_it(redis, clock, monkeypatch, failure):
    monkeypatch.setattr(demand, "HANDLER_SEC", 0.05)
    _wanted(redis, {"quote": ["A", "B", "C"], "futures_quote": ["F:quote"]})
    asked, futures = [], []

    async def quotes(keys):
        asked.append(sorted(keys))
        if failure == "raises":
            raise RuntimeError("toss 500")
        await asyncio.sleep(1)
        return set()

    async def per_future(key):
        futures.append(key)

    handlers = {"futures_quote": per_future}
    assert asyncio.run(demand.serve(handlers, batch={"quote": quotes})) == 1     # the other namespace goes on
    assert demand.stats()["failed"] == 3 and futures == ["F:quote"]
    clock[0] += demand.EVERY["quote"] - 1
    asyncio.run(demand.serve(handlers, batch={"quote": quotes}))
    assert len(asked) == 1
    clock[0] += 1
    asyncio.run(demand.serve(handlers, batch={"quote": quotes}))
    # a whole call's first failure waits one period only (the shared value outlives it), not twice
    assert asked == [["A", "B", "C"], ["A", "B", "C"]]
    clock[0] += 2 * demand.EVERY["quote"]
    asyncio.run(demand.serve(handlers, batch={"quote": quotes}))
    assert len(asked) == 2                                           # failed twice: four times the period
    clock[0] += 2 * demand.EVERY["quote"]
    asyncio.run(demand.serve(handlers, batch={"quote": quotes}))
    assert len(asked) == 3


def test_a_batch_call_takes_one_place_of_the_gate(redis):
    _wanted(redis, {"quote": ["A", "B", "C"], "futures_quote": ["F1", "F2", "F3"]})
    active, peak, batches = [0], [0], []

    async def busy():
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1

    async def quotes(keys):
        batches.append(len(keys))
        await busy()
        return set()

    async def per_future(key):
        await busy()

    assert asyncio.run(demand.serve({"futures_quote": per_future}, batch={"quote": quotes})) == 6
    assert batches == [3] and peak[0] == demand.PARALLEL


def test_a_batch_past_the_pass_time_waits_for_the_next_pass(redis, clock, monkeypatch):
    _wanted(redis, {"quote": ["A", "B"]})
    asked = []

    async def quotes(keys):
        asked.append(sorted(keys))
        return set()

    monkeypatch.setattr(demand, "PASS_SEC", -1.0)                   # the pass is over before it starts one
    assert asyncio.run(demand.serve({}, batch={"quote": quotes})) == 0
    assert asked == [] and demand.stats()["failed"] == 0
    monkeypatch.setattr(demand, "PASS_SEC", 10.0)
    assert asyncio.run(demand.serve({}, batch={"quote": quotes})) == 2   # still due


def test_a_batch_key_is_due_again_a_period_after_its_pass_started(redis, clock):
    """A slow pass does not stretch the period: due again EVERY after the pass began, not after the call ended."""
    _wanted(redis, {"quote": ["A", "B"]})
    asked = []

    async def quotes(keys):
        asked.append(sorted(keys))
        clock[0] += 4.0                                              # a 4 s pass
        return set()

    started = clock[0]
    assert asyncio.run(demand.serve({}, batch={"quote": quotes})) == 2
    clock[0] = started + demand.EVERY["quote"] - 0.5
    assert asyncio.run(demand.serve({}, batch={"quote": quotes})) == 0
    clock[0] = started + demand.EVERY["quote"]
    assert asyncio.run(demand.serve({}, batch={"quote": quotes})) == 2   # 10 s on, not 14
    assert asked == [["A", "B"], ["A", "B"]]
