"""Offline profile of the dashboard's first-look burst (#874). From backend: python -m tests.cold_burst_profile

A fresh app on a temporary SQLite database, logged in over httpx.ASGITransport (no socket), with synthetic
origins only: a broker (Toss' place: async, ``--origin-ms`` a call), Yahoo (bars, quotes and the board's daily
closes: a blocking ``time.sleep`` of ``--origin-ms`` on whatever thread reads it) and Binance (async). Every
other network call is refused and fails the run.

Each sample sends a bundle at once, ``--http`` requests at a time (a browser's connections). ``first-look`` (the
default) is the dashboard's: the market board's batch bars (``1d`` 63 and ``5m`` 78 for its eleven default
symbols, MarketBoard.tsx BARS) and its tiles' levels (batch levels, the swing horizon's ``1d`` 200 and ``1w``
156), the favorites' sparks (batch ``5m`` 78) and the quote board (/api/estimates) for 24 US tickers in the US
regular session - five requests. ``stress``: those 24 tickers' daily bars on both sides of the broker's 200
(1d 120 and 240), their 5m 78 and the quote board. Three states, ``--samples`` each:

- cold: every cache empty (a restart, or the first look of the day);
- expired: the bundle once, then the caches' clock ``--late-sec`` on (150 s: past the bars' TTL and the
  batch's stale window, desk.api BATCH_STALE_SEC) - a page opened again after a while;
- warm: the bundle once, then again at once.

Prints each path's p50 / p95, whether the batch paths and the quote board meet #858's 1 s and #874's 1.5 s, how
long a page's small ``asyncio.to_thread`` call waited for a thread meanwhile (the default executor sized as the
server's, ``--default-workers``), the most Yahoo reads under way at once, and the origin calls a bundle made (and
on which threads Yahoo was read).
``--source-workers`` / ``--bars-parallel`` / ``--board-parallel`` try other limits than the app's.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import secrets
import statistics
import threading
import time
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, ExitStack
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from desk import api, db, estimate, sessions, shared_cache, sr
from desk.auth import Auth
from desk.broker.base import Bar
from desk.broker.mock import MockBroker
from desk.estimate import BinanceEstimator
from desk.prices import PriceSource
from tests.screen_cache_benchmark import _block_network, _isolated_app_state, _settings

USER = "cold-burst@example.test"
NOW = datetime(2026, 11, 24, 16, 0, tzinfo=UTC)            # a Tuesday, 11:00 New York: the regular session
BOARD = ("NQ=F", "ES=F", "^KS11", "^KQ11", "^SOX", "GC=F", "KRW=X", "BTC-USD", "ETH-USD", "XRP-USD", "SOL-USD")
FAVORITES = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX", "COST", "ADBE",
             "INTC", "QCOM", "MU", "ORCL", "CRM", "PLTR", "COIN", "MSTR", "UBER", "SHOP", "ARM", "SMCI")
BUNDLES = {   # name -> path -> (route, params)
    # the dashboard's first look (MarketBoard.tsx, FavoritesPanel.tsx)
    "first-look": {
        "board_1d_63": ("/api/batch/candles", {"tickers": ",".join(BOARD), "interval": "1d", "count": 63}),
        "board_5m_78": ("/api/batch/candles", {"tickers": ",".join(BOARD), "interval": "5m", "count": 78}),
        "board_levels": ("/api/batch/levels", {"tickers": ",".join(BOARD)}),   # swing: 1d 200 + 1w 156
        "favs_5m_78": ("/api/batch/candles", {"tickers": ",".join(FAVORITES), "interval": "5m", "count": 78}),
        "estimates": ("/api/estimates", {"tickers": ",".join(FAVORITES)}),
    },
    # #874's synthetic baseline elsewhere: the 24 tickers' daily bars on both sides of the broker's 200 (Toss
    # 120, Yahoo 240) and their 5-minute bars, with the quote board
    "stress": {
        "favs_1d_120": ("/api/batch/candles", {"tickers": ",".join(FAVORITES), "interval": "1d", "count": 120}),
        "favs_1d_240": ("/api/batch/candles", {"tickers": ",".join(FAVORITES), "interval": "1d", "count": 240}),
        "favs_5m_78": ("/api/batch/candles", {"tickers": ",".join(FAVORITES), "interval": "5m", "count": 78}),
        "estimates": ("/api/estimates", {"tickers": ",".join(FAVORITES)}),
    },
}
STATES = ("cold", "expired", "warm")
TARGETS = (("#858", 1000), ("#874", 1500))     # p95 of the batch paths and of the quote board, ms
# the batch candles' gate: its own since #874, the bars kinds' before
BARS_GATE = "BATCH_PARALLEL_CANDLES" if hasattr(api, "BATCH_PARALLEL_CANDLES") else "BATCH_PARALLEL_BARS"
_STEP = {"1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "1d": timedelta(days=1), "1w": timedelta(weeks=1)}


class _Clock:
    """The caches' monotonic clock, moved on by hand (the expired state)."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.offset


