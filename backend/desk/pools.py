"""#874 the app's own thread pools, one per kind of blocking work, so one kind waiting cannot hold up another.

asyncio's default executor (``asyncio.to_thread``: 20 threads at 16 CPUs) is what every page's small blocking
work runs on - DB reads, parsing, the levels' maths. In the dashboard's first-look burst the price sources'
blocking reads (Yahoo ~0.2-1 s each, dozens at once) took its threads and every other page queued behind them
(#858 gated the batch to keep them a few). Each kind now has its own fixed pool:

- ``SOURCE``: desk.prices' blocking reads (Yahoo's bars and quotes). Waiting on the network, not the CPU: what
  bounds it is how many reads Yahoo takes at once. The quote board's daily closes (desk.estimate) stay on the
  default executor: here they queued behind the burst's bars (the board's cold p95 0.92 -> 1.07 s, measured as
  below), there they are a few at a time (desk.api BOARD_PARALLEL) and the bars no longer crowd them.
- ``COMPUTE``: CPU work big enough to stall the event loop (a long chart's indicators). Under the GIL more
  threads would not run it faster; two keep one long job from holding up the next.
- ``PREWARM``: desk.prewarm's passes (#864) - a slow pass never takes the pages' threads.
- ``ADMISSION``: each request's policy and session checks (#858) - never behind a source read.

``run`` is ``asyncio.to_thread`` on one of them (the caller's context goes along, as to_thread's does).

``YAHOO`` bounds the Yahoo reads under way, whichever pool (or the default executor) runs them: the screens'
burst paths and the prewarm's (desk.prices, desk.estimate's daily closes, day_sectors, halving, the portfolio's
FX, kis_stream's exchange lookup). A few reads off those paths are not under it (positioning, the paper desk's
FX, earnings, related, the runner's levels). A read holds one permit (``yahoo_slot`` / ``yahoo``) for its own
work - the HTTP, its parse and a #861 retry's short wait - never while it waits on another lock. Nothing here may
import the app: every module that blocks imports this one.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

# #874 the burst's bar reads: the batch bars (desk.api BATCH_PARALLEL_CANDLES, 12), the levels' two a ticker
# (BATCH_PARALLEL 4) and REFRESH_PARALLEL background reads. 14 = YAHOO_AT_ONCE less the quote board's daily closes
# (desk.api BOARD_PARALLEL, 6, on the default executor): with SOURCE full the board still finds its permits. With
# 20 the bars and the levels took all 20 and the board's cold p95 went 0.92 -> 1.07 s. Measured with
# tests/cold_burst_profile.py (170 ms origins, 20 samples a state, p95 cold / 150 s on - batch paths' slowest; the
# board): the dashboard's first look 0.93 / 0.74 s; 0.93 / 0.74 s (before #874: 1.11 / 1.08; 0.92 / 0.73), the
# 24-ticker stress bundle 1.13 / 1.14 s; 0.97 / 0.78 s (before: 1.66 / 1.66; 0.94 / 0.75). Yahoo reads at once,
# most: 20 and 18 (before: 20 and 14).
SOURCE_WORKERS = 14
COMPUTE_WORKERS = 2
PREWARM_WORKERS = 4      # #864
ADMISSION_WORKERS = 4    # #858

SOURCE = ThreadPoolExecutor(max_workers=SOURCE_WORKERS, thread_name_prefix="desk-source")
COMPUTE = ThreadPoolExecutor(max_workers=COMPUTE_WORKERS, thread_name_prefix="desk-compute")
PREWARM = ThreadPoolExecutor(max_workers=PREWARM_WORKERS, thread_name_prefix="desk-prewarm")
ADMISSION = ThreadPoolExecutor(max_workers=ADMISSION_WORKERS, thread_name_prefix="desk-admission")


async def run(pool: ThreadPoolExecutor, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """``asyncio.to_thread`` on ``pool``: ``fn(*args, **kwargs)`` in one of its threads, in a copy of the
    caller's context."""
    ctx = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(pool, functools.partial(ctx.run, fn, *args, **kwargs))


# #874 review: Yahoo reads under way at once in the process. Before #874 the default executor (20 threads at the
# server's 16 CPUs) ran the screens' Yahoo reads and so bounded them at 20 (the profile above saw 20 on the first
# look); with SOURCE beside it (the quote board's daily closes, the estimated bars' and the prewarm's reads still
# on other threads) up to ~34 could be. The same 20 again - more at once is what Yahoo answers with empty bars
# (#391 / #861)
YAHOO_AT_ONCE = 20
YAHOO = threading.BoundedSemaphore(YAHOO_AT_ONCE)
_holding = threading.local()


@contextmanager
def yahoo_slot() -> Iterator[None]:
    """One of YAHOO's permits for this thread's Yahoo read; a read inside another (desk.prices' daily bars and
    their hourly fix-up) takes no second one - nested permits could take them all and wait on each other."""
    if getattr(_holding, "depth", 0):
        _holding.depth += 1
        try:
            yield
        finally:
            _holding.depth -= 1
        return
    with YAHOO:
        _holding.depth = 1
        try:
            yield
        finally:
            _holding.depth = 0


def yahoo(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """``fn(*args, **kwargs)`` holding a Yahoo permit (for a thread: ``run(SOURCE, yahoo, read, ...)``)."""
    with yahoo_slot():
        return fn(*args, **kwargs)
