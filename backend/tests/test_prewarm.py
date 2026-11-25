"""#536 step 2: the prewarm loop, refresh-ahead on the shared caches, and one candle fetch per key."""

import asyncio
import threading
import time
from contextlib import closing

from desk import fear, halving, prewarm, risk_regime, tips
from desk import weekly_map as wm
from desk.broker.mock import MockBroker
from desk.prices import Bar, PriceSource
from tests.test_favorites import OTHER, _app, _login
from tests.test_halving import _history
from tests.test_risk_regime import _dump
from tests.test_tips import EVIDENCE, _series
from tests.test_weekly_map import _bars_obj, _rise

# --- refresh-ahead: a read keeps the fresh value, the prewarm's ``ahead`` refetches it -----------------

def test_the_risk_score_is_refetched_ahead_and_readers_keep_the_old_one():
    calls, now = [], [0.0]
    cache = risk_regime.Cache(ttl_sec=900, fetcher=lambda syms: calls.append(syms) or _dump(), clock=lambda: now[0])
    cache.series(False)
    now[0] = 700.0                                       # 200 s before it expires
    cache.series(False)
    assert len(calls) == 1                               # a reader: still fresh
    cache.series(False, ahead=300)
    assert len(calls) == 2                               # the prewarm: it would expire before the next pass
    cache.series(False, ahead=300)
    assert len(calls) == 2                               # just refetched: fresh even with ``ahead``


def test_the_rate_regime_is_refetched_ahead_without_making_a_reader_wait():
    now, gate, calls = [0.0], threading.Event(), []

    def fetch(symbols):
        calls.append(1)
        if len(calls) == 2:
            gate.wait(5)                                 # the prewarm's refetch hangs a while
        return _series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0)

    c = tips.Cache(fetch, clock=lambda: now[0], evidence=EVIDENCE)
    assert c.regime()["rates"] == "hiking"
    now[0] = c.ttl - 100
    warming = threading.Thread(target=c.regime, kwargs={"ahead": 300})
    warming.start()
    while len(calls) < 2:
        time.sleep(0.01)
    start = time.perf_counter()
    assert c.regime()["rates"] == "hiking"               # the old value at once, not after the refetch
    assert time.perf_counter() - start < 1
    gate.set()
    warming.join()
    assert len(calls) == 2


def test_the_trend_halving_and_weekly_caches_take_ahead():
    from desk import primary_trend

    now = [0.0]
    trend_calls = []
    trend = primary_trend.Cache(lambda syms: trend_calls.append(1) or {}, clock=lambda: now[0])
    trend.view()
    trend.view(ahead=trend.fail_ttl + 1)                 # an empty answer is short-lived: refetched ahead
    assert len(trend_calls) == 2

    halving_calls = []
    hv = halving.Cache(lambda: halving_calls.append(1) or _history(), clock=lambda: now[0])
    hv.get()
    now[0] = halving.TTL_SEC - 100
    hv.get()
    assert len(halving_calls) == 1
    hv.get(ahead=300)
    assert len(halving_calls) == 2

    weekly_calls = []
    maps = wm.Maps(candles=lambda s, i, c: weekly_calls.append(s) or _bars_obj(_rise(90)), clock=lambda: now[0])
    maps.all()
    n = len(weekly_calls)
    now[0] += maps.ttl - 100
    maps.all()
    assert len(weekly_calls) == n
    maps.all(ahead=300)
    assert len(weekly_calls) == 2 * n


def test_the_weekly_targets_are_fetched_side_by_side_in_their_order():
    active, peak, lock = [0], [0], threading.Lock()

    def candles(symbol, interval, count):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return _bars_obj(_rise(90))

    out = wm.Maps(candles=candles).all()["maps"]
    assert [m["symbol"] for m in out] == [s for s, _n, _f in wm.TARGETS]
    assert peak[0] > 1


def test_the_fear_gauges_refetch_ahead_and_answer_a_fresh_read_without_the_lock():
    now, calls = [0.0], []
    f = fear.Fear(candles=lambda *a: [], clock=lambda: now[0])
    def source(k):
        return lambda: calls.append(k) or fear._item(k, k, "test", value=1.0)

    for key in fear.ORDER:
        f._fetch[key] = source(key)

    async def vkospi():
        calls.append("vkospi")
        return fear._item("vkospi", "VKOSPI", "test", value=1.0), fear.TTL_SEC["vkospi"]
    f._vkospi = vkospi

    async def go():
        await f.get()
        n = len(calls)
        now[0] = min(fear.TTL_SEC.values()) - 100        # the shortest-lived source expires in 100 s
        async with f._lock:                              # a refetch holds the lock: a fresh read does not wait
            assert len((await asyncio.wait_for(f.get(), 1))["items"]) == len(fear.ORDER)
        assert len(calls) == n
        await f.get(ahead=300)
        assert len(calls) > n

    asyncio.run(go())


# --- one candle fetch per key -------------------------------------------------------------------------