class _Origin:
    def __init__(self, delay_ms: float) -> None:
        self.delay = delay_ms / 1000
        self.calls: Counter[str] = Counter()
        self.threads: Counter[str] = Counter()
        self.lock = threading.Lock()
        self._bars: dict[tuple[str, str], list[Bar]] = {}
        self.yahoo_now = 0                  # Yahoo reads under way
        self.yahoo_peak = 0                 # the most at once since the last reset

    def _yahoo(self, kind: str) -> None:
        """One blocking Yahoo read: counted, timed, and how many were under way at once."""
        self.hit(kind, thread=True)
        with self.lock:
            self.yahoo_now += 1
            self.yahoo_peak = max(self.yahoo_peak, self.yahoo_now)
        try:
            time.sleep(self.delay)
        finally:
            with self.lock:
                self.yahoo_now -= 1

    def hit(self, kind: str, thread: bool = False) -> None:
        with self.lock:
            self.calls[kind] += 1
            if thread:
                self.threads[threading.current_thread().name.rsplit("_", 1)[0]] += 1

    def bars(self, ticker: str, interval: str, count: int) -> list[Bar]:
        key = (ticker, interval)
        with self.lock:
            if key not in self._bars:
                base = 50.0 + zlib.crc32(ticker.encode()) % 500
                step = _STEP.get(interval, timedelta(days=1))
                end = NOW.replace(hour=0, minute=0) if interval == "1d" else NOW
                rows = []
                for i in range(600):
                    close = base * (1 + 0.03 * math.sin(i / 7) + 0.0002 * i)
                    at = (end - step * (599 - i)).isoformat(timespec="seconds")
                    rows.append(Bar(at, close * 0.998, close * 1.004, close * 0.995, close, 1000.0 + i))
                self._bars[key] = rows
            return self._bars[key][-count:]

    # Yahoo: blocking, on the caller's thread
    def yahoo_candles(self, ticker: str, interval: str, count: int) -> list[Bar]:
        self._yahoo("yahoo_candles")
        return self.bars(ticker, interval, count)

    def yahoo_quote(self, ticker: str) -> tuple[float, str | None]:
        self._yahoo("yahoo_quote")
        return self.bars(ticker, "1d", 1)[-1].close, "USD"

    def yahoo_closes(self, ticker: str, session) -> list[tuple[date, float]]:
        self._yahoo("yahoo_closes")
        days = []
        day = NOW.date()
        while len(days) < 10:
            if day.weekday() < 5:
                days.append(day)
            day -= timedelta(days=1)
        return [(d, 100.0 + i) for i, d in enumerate(reversed(days))]

    # Binance: async
    async def binance(self, request: httpx.Request) -> httpx.Response:
        self.hit("binance")
        await asyncio.sleep(self.delay)
        if request.url.path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json={"symbols": [
                {"baseAsset": t, "symbol": f"{t}USDT", "contractType": "TRADIFI_PERPETUAL",
                 "underlyingType": "EQUITY", "quoteAsset": "USDT", "status": "TRADING"} for t in FAVORITES]})
        return httpx.Response(404, json={})


