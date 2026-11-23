"""#536 step 2 - warm the shared caches before a page asks.

Production's table (#536) showed the slow requests are the first ask after a cache expired: the market's
selling score (risk), the rate / dollar regime (tips), the indices' primary trend, the weekly map, the fear
gauges and the halving cycles took 1-9 s cold and a few ms warm. This loop pays that first ask itself:
every ``INTERVAL_SEC`` it asks each shared cache with ``ahead=AHEAD_SEC`` - a value that would expire before
the next pass is fetched now, while pages keep reading the old one (the caches read a fresh value without
their fetch lock). The jobs run side by side; one failing does not stop the others or the next pass.

#864: the jobs that are one blocking call (tips · trend · weekly · halving · gaps · sectors · risk · bottoms) run
it on this module's own small executor (``off``), not asyncio's default one - the pages' requests use that
(asyncio.to_thread), and a pass of slow reads must not leave them waiting for a worker at the market's open. The
async jobs (board · fear · playbooks) and the positioning tickers still use the default one inside. ``LAST`` is
the last pass, for /api/status.

Then the favorites' US tickers' positioning (Cboe options, FINRA, short interest - 6-14 s cold), a few, one
at a time: its cache holds a ticker's lock while it fetches, so it is only filled when missing, never
refetched ahead.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

log = logging.getLogger(__name__)

INTERVAL_SEC = 240.0
AHEAD_SEC = INTERVAL_SEC + 60.0     # what expires before the next pass (with room for a slow pass)
FAVORITES_MAX = 8                   # favorites' tickers warmed per pass
# #536 review: the board's closes and the playbooks live 300 s (desk.estimate CLOSES_TTL_SEC, the swing
# horizon's sr_ttl_sec) - AHEAD_SEC would call every value stale. A third of that: refreshed once a pass while
# their market is open (a value read in the last pass is 240 s old), never while it is closed.
LIVE_AHEAD_SEC = 100.0
FIRST_DELAY_SEC = 5.0               # let the app finish starting before the first pass

Job = Callable[[], Awaitable[object]]

# #864 the prewarm's own workers (as #858's admission executor: a fixed small pool, asyncio.to_thread's meaning)
WORKERS = 4
_POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="desk-prewarm")
LAST: dict = {}                     # the last pass: {"at", "ms", "jobs": {name: "<ms>ms" | error}}


async def off(fn: Callable, /, *args, **kwargs):
    """``asyncio.to_thread`` on the prewarm's own executor (the context goes along, as to_thread's does)."""
    ctx = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(_POOL, functools.partial(ctx.run, fn, *args, **kwargs))


async def warm(jobs: dict[str, Job]) -> dict[str, str]:
    """Every job side by side: name -> "<ms>ms" or the error's class name."""
    async def one(job: Job) -> str:
        start = time.perf_counter()
        try:
            await job()
        except Exception as exc:  # noqa: BLE001 - a cache that cannot be warmed now is asked next pass
            return type(exc).__name__
        return f"{(time.perf_counter() - start) * 1000:.0f}ms"

    names = list(jobs)
    got = await asyncio.gather(*(one(jobs[n]) for n in names))
    return dict(zip(names, got, strict=True))


async def run(jobs: Callable[[], dict[str, Job]], tickers: Callable[[], list[str]],
              ticker_job: Callable[[str], Awaitable[object]], interval: float = INTERVAL_SEC,
              first_delay: float = FIRST_DELAY_SEC, sleep=asyncio.sleep,
              then: Callable[[], dict[str, Job]] | None = None) -> None:
    """The loop: the shared jobs, then the ``then`` jobs (ones that read the shared caches - the playbooks read
    the market's selling score, which a cold start has not got yet), then each of ``tickers()`` (at most
    FAVORITES_MAX) through ``ticker_job``."""
    await sleep(first_delay)
    while True:
        start = time.monotonic()
        try:
            took = await warm(jobs())
            if then is not None:
                took.update(await warm(then()))
            log.info("prewarm: %s", " ".join(f"{k}={v}" for k, v in took.items()))
            LAST.clear()
            LAST.update(at=datetime.now(UTC).isoformat(timespec="seconds"),
                        ms=round((time.monotonic() - start) * 1000), jobs=took)
            for ticker in (await asyncio.to_thread(tickers))[:FAVORITES_MAX]:   # a DB read: off the loop
                try:
                    await ticker_job(ticker)
                except Exception as exc:  # noqa: BLE001 - that ticker only
                    log.info("prewarm %s failed: %s", ticker, type(exc).__name__)
        except Exception:  # a failed pass must not stop the next one
            log.exception("prewarm pass failed")
        # a fixed rate: a slow pass (cold positioning takes seconds a ticker) does not push the next one past
        # what AHEAD_SEC covers
        await sleep(max(interval - (time.monotonic() - start), 1.0))