def test_requests_for_the_same_bars_together_share_one_fetch():
    fetched = []

    class Slow(MockBroker):
        async def candles(self, symbol, interval, count):
            fetched.append(symbol)
            await asyncio.sleep(0.05)
            return [Bar("2026-11-04T00:00:00+00:00", 1, 1, 1, 1, 0)]

    source = PriceSource(Slow())

    async def go():
        a, b = await asyncio.gather(source.candles("NVDA", "1d", 50), source.candles("NVDA", "1d", 50))
        assert a is b and len(fetched) == 1
        await source.candles("NVDA", "1d", 60)            # another key: its own fetch
        assert len(fetched) == 2
        assert not source._inflight

    asyncio.run(go())


def test_a_failed_shared_fetch_fails_every_waiter_and_the_next_ask_tries_again():
    calls = []

    class Down(MockBroker):
        async def candles(self, symbol, interval, count):
            calls.append(1)
            await asyncio.sleep(0.01)
            raise RuntimeError("down")

    def yahoo(ticker, interval, count):
        raise ConnectionError("yahoo down")

    source = PriceSource(Down(), yahoo_candles=yahoo)

    async def go():
        got = await asyncio.gather(source.candles("NVDA", "1d", 50), source.candles("NVDA", "1d", 50),
                                   return_exceptions=True)
        assert all(isinstance(g, ConnectionError) for g in got) and len(calls) == 1
        await asyncio.gather(source.candles("NVDA", "1d", 50), return_exceptions=True)
        assert len(calls) == 2

    asyncio.run(go())


# --- the loop -------------------------------------------------------------------------------------------

def test_a_pass_runs_the_jobs_side_by_side_and_one_failure_stops_nothing():
    seen, ticks = [], []

    async def slow():
        await asyncio.sleep(0.05)

    async def broken():
        raise RuntimeError("down")

    async def ticker_job(t):
        seen.append(t)
        if t == "BAD":
            raise ValueError("nope")

    async def sleep(sec):
        ticks.append(sec)
        if len(ticks) >= 3:                              # the first delay, then two passes
            raise asyncio.CancelledError

    async def go():
        start = time.perf_counter()
        took = await prewarm.warm({"a": slow, "b": slow, "c": broken})
        assert time.perf_counter() - start < 0.09        # side by side, not 0.1 s in a row
        assert took["c"] == "RuntimeError" and took["a"].endswith("ms")
        try:
            await prewarm.run(lambda: {"c": broken}, lambda: ["BAD", "NVDA", *(f"T{i}" for i in range(20))],
                              ticker_job, interval=7, first_delay=1, sleep=sleep)
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert ticks[0] == 1 and all(6 < t <= 7 for t in ticks[1:])   # a fixed rate: the pass's time is taken off
    assert seen[:2] == ["BAD", "NVDA"] and len(seen) == 2 * prewarm.FAVORITES_MAX


def test_the_favorites_tickers_of_every_account_without_repeats(db_url):
    app = _app(db_url)
    with _login(app) as owner, closing(_login(app, OTHER)) as other:
        for t in ("NVDA", "SOXL"):
            owner.post("/api/favorites", json={"ticker": t})
        for t in ("SOXL", "QQQ"):
            other.post("/api/favorites", json={"ticker": t})
    from desk.db import make_engine
    from desk.favorites import Favorites

    engine = make_engine(db_url)
    favorites = Favorites(engine, lambda t: None)
    # each account's first ones first (NVDA · SOXL at position 0), a repeat once
    assert favorites.tickers(10) == ["NVDA", "SOXL", "QQQ"] and favorites.tickers(2) == ["NVDA", "SOXL"]
    engine.dispose()


# --- review fixes ---------------------------------------------------------------------------------------

def test_a_failed_refetch_ahead_keeps_the_values_still_in_date():
    now, ok = [0.0], [True]
    cache = risk_regime.Cache(ttl_sec=900, fetcher=lambda syms: _dump() if ok[0] else {}, clock=lambda: now[0])
    good = cache.series(False)
    now[0], ok[0] = 700.0, False
    assert cache.series(False, ahead=300) == good and cache.current("NVDA")    # still scored
    now[0] = 901.0
    assert cache.series(False) == {}                     # expired for real: the failure shows

    maps = wm.Maps(candles=lambda s, i, c: _bars_obj(_rise(90)) if ok[0] else [], clock=lambda: now[0])
    ok[0], now[0] = True, 0.0
    maps.all()
    ok[0], now[0] = False, maps.ttl - 100
    assert all("error" not in m for m in maps.all(ahead=300)["maps"])

    fetch_ok = [True]
    c = tips.Cache(lambda syms: _series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0) if fetch_ok[0] else {},
                   clock=lambda: now[0], evidence=EVIDENCE)
    now[0] = 0.0
    c.regime()
    fetch_ok[0], now[0] = False, c.ttl - 100
    assert c.regime(ahead=300)["rates"] == "hiking"