class _Broker(MockBroker):
    """Toss' place: async calls, 1m / 1d bars up to 200 and quotes."""

    name = "toss"

    def __init__(self, origin: _Origin) -> None:
        self.origin = origin

    async def quote(self, symbol: str) -> tuple[float, str | None]:
        self.origin.hit("broker_quote")
        await asyncio.sleep(self.origin.delay)
        return self.origin.bars(symbol, "1d", 1)[-1].close, "USD"

    async def candles(self, symbol: str, interval: str, count: int) -> list[Bar]:
        self.origin.hit("broker_candles")
        await asyncio.sleep(self.origin.delay)
        return self.origin.bars(symbol, interval, count)


async def _regular(_calendar, ticker: str, now: datetime | None = None) -> str:
    return "regular"


def _check(paths: dict, name: str, response: httpx.Response) -> None:
    response.raise_for_status()
    body = response.json()
    if name == "estimates":
        bad = [i["ticker"] for i in body["items"] if i.get("state") != "live" or i.get("close") is None]
    elif name == "board_levels":
        bad = [t for t, i in body["items"].items() if "error" in i]
    else:
        count = paths[name][1]["count"]
        bad = [t for t, i in body["items"].items() if "error" in i or len(i.get("bars") or []) != count]
    if bad:
        raise RuntimeError(f"{name}: unexpected answers for {', '.join(bad[:5])}")


async def _bundle(client: httpx.AsyncClient, paths: dict, http: int) -> dict[str, float]:
    gate = asyncio.Semaphore(http)

    async def one(name: str) -> tuple[str, float]:
        path, params = paths[name]
        async with gate:
            start = time.perf_counter()
            response = await client.get(path, params=params)
            took = (time.perf_counter() - start) * 1000
        _check(paths, name, response)
        return name, took

    return dict(await asyncio.gather(*(one(n) for n in paths)))


async def _settle(source: PriceSource) -> None:
    """Background reads (a stale or nearly stale entry's) finish before the next sample."""
    for _ in range(100):
        pending = [*source._inflight.values(), *source._queued]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
    raise RuntimeError("background candle reads never settled")


async def _probe(stop: asyncio.Event, waits: list[float]) -> None:
    """A page's small to_thread call (a DB read) every 20 ms while the bundle runs: how long it waits for a
    thread of the default executor."""
    while not stop.is_set():
        start = time.perf_counter()
        await asyncio.to_thread(int)
        waits.append((time.perf_counter() - start) * 1000)
        await asyncio.sleep(0.02)


def _p(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) * q) - 1, 0)]


