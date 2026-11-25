"""#874 desk.pools: each kind of blocking work on threads of its own."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

from desk import api, pools, prewarm

_SEEN: ContextVar[str] = ContextVar("seen", default="")


def test_run_takes_the_callers_context_to_the_pools_thread():
    def where() -> tuple[str, str]:
        return threading.current_thread().name, _SEEN.get()

    async def go():
        _SEEN.set("caller")
        return [await pools.run(pool, where) for pool in (pools.SOURCE, pools.COMPUTE, pools.PREWARM,
                                                          pools.ADMISSION)]

    got = asyncio.run(go())
    assert [name.split("_")[0] for name, _ in got] == ["desk-source", "desk-compute", "desk-prewarm",
                                                       "desk-admission"]
    assert all(seen == "caller" for _, seen in got)


def test_a_page_does_not_wait_behind_the_sources_reads():
    """#874 원천 읽기가 SOURCE 를 꽉 채워도(그 두 배가 줄 서도) 화면의 to_thread 와 요청 입장 검사는 바로 돈다."""
    release = threading.Event()

    async def go():
        reading = [asyncio.ensure_future(pools.run(pools.SOURCE, release.wait, 5))
                   for _ in range(pools.SOURCE_WORKERS * 2)]
        await asyncio.sleep(0.05)
        start = time.perf_counter()
        await asyncio.to_thread(lambda: None)                        # a page's read
        page = time.perf_counter() - start
        start = time.perf_counter()
        name = await api._admit(lambda: threading.current_thread().name)      # a request's session check
        admitted = time.perf_counter() - start
        release.set()
        await asyncio.gather(*reading)
        return page, admitted, name

    page, admitted, name = asyncio.run(go())
    assert page < 0.5 and admitted < 0.5
    assert name.startswith("desk-admission")


def test_the_prewarm_keeps_its_own_workers():
    assert prewarm.WORKERS == pools.PREWARM_WORKERS == pools.PREWARM._max_workers
    name = asyncio.run(prewarm.off(lambda: threading.current_thread().name))
    assert name.startswith("desk-prewarm")


def test_the_process_reads_yahoo_at_most_so_many_at_once():
    """#874 review: SOURCE 와 기본 풀(시세판 종가)을 합쳐도 야후 읽기는 프로세스 전체에서 YAHOO_AT_ONCE 개까지."""
    lock, now, peak = threading.Lock(), [0], [0]
    release = threading.Event()

    def read():
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        release.wait(5)
        with lock:
            now[0] -= 1

    others = ThreadPoolExecutor(max_workers=10)          # the default executor's part (its size varies by CPU)

    async def go():
        loop = asyncio.get_running_loop()
        reads = [asyncio.ensure_future(pools.run(pools.SOURCE, pools.yahoo, read))
                 for _ in range(pools.SOURCE_WORKERS)]
        reads += [loop.run_in_executor(others, pools.yahoo, read) for _ in range(10)]
        deadline = time.monotonic() + 5
        while pools.YAHOO._value and time.monotonic() < deadline:    # every permit taken
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        held = peak[0]
        release.set()
        await asyncio.gather(*reads)
        return held

    try:
        assert asyncio.run(go()) == pools.YAHOO_AT_ONCE == peak[0]
    finally:
        others.shutdown(wait=False)


def test_a_yahoo_read_inside_another_takes_no_second_permit():
    """desk.prices' daily bars read their hourly fix-up inside the same read: one permit, never two (nested permits
    could all be taken by reads waiting for their second)."""
    def nested():
        with pools.yahoo_slot():
            outer = pools.YAHOO._value
            with pools.yahoo_slot():
                inner = pools.YAHOO._value
            return outer, inner

    outer, inner = asyncio.run(pools.run(pools.SOURCE, nested))
    assert outer == inner == pools.YAHOO_AT_ONCE - 1
    assert pools.YAHOO._value == pools.YAHOO_AT_ONCE