def test_the_apps_prewarm_jobs_all_run(db_url, monkeypatch):
    """The lifespan's wiring (names and arguments) - run once against stand-in sources, no network."""
    from desk import fear as fear_mod
    from desk import halving as halving_mod
    from desk import prices as prices_mod
    from tests.test_prices import _client

    bars = [Bar(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}T00:00:00+00:00", 100 + i, 101 + i, 99 + i, 100 + i, 1)
            for i in range(300)]
    monkeypatch.setattr(prices_mod, "yahoo_candles", lambda *a: list(bars))
    monkeypatch.setattr(halving_mod, "yahoo_closes", _history)
    monkeypatch.setattr(fear_mod, "_get_json", lambda *a, **k: {})
    monkeypatch.setattr(fear_mod, "vkospi", lambda: fear_mod._item("vkospi", "VKOSPI", "test", value=1.0))
    source = PriceSource(MockBroker(), yahoo_candles=lambda *a: list(bars))
    with _client(db_url, source, fake_runs=False) as client:
        app = client.app
        took = asyncio.run(prewarm.warm(app.state.prewarm_jobs()))
        assert set(took) == {"tips", "trend", "weekly", "halving", "risk_us", "risk_kr", "fear", "board", "gaps",
                             "bottoms"}
        then = asyncio.run(prewarm.warm(app.state.prewarm_then()))
        assert set(then) == {"playbooks"} and then["playbooks"].endswith("ms"), then
        assert all(v.endswith("ms") for v in took.values()), took
        assert app.state.prewarm_tickers() == []


def test_the_then_jobs_start_after_the_shared_jobs_have_finished():
    """#536 review: the playbooks read the selling score, so they are warmed only once its cache is."""
    order: list[str] = []

    async def risk():
        await asyncio.sleep(0.05)
        order.append("risk done")

    async def playbooks():
        order.append("playbooks start")

    async def sleep(_sec):
        if order:                                        # after the first pass
            raise asyncio.CancelledError

    async def go():
        try:
            await prewarm.run(lambda: {"risk": risk}, list, lambda _t: asyncio.sleep(0),
                              first_delay=0, sleep=sleep, then=lambda: {"playbooks": playbooks})
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert order == ["risk done", "playbooks start"]


def test_the_prewarm_runs_on_its_own_workers_and_a_page_does_not_wait_behind_it():
    """#864 prewarm 이 기본 to_thread 풀을 붙잡지 않는다 — 전용 실행기가 꽉 차도 화면 요청(to_thread)은 바로 돈다."""
    release = threading.Event()
    names = []

    def slow():
        names.append(threading.current_thread().name)
        release.wait(5)

    async def go():
        warming = [asyncio.ensure_future(prewarm.off(slow)) for _ in range(prewarm.WORKERS * 2)]
        await asyncio.sleep(0.05)
        start = time.perf_counter()
        await asyncio.to_thread(lambda: None)                        # a page's read
        waited = time.perf_counter() - start
        release.set()
        await asyncio.gather(*warming)
        return waited

    waited = asyncio.run(go())
    assert waited < 0.5
    assert names and all(n.startswith("desk-prewarm") for n in names)


def test_the_last_pass_is_kept_for_the_status_page():
    async def go():
        async def stop(_):
            raise asyncio.CancelledError
        jobs = {"a": lambda: asyncio.sleep(0)}
        try:
            await prewarm.run(lambda: jobs, list, lambda t: asyncio.sleep(0), first_delay=0,
                              sleep=lambda s: asyncio.sleep(0) if s == 0 else stop(s))
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert set(prewarm.LAST) == {"at", "ms", "jobs", "queued"} and set(prewarm.LAST["jobs"]) == {"a"}


# --- #864 a job's time is its wait for a prewarm worker plus its own work: the pass names the wait --------

def test_warm_notes_each_jobs_wait_for_a_prewarm_worker(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from desk import pools
    one = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-prewarm")
    monkeypatch.setattr(pools, "PREWARM", one)
    queued: dict[str, float] = {}
    jobs = {
        "first": lambda: prewarm.off(time.sleep, 0.2),
        # two pieces side by side (as gaps' tickers): the job's wait is its longest piece's
        "second": lambda: asyncio.gather(prewarm.off(time.sleep, 0.05), prewarm.off(time.sleep, 0.05)),
    }
    try:
        took = asyncio.run(prewarm.warm(jobs, queued))
    finally:
        one.shutdown()
    assert queued["first"] < 50
    assert 180 <= queued["second"] <= 600       # behind "first" on the one worker, then behind its own twin
    assert int(took["second"].removesuffix("ms")) >= queued["second"]
    line = prewarm._line(took, queued)
    assert f"second={took['second']}(q{queued['second']:.0f})" in line
    assert f"first={took['first']} " in line + " "                    # under QUEUED_MIN_MS: not named


def test_off_outside_a_pass_does_not_measure():
    assert asyncio.run(prewarm.off(lambda: 7)) == 7

