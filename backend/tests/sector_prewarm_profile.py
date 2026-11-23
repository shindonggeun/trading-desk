"""Offline sector prewarm profile. From backend: python -m tests.sector_prewarm_profile.

Each repeat starts a fresh Cache and measures two concurrent market reads before
the Korean close, on the next pass, and after the Korean scoring boundary.
The production cache serializes the view builds and parallelizes origin requests.
Only the candle origin and the cache clock are synthetic. Fixture construction
and process startup are outside the pass timers.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from desk import day_sectors as ds
from desk import market_calendar
from desk.broker.base import Bar

DAY = date(2026, 11, 23)
BEFORE_CLOSE = datetime(2026, 11, 23, 15, 54, 11, tzinfo=ds.KST)
AHEAD_SEC = 300
SEED = 864
STAGES = ("bars_pipeline", "view", "build_excess", "rows", "fit", "holdout")


def _fixture(count: int) -> tuple[dict[str, list[Bar]], dict[str, list[Bar]]]:
    """Synthetic adjusted closes, on the same exchange-date grids as the live inputs."""
    rng = random.Random(SEED)
    before, after = {}, {}
    for m in ds.MARKETS.values():
        days = []
        d = DAY - timedelta(days=1)
        while len(days) < count:
            if market_calendar.is_open(m.id, d):
                days.append(d)
            d -= timedelta(days=1)
        days.reverse()
        if m == ds.KR:
            days.append(DAY)
        stamps = [datetime.combine(d, datetime.min.time(), m.tz).astimezone(UTC).isoformat() for d in days]
        market_returns = [rng.gauss(0, 0.008) for _ in days]
        for ticker in [m.benchmark, *(s.ticker for s in m.sectors)]:
            level = 100.0
            bars = []
            for at, ret in zip(stamps, market_returns, strict=True):
                excess = 0.0 if ticker == m.benchmark else rng.gauss(0, 0.006)
                level *= math.exp(ret + excess)
                bars.append(Bar(at, level, level, level, level, 1.0))
            before[ticker] = bars[:count]
            after[ticker] = bars[-count:]
    return before, after


class Origin:
    def __init__(self, bars: dict[str, list[Bar]], delay: float,
                 mode: str = "normal", extra_delay: float = 0) -> None:
        self.bars = bars
        self.delay = delay
        self.mode = mode
        self.extra_delay = extra_delay
        self.injected_delay = 0.0
        self.injected_calls = 0
        self.requests: list[tuple[str, int]] = []
        self.peak = 0
        self.active_wall = 0.0
        self.longest_call = 0.0
        self._inflight = 0
        self._active_since = 0.0
        self._lock = threading.Lock()

    def __call__(self, ticker: str, interval: str, count: int) -> list[Bar]:
        if interval != "1d":
            raise AssertionError(f"unexpected candle interval: {interval}")
        injected = self.mode != "normal" and ticker == ds.KR.benchmark
        delay = self.delay + (self.extra_delay if injected else 0)
        started = time.perf_counter()
        with self._lock:
            self.requests.append((ticker, count))
            self.injected_delay += delay
            self.injected_calls += int(injected)
            if self._inflight == 0:
                self._active_since = time.perf_counter()
            self._inflight += 1
            self.peak = max(self.peak, self._inflight)
        try:
            if delay:
                time.sleep(delay)
            if injected and self.mode == "timeout":
                raise TimeoutError
            if injected and self.mode == "empty":
                return []
            return self.bars[ticker][-count:]
        finally:
            with self._lock:
                self.longest_call = max(self.longest_call, time.perf_counter() - started)
                self._inflight -= 1
                if self._inflight == 0:
                    self.active_wall += time.perf_counter() - self._active_since


class Timings:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.inclusive: Counter[str] = Counter()
        self.exclusive: Counter[str] = Counter()
        self._local = threading.local()

    def wrap(self, stage: str, function: Callable) -> Callable:
        def measured(*args, **kwargs):
            stack = getattr(self._local, "children", None)
            if stack is None:
                stack = self._local.children = []
            stack.append(0.0)
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                children = stack.pop()
                if stack:
                    stack[-1] += elapsed
                self.calls[stage] += 1
                self.inclusive[stage] += elapsed
                self.exclusive[stage] += max(0.0, elapsed - children)

        return measured


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 3)


def _pass(cache: ds.Cache, workers: ThreadPoolExecutor, now: datetime, origin: Origin) -> dict:
    timings = Timings()
    bar_lookups = bar_misses = bar_reused_failures = 0
    read_bars = getattr(cache, "_read_bars", None)

    def fetch_bars(tickers, candles, at):
        nonlocal bar_lookups, bar_misses, bar_reused_failures
        first_call = len(origin.requests)
        result = read_bars(tickers, candles, at)
        bar_lookups += len(tickers)
        bar_misses += len({t for t, _ in origin.requests[first_call:]})
        if len(result) == 3:  # the third element contains read outcomes on #864 follow-up
            bar_reused_failures += sum(read.status == "reused_failure" for read in result[2])
        return result

    # The refactor may split holdout scoring from model training. Compare the
    # exclusive timing, which subtracts any nested, instrumented rows and fit.
    holdout = "_score_record" if hasattr(ds, "_score_record") else "record"
    functions = {"view": "view", "build": "build_excess", "rows": "rows", "fit": "fit", holdout: "holdout"}
    wrapped = {name: timings.wrap(stage, getattr(ds, name)) for name, stage in functions.items()}
    with ExitStack() as patches:
        patches.enter_context(patch.multiple(ds, **wrapped))
        if read_bars is not None:
            patches.enter_context(patch.object(cache, "_read_bars", timings.wrap("bars_pipeline", fetch_bars)))
        start = time.perf_counter()
        pending = {m: workers.submit(cache.read, m, origin, now, AHEAD_SEC) for m in ds.MARKETS}
        views = {m: task.result() for m, task in pending.items()}
        wall = time.perf_counter() - start
    if read_bars is None:  # Before #867 every bar lookup went to the origin.
        bar_lookups = bar_misses = len(origin.requests)
    requests = Counter(count for _, count in origin.requests)
    return {
        "wall_ms": _ms(wall),
        "views": {m: {"date": v["date"], "target": v["target"], "ready": v["ready"],
                      "scored": v["result"] is not None} for m, v in views.items()},
        "view_cache_hits": len(views) - timings.calls["view"],
        "view_cache_misses": timings.calls["view"],
        "bar_cache_hits": bar_lookups - bar_misses - bar_reused_failures,
        "bar_cache_misses": bar_misses,
        "bar_reused_failures": bar_reused_failures,
        "origin": {"unique_tickers": len({t for t, _ in origin.requests}), "calls": len(origin.requests),
                   "full_calls": requests[ds.HISTORY_BARS], "recent_calls": requests[getattr(ds, "RECENT_BARS", 0)],
                   "peak_inflight": origin.peak, "active_wall_ms": _ms(origin.active_wall),
                   "longest_call_ms": _ms(origin.longest_call),
                   "injected_delay_sum_ms": _ms(origin.injected_delay),
                   "injected_calls": origin.injected_calls},
        "stages": {stage: {"calls": timings.calls[stage], "inclusive_ms": _ms(timings.inclusive[stage]),
                           "exclusive_ms": _ms(timings.exclusive[stage])} for stage in STAGES},
    }


def profile(runs: int, bars: int, origin_ms: float, rebuild_origin: str = "normal", extra_origin_ms: float = 0) -> dict:
    before, after = _fixture(bars)
    boundary = (ds.session_close(ds.KR, DAY) + ds.SCORE_AFTER).astimezone(ds.KST) + timedelta(seconds=41)
    phases = {"cold": (BEFORE_CLOSE, before), "stable": (BEFORE_CLOSE + timedelta(minutes=4), before),
              "rebuild": (boundary, after)}
    samples: dict[str, list[dict]] = {name: [] for name in phases}
    with ThreadPoolExecutor(max_workers=len(ds.MARKETS)) as workers:
        for _ in range(runs):
            clock = [0.0]
            cache = ds.Cache(clock=lambda clock=clock: clock[0])
            for name, (now, fixture) in phases.items():
                clock[0] = (now - BEFORE_CLOSE).total_seconds()
                mode = rebuild_origin if name == "rebuild" else "normal"
                origin = Origin(fixture, origin_ms / 1000, mode, extra_origin_ms / 1000)
                samples[name].append(_pass(cache, workers, now, origin))
    return {
        "fixture": {"runs": runs, "tickers": len(before), "max_bars_per_ticker": bars,
                    "origin_delay_ms": origin_ms, "fetch_parallel_limit": getattr(ds, "FETCH_PARALLEL", 1),
                    "rebuild_origin": rebuild_origin, "rebuild_extra_origin_ms": extra_origin_ms,
                    "ahead_seconds": AHEAD_SEC, "seed": SEED,
                    "pass_times": {name: now.isoformat() for name, (now, _) in phases.items()}},
        "passes": {name: {"median_wall_ms": statistics.median(s["wall_ms"] for s in values),
                          "samples": values} for name, values in samples.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--bars", type=int, default=ds.HISTORY_BARS, help="synthetic sessions per ticker")
    parser.add_argument("--origin-ms", type=float, default=200, help="synthetic delay per source call")
    parser.add_argument("--rebuild-origin", choices=("normal", "slow", "timeout", "empty"), default="normal",
                        help="scenario applied to the KR benchmark request only in the rebuild")
    parser.add_argument("--extra-origin-ms", type=float, default=0, help="extra delay on the injected request")
    args = parser.parse_args()
    if args.runs < 1 or not 1 <= args.bars <= ds.HISTORY_BARS:
        parser.error(f"--runs must be positive and --bars must be in 1..{ds.HISTORY_BARS}")
    if not math.isfinite(args.origin_ms) or args.origin_ms < 0:
        parser.error("--origin-ms must be finite and nonnegative")
    if not math.isfinite(args.extra_origin_ms) or args.extra_origin_ms < 0:
        parser.error("--extra-origin-ms must be finite and nonnegative")
    if args.extra_origin_ms and args.rebuild_origin == "normal":
        parser.error("--extra-origin-ms requires a non-normal --rebuild-origin")
    print(json.dumps(profile(args.runs, args.bars, args.origin_ms, args.rebuild_origin, args.extra_origin_ms), indent=2))


if __name__ == "__main__":
    main()