async def profile(samples: int = 20, origin_ms: float = 170.0, http: int = 5, late_sec: float = 150.0,
                  bundle: str = "first-look", default_workers: int = 20, source_workers: int | None = None,
                  bars_parallel: int | None = None, board_parallel: int | None = None) -> dict:
    paths = BUNDLES[bundle]
    # the server's default executor (min(32, CPUs + 4): 20 at 16 CPUs), not this machine's
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=default_workers, thread_name_prefix="asyncio"))
    origin = _Origin(origin_ms)
    clock = _Clock()
    broker = _Broker(origin)

    def fresh_source() -> PriceSource:
        return PriceSource(broker, yahoo_quote=origin.yahoo_quote, yahoo_candles=origin.yahoo_candles, clock=clock)

    binance = httpx.AsyncClient(transport=httpx.MockTransport(origin.binance), base_url=estimate.BINANCE_URL)

    def fresh_estimator() -> BinanceEstimator:
        return BinanceEstimator(http=binance, now=lambda: NOW, clock=clock, after_hours=None)

    source, estimator = fresh_source(), fresh_estimator()
    results: dict[str, dict] = {}
    with ExitStack() as patches:
        patches.enter_context(_block_network())
        patches.enter_context(_isolated_app_state())
        patches.enter_context(patch.object(sessions, "session", _regular))
        patches.enter_context(patch.object(estimate, "_yahoo_rows", origin.yahoo_closes))
        patches.enter_context(patch.object(estimate, "_clock", clock))
        pools = getattr(api, "pools", None)          # #874 (absent before it): the limits under measurement
        if source_workers is not None and pools is not None:
            patches.enter_context(patch.object(pools, "SOURCE", ThreadPoolExecutor(
                max_workers=source_workers, thread_name_prefix="desk-source")))
        if bars_parallel is not None:
            patches.enter_context(patch.object(api, BARS_GATE, bars_parallel))
        if board_parallel is not None:
            patches.enter_context(patch.object(api, "BOARD_PARALLEL", board_parallel))
        limits = {"default_workers": default_workers, "bars_parallel": getattr(api, BARS_GATE),
                  "source_workers": pools.SOURCE._max_workers if pools is not None else None,
                  "board_parallel": api.BOARD_PARALLEL}
        temp = patches.enter_context(TemporaryDirectory(prefix="desk-cold-burst-"))
        settings = _settings(Path(temp) / "desk.db")
        app = api.create_app(settings, prices=source, estimator=estimator)
        password = secrets.token_urlsafe(24)
        stack = AsyncExitStack()
        try:
            await stack.enter_async_context(app.router.lifespan_context(app))
            shared_cache.use(None, None)
            engine = db.make_engine(settings.database_url)
            try:
                Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(USER, password)
            finally:
                engine.dispose()
            client = await stack.enter_async_context(httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://bench.test", trust_env=False, timeout=60))
            (await client.post("/api/auth/login", json={"username": USER, "password": password})).raise_for_status()

            def reset() -> None:
                # every cache the bundle reads, emptied in place (the app holds these objects)
                source.__init__(broker, yahoo_quote=origin.yahoo_quote, yahoo_candles=origin.yahoo_candles,
                                clock=clock)
                estimator.__init__(http=binance, now=lambda: NOW, clock=clock, after_hours=None)
                estimate._CLOSES.clear()
                estimate._CLOSES_FAILED.clear()
                sr._CACHE.clear()

            reset()
            await _bundle(client, paths, http)              # the runtime's own first use, outside every state
            await _settle(source)
            for state in STATES:
                took: dict[str, list[float]] = {n: [] for n in paths}
                calls: Counter[str] = Counter()
                threads: Counter[str] = Counter()
                walls: list[float] = []
                waits: list[float] = []
                peak = 0
                for _ in range(samples):
                    reset()
                    if state != "cold":
                        await _bundle(client, paths, http)
                        await _settle(source)
                    if state == "expired":
                        clock.offset += late_sec
                    before, before_threads = origin.calls.copy(), origin.threads.copy()
                    origin.yahoo_peak = 0
                    stop = asyncio.Event()
                    probe = asyncio.ensure_future(_probe(stop, waits))
                    start = time.perf_counter()
                    got = await _bundle(client, paths, http)
                    walls.append((time.perf_counter() - start) * 1000)
                    stop.set()
                    await probe
                    await _settle(source)
                    calls += origin.calls - before
                    peak = max(peak, origin.yahoo_peak)
                    threads += origin.threads - before_threads
                    for name, ms in got.items():
                        took[name].append(ms)
                results[state] = {
                    "paths": {n: {"p50_ms": round(statistics.median(v)), "p95_ms": round(_p(v, 0.95))}
                              for n, v in took.items()},
                    "bundle_p95_ms": round(_p(walls, 0.95)),
                    "origin_calls_per_bundle": {k: round(v / samples, 1) for k, v in sorted(calls.items())},
                    "origin_calls_total_per_bundle": round(calls.total() / samples, 1),
                    "yahoo_threads": dict(threads),
                    "yahoo_at_once_max": peak,
                    "default_pool_wait_ms": {"p95": round(_p(waits, 0.95), 1), "max": round(max(waits), 1)},
                }
        finally:
            for task in [*source._inflight.values(), *source._queued]:
                task.cancel()
            await stack.aclose()
            await binance.aclose()
    return {"bundle": bundle, "origin_ms": origin_ms, "http": http, "samples": samples, "late_sec": late_sec,
            **limits, "states": results}


def _table(report: dict) -> str:
    title = (f"{report['bundle']}: origin {report['origin_ms']:.0f} ms, {report['http']} HTTP at a time, "
             f"{report['samples']} samples a state, expired = +{report['late_sec']:.0f} s, default executor "
             f"{report.get('default_workers')}, source pool {report.get('source_workers')}, "
             f"bars gate {report.get('bars_parallel')}, board gate {report.get('board_parallel', 6)}")
    lines = [title, f"{'path':<14}" + "".join(f"{s + ' p50/p95 ms':>24}" for s in STATES)]
    for name in BUNDLES[report["bundle"]]:
        cells = []
        for state in STATES:
            got = report["states"][state]["paths"][name]
            cells.append(f"{got['p50_ms']:>11} / {got['p95_ms']:<6}")
        lines.append(f"{name:<14}" + "".join(f"{c:>24}" for c in cells))
    batch = [n for n, (route, _) in BUNDLES[report["bundle"]].items() if route.startswith("/api/batch/")]
    worst = {s: max(report["states"][s]["paths"][n]["p95_ms"] for n in batch) for s in STATES}
    board = {s: report["states"][s]["paths"]["estimates"]["p95_ms"] for s in STATES}
    lines.append(f"{'batch p95 max':<14}" + "".join(f"{worst[s]:>24}" for s in STATES))
    for label, limit in TARGETS:
        cells = [f"batch {'yes' if worst[s] <= limit else 'NO'} / board {'yes' if board[s] <= limit else 'NO'}"
                 for s in STATES]
        lines.append(f"{label + f' <={limit / 1000:g}s':<14}" + "".join(f"{c:>24}" for c in cells))
    lines.append(f"{'bundle p95':<14}" + "".join(f"{report['states'][s]['bundle_p95_ms']:>24}" for s in STATES))
    lines.append(f"{'to_thread p95':<14}" + "".join(
        f"{report['states'][s]['default_pool_wait_ms']['p95']:>24}" for s in STATES))
    lines.append(f"{'to_thread max':<14}" + "".join(
        f"{report['states'][s]['default_pool_wait_ms']['max']:>24}" for s in STATES))
    lines.append(f"{'yahoo at once':<14}" + "".join(
        f"{report['states'][s].get('yahoo_at_once_max', '?'):>24}" for s in STATES))
    lines.append(f"{'origin calls':<14}" + "".join(
        f"{report['states'][s]['origin_calls_total_per_bundle']:>24}" for s in STATES))
    for state in STATES:
        got = report["states"][state]
        lines.append(f"  {state}: {got['origin_calls_per_bundle']}  yahoo threads {got['yahoo_threads']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--origin-ms", type=float, default=170.0)
    parser.add_argument("--http", type=int, default=5, help="requests in flight at once")
    parser.add_argument("--late-sec", type=float, default=150.0, help="the expired state's clock step")
    parser.add_argument("--bundle", choices=sorted(BUNDLES), default="first-look")
    parser.add_argument("--default-workers", type=int, default=20, help="the default executor's threads")
    parser.add_argument("--source-workers", type=int, help="desk.pools.SOURCE's threads (default: the app's)")
    parser.add_argument("--bars-parallel", type=int, help="the batch candles' gate (default: the app's)")
    parser.add_argument("--board-parallel", type=int, help="desk.api.BOARD_PARALLEL (default: the app's)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(profile(args.samples, args.origin_ms, args.http, args.late_sec, args.bundle,
                                 args.default_workers, args.source_workers, args.bars_parallel,
                                 args.board_parallel))
    print(json.dumps(report, indent=2) if args.json else _table(report))


if __name__ == "__main__":
    main()
