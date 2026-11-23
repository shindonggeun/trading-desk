"""Measure authenticated screen request bundles with synthetic origins and Redis.

Run from backend: python -m tests.screen_cache_benchmark --samples 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import secrets
import socket
import statistics
import time
from collections import Counter
from contextlib import AsyncExitStack, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from inspect import getclosurevars
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from unittest.mock import patch

import httpx

from desk import (
    api,
    day_calls,
    db,
    earnings,
    log_setup,
    market_bus,
    net,
    notify,
    notify_channels,
    oscillator,
    shared_cache,
    sr,
)
from desk.auth import Auth
from desk.broker.base import Bar
from desk.broker.mock import MockBroker
from desk.indicators import normalize as normalize_indicators
from desk.prices import PriceSource
from desk.settings import Settings
from desk.ttl_cache import TTLCache

STATES = ("cold", "peer_warm", "api_warm")
USER = "cache-benchmark@example.test"
FIXTURE_END = datetime(2026, 1, 30, 12, tzinfo=UTC)
INDICES = ("^KS11", "^KQ11", "NQ=F")
BOARD_TICKERS = ("NQ=F", "ES=F", "^KS11", "^KQ11")
PRICE_BASES = {"^KS11": 2900.0, "^KQ11": 880.0, "NQ=F": 21200.0, "ES=F": 6050.0, "QQQ": 510.0}
CANDLE_OSCILLATOR_FIELDS = {"rsi14": ("rsi",), "stoch": ("stoch_k", "stoch_d")}


@dataclass(frozen=True)
class Request:
    kind: str
    tickers: tuple[str, ...]
    interval: str | None = None
    count: int | None = None
    batch: bool = False
    indicators: tuple[str, ...] = ()

    @property
    def path(self) -> str:
        return f"/api/{'batch/' if self.batch else ''}{self.kind}"

    def route(self) -> dict:
        params: dict[str, str | int] = {}
        if self.batch:
            params["tickers"] = ",".join(self.tickers)
        else:
            params["ticker"] = self.tickers[0]
        if self.interval is not None:
            params["interval"] = self.interval
        if self.count is not None:
            params["count"] = self.count
        if self.indicators:
            params["indicators"] = ",".join(self.indicators)
        return {"path": self.path, "params": params}


SCREENS = {
    "dashboard": tuple(Request("candles", (ticker,), "1d", 5) for ticker in INDICES),
    "chart": (Request("candles", ("QQQ",), "1d", 130, indicators=("rsi", "stoch")),
              Request("oscillator", ("QQQ",), "1d")),
    "market_board": (Request("candles", BOARD_TICKERS, "1d", 63, batch=True),
                     Request("levels", BOARD_TICKERS, batch=True)),
}
SHARED_NAMESPACES = {"dashboard": [], "chart": ["oscillator"], "market_board": ["sr"]}


class _RedisStore:
    """One expiring keyspace shared by both clients, with one delay per pipeline or SET."""

    def __init__(self, delay_ms: float, clock=time.monotonic):
        self.delay = delay_ms / 1000
        self.clock = clock
        self.entries: dict[str, tuple[str, float]] = {}
        self.calls: Counter[str] = Counter()
        self.lock = Lock()

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()

    def read(self, key: str, client: str) -> tuple[str | None, int]:
        with self.lock:
            self.calls[f"{client}_pipeline"] += 1
            entry = self.entries.get(key)
            if entry is not None:
                value, expires = entry
                remaining = expires - self.clock()
                if remaining > 0:
                    left_ms = math.ceil(remaining * 1000)
                    return value, left_ms
                self.entries.pop(key)
            return None, -2

    def write(self, key: str, value: str, px: int, client: str) -> None:
        with self.lock:
            self.calls[f"{client}_set"] += 1
            self.entries[key] = (value, self.clock() + px / 1000)


class _RedisRead:
    def __init__(self, store: _RedisStore, *, transaction: bool):
        if transaction:
            raise ValueError("expected a non-transactional Redis pipeline")
        self.store = store
        self.commands: list[tuple[str, str]] = []

    def get(self, key: str) -> None:
        self.commands.append(("get", key))

    def pttl(self, key: str) -> None:
        self.commands.append(("pttl", key))

    def read(self, client: str) -> tuple[str | None, int]:
        match self.commands:
            case [("get", key), ("pttl", ttl_key)] if key == ttl_key:
                return self.store.read(key, client)
            case _:
                raise ValueError("expected GET and PTTL for the same Redis key")


class _SyncRedisRead(_RedisRead):
    def execute(self) -> tuple[str | None, int]:
        if self.store.delay:
            time.sleep(self.store.delay)
        return self.read("sync")


class _AsyncRedisRead(_RedisRead):
    async def execute(self) -> tuple[str | None, int]:
        if self.store.delay:
            await asyncio.sleep(self.store.delay)
        return self.read("async")


class _SyncRedis:
    def __init__(self, store: _RedisStore):
        self.store = store

    def pipeline(self, *, transaction: bool) -> _SyncRedisRead:
        return _SyncRedisRead(self.store, transaction=transaction)

    def set(self, key: str, value: str, *, px: int) -> None:
        if self.store.delay:
            time.sleep(self.store.delay)
        self.store.write(key, value, px, "sync")

    def close(self) -> None:
        pass


class _AsyncRedis:
    def __init__(self, store: _RedisStore):
        self.store = store

    def pipeline(self, *, transaction: bool) -> _AsyncRedisRead:
        return _AsyncRedisRead(self.store, transaction=transaction)

    async def set(self, key: str, value: str, *, px: int) -> None:
        if self.store.delay:
            await asyncio.sleep(self.store.delay)
        self.store.write(key, value, px, "async")

    async def aclose(self) -> None:
        pass


def _history(base: float, interval: str, count: int) -> list[Bar]:
    step = timedelta(weeks=1) if interval == "1w" else timedelta(days=1)
    day = FIXTURE_END - timedelta(days=FIXTURE_END.weekday()) if interval == "1w" else FIXTURE_END
    days = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= step
    bars = []
    prior = base
    for i, day in enumerate(reversed(days)):
        close = base * (1 + i * .00012 + .032 * math.sin(i / 5) + .019 * math.sin(i / 17))
        op = prior * (1 + .006 * math.sin(i))
        high, low = max(op, close) * 1.005, min(op, close) * .995
        bars.append(Bar(day.isoformat(), op, high, low, close, float(950_000 + 11_000 * (i % 23))))
        prior = close
    return bars


class _Origin:
    def __init__(self, delay_ms: float):
        self.delay = delay_ms / 1000
        self.counts: Counter[str] = Counter()
        self.lock = Lock()
        self.bars = {(ticker, interval): _history(base, interval, 1_000)
                     for ticker, base in PRICE_BASES.items() for interval in ("1d", "1w")}

    def yahoo_candles(self, ticker: str, interval: str, count: int) -> list[Bar]:
        with self.lock:
            self.counts["yahoo_candles"] += 1
        if self.delay:
            time.sleep(self.delay)
        return self.bars[ticker, interval][-count:]


@contextmanager
def _block_network():
    """ASGI needs no socket. Detect even denied calls swallowed by production fallbacks."""
    attempts: list[str] = []
    lock = Lock()

    def blocked(name):
        def deny(*_args, **_kwargs):
            with lock:
                attempts.append(name)
            raise RuntimeError(f"benchmark blocked network access ({name})")
        return deny

    with ExitStack() as stack:
        guards = [
            (socket, ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo")),
            (socket.socket, ("connect", "connect_ex", "sendto", "sendmsg")),
        ]
        if find_spec("curl_cffi") is not None:
            # yfinance can use libcurl, which does not call Python's socket methods.
            from curl_cffi import AsyncCurl, Curl

            guards.extend(((Curl, ("perform",)), (AsyncCurl, ("add_handle",))))
        for target, names in guards:
            for name in names:
                if hasattr(target, name):
                    stack.enter_context(patch.object(target, name, blocked(name)))
        try:
            yield
        finally:
            if attempts:
                raise RuntimeError(f"unexpected network access during benchmark: {', '.join(sorted(set(attempts)))}")


@contextmanager
def _isolated_app_state():
    # Lifespan mutates process globals. Give it disposable objects and restore the caller's references.
    with ExitStack() as stack:
        # Startup builds outbound TLS contexts even for clients that never send requests.
        stack.enter_context(patch.dict(os.environ, dict.fromkeys(("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"), "")))
        stack.enter_context(patch.multiple(
            shared_cache, _sync=None, _async=None, _box=None, _name_key=b"", _down_until=0.0,
            _stats=dict.fromkeys(shared_cache._stats, 0), _unshareable=set(), _warned_rejected=False,
        ))
        for cache in (sr._CACHE, oscillator._CACHE):
            stack.enter_context(patch.object(cache, "local", TTLCache(cache.local.max_items, cache.ttl)))
        stack.enter_context(patch.object(market_bus, "BUS", market_bus.Bus()))
        stack.enter_context(patch.object(day_calls, "DESK", None))
        stack.enter_context(patch.object(earnings.CACHE, "name_of", earnings.CACHE.name_of))
        stack.enter_context(patch.object(api, "kr_market", api.kr_market))
        stack.enter_context(patch.object(net, "_by_thread", {}))
        stack.enter_context(patch.multiple(notify, _channels={}, _store=None, _pool=None, _closed=False))
        # The offline workload needs neither application-wide logging configuration nor changes to filters.
        stack.enter_context(patch.object(log_setup, "configure", lambda _level: logging.INFO))
        stack.enter_context(patch.object(notify_channels, "install_log_filter", lambda: None))
        yield


def _settings(path: Path) -> Settings:
    # Settings default factories read the environment; ignore local .env and CI defaults alike.
    with patch.dict(os.environ, clear=True):
        return Settings(database_url=f"sqlite:///{path.as_posix()}", role="api", fake_runs=True,
                        market_stream=False, macro_interval_sec=0, report_every_days=0,
                        score_interval_sec=0, retention_days=0, morning_brief_at="", futures_snapshot_min=0)


def _level_gap_cache(app) -> dict:
    # /api/levels augments shared SR from a separate app-local dictionary, also used by /api/batch/levels.
    endpoint = next((r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/levels"), None)
    cache = getclosurevars(endpoint).nonlocals.get("gap_cache") if endpoint else None
    if not isinstance(cache, dict):
        raise TypeError("cannot reset /api/levels: the app-local gap_cache changed")
    return cache


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _payload_ok(kind: str, request: Request, ticker: str, body: dict) -> bool:
    if not isinstance(body, dict) or body.get("error") is not None or body.get("ticker") != ticker:
        return False
    if kind == "candles":
        bars = body.get("bars")
        indicators = normalize_indicators(request.indicators)
        if (body.get("interval") != request.interval or body.get("source") != "yahoo"
                or body.get("truncated") is not False or body.get("indicators") != list(indicators)
                or not isinstance(bars, list) or not bars or len(bars) != request.count):
            return False
        previous = ""
        for bar in bars:
            if not isinstance(bar, dict) or not all(_number(bar.get(k)) for k in ("open", "high", "low", "close", "volume")):
                return False
            at = bar.get("at")
            if (not isinstance(at, str) or at <= previous
                    or not 0 < bar["low"] <= min(bar["open"], bar["close"])
                    or max(bar["open"], bar["close"]) > bar["high"] or bar["volume"] <= 0):
                return False
            previous = at
        last = bars[-1]
        fields = [field for indicator in indicators for field in CANDLE_OSCILLATOR_FIELDS[indicator]]
        return (previous == FIXTURE_END.isoformat() and math.isclose(last["close"], PRICE_BASES[ticker], rel_tol=.2)
                and all(_number(last.get(field)) and 0 <= last[field] <= 100 for field in fields))
    if kind == "oscillator":
        own = body.get("own")
        return (
            body.get("interval") == request.interval and body.get("underlying") is None
            and isinstance(own, dict) and own.get("error") is None and own.get("ticker") == ticker
            and own.get("bars") == oscillator.HISTORY_BARS[request.interval]
            and own.get("zone") in ("hot", "neutral", "cold") and _number(own.get("rsi"))
            and own.get("at") == FIXTURE_END.isoformat() and bool(own.get("reason"))
        )
    if kind == "levels":
        levels = body.get("levels")
        return (
            body.get("reference") == "close" and _number(body.get("reference_price"))
            and body["reference_price"] > 0 and _number(body.get("atr")) and body["atr"] > 0
            and body.get("as_of") == FIXTURE_END.isoformat()
            and "gaps_error" in body and body["gaps_error"] is None and isinstance(body.get("gaps"), list)
            and not body.get("fallback") and isinstance(levels, list) and bool(levels)
            and all(isinstance(level, dict) and _number(level.get("price")) and level["price"] > 0
                    for level in levels)
        )
    raise ValueError(f"unknown benchmark payload kind: {kind}")


def _validate_response(request: Request, response: httpx.Response) -> None:
    response.raise_for_status()
    body = response.json()
    if request.batch:
        items = body.get("items") if isinstance(body, dict) else None
        if (not isinstance(items, dict) or body.get("kind") != request.kind
                or set(items) != set(request.tickers) or body.get("error") is not None):
            raise RuntimeError(f"invalid benchmark batch envelope from {request.path}")
    else:
        items = {request.tickers[0]: body}
    for ticker in request.tickers:
        if not _payload_ok(request.kind, request, ticker, items[ticker]):
            raise RuntimeError(f"invalid benchmark payload from {request.path} for {ticker}")


async def _request_bundle(client: httpx.AsyncClient, screen: str) -> float:
    requests = SCREENS[screen]
    routes = [request.route() for request in requests]
    start = time.perf_counter_ns()
    responses = await asyncio.gather(
        *(client.get(route["path"], params=route["params"]) for route in routes), return_exceptions=True,
    )
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    for request, response in zip(requests, responses, strict=True):
        if isinstance(response, BaseException):
            raise response
        _validate_response(request, response)
    return elapsed_ms


def _snapshot(origin: _Origin, redis: _RedisStore) -> tuple[Counter, Counter, Counter]:
    stats = shared_cache.stats()
    return origin.counts.copy(), redis.calls.copy(), Counter({k: stats[k] for k in shared_cache._stats})


def _deltas(before: tuple[Counter, Counter, Counter], origin: _Origin, redis: _RedisStore):
    return tuple(new - old for old, new in zip(before, _snapshot(origin, redis), strict=True))


async def _case(client, source, origin, gaps, screen, enabled, state, samples, redis_delay_ms) -> dict:
    redis = _RedisStore(redis_delay_ms)
    shared_cache.use(_SyncRedis(redis) if enabled else None, _AsyncRedis(redis) if enabled else None)

    def require_idle():
        if source._inflight or source._queued:
            raise RuntimeError(f"{screen} {state} benchmark found outstanding candle reads")

    def reset_local():
        require_idle()
        source._candles.clear()
        source._quotes.clear()
        gaps.clear()
        sr._CACHE.clear()
        oscillator._CACHE.clear()

    origins, redis_calls, stats = Counter(), Counter(), Counter()
    primer_origins, primer_samples = Counter(), 0
    latencies = []
    for sample in range(samples):
        prime = state == "peer_warm" or (state == "api_warm" and sample == 0)
        if state != "api_warm" or sample == 0:
            reset_local()
            redis.clear()
        if prime:
            before = _snapshot(origin, redis)
            await _request_bundle(client, screen)
            require_idle()
            primer_origins += _deltas(before, origin, redis)[0]
            primer_samples += 1
            if state == "peer_warm":
                reset_local()  # the API has never seen the peer's process-local results
        before = _snapshot(origin, redis)
        latencies.append(await _request_bundle(client, screen))
        require_idle()
        measured_origins, measured_calls, measured_stats = _deltas(before, origin, redis)
        origins += measured_origins
        redis_calls += measured_calls
        stats += measured_stats
    if state == "api_warm" and (origins or redis_calls):
        raise RuntimeError(f"{screen} API-warm entries expired during measurement; use fewer samples")
    if stats["errors"] or stats["rejected"] or stats["skipped"]:
        raise RuntimeError(f"{screen} encountered a failed synthetic shared-cache operation in {state}")
    return {
        "screen": screen, "redis_enabled": enabled, "state": state, "samples": samples,
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(sorted(latencies)[math.ceil(samples * .95) - 1], 3),
        "api_origin_calls": origins.total(), "api_origin_by_method": dict(origins),
        "redis_calls_by_method": dict(redis_calls),
        "redis_stats": {key: stats[key] for key in shared_cache._stats},
        "primer_samples": primer_samples,
        "primer_origin_calls": primer_origins.total(), "primer_origin_by_method": dict(primer_origins),
    }


async def benchmark(samples: int = 30, upstream_ms: float = 20, redis_ms: float = 1) -> dict:
    """Run in a dedicated process; the app lifespan and socket guard use process-wide state."""
    if samples < 1 or any(not math.isfinite(v) or v < 0 for v in (upstream_ms, redis_ms)):
        raise ValueError("samples must be positive and delays must be finite and non-negative")
    origin = _Origin(upstream_ms)
    source = PriceSource(MockBroker(), yahoo_candles=origin.yahoo_candles)
    cases = []
    with _block_network(), _isolated_app_state(), TemporaryDirectory(prefix="desk-screen-bench-") as temp:
        settings = _settings(Path(temp) / "desk.db")
        app = api.create_app(settings, prices=source)
        password = secrets.token_urlsafe(24)
        # Close the lifespan cleanly even when a measured request fails validation.
        stack = AsyncExitStack()
        try:
            await stack.enter_async_context(app.router.lifespan_context(app))
            gaps = _level_gap_cache(app)
            engine = db.make_engine(settings.database_url)
            try:
                Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(USER, password)
            finally:
                engine.dispose()
            client = await stack.enter_async_context(httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://bench.test", trust_env=False,
            ))
            login = await client.post("/api/auth/login", json={"username": USER, "password": password})
            login.raise_for_status()
            me = await client.get("/api/auth/me")
            me.raise_for_status()
            if me.json().get("username") != USER:
                raise RuntimeError("benchmark could not authenticate the ASGI client")
            for screen in SCREENS:
                # Prepare the runtime outside all cache states and validate the fixture up front.
                shared_cache.use(None, None)
                await _request_bundle(client, screen)
                for enabled in (False, True):
                    for state in STATES:
                        cases.append(await _case(client, source, origin, gaps, screen, enabled,
                                                 state, samples, redis_ms))
        finally:
            # A failed or cancelled bundle can leave shielded candle tasks behind.
            pending = {*source._inflight.values(), *source._queued}
            for task in pending:
                task.cancel()
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            finally:
                await stack.aclose()
    return {
        "timing": "synthetic in-process ASGI",
        "redis": "synthetic in-memory sync and async clients",
        "fixture_end_utc": FIXTURE_END.isoformat(),
        "origin_delay_ms": upstream_ms, "redis_delay_ms": redis_ms,
        "screens": {
            name: {"requests": [request.route() for request in requests], "shared_namespaces": SHARED_NAMESPACES[name]}
            for name, requests in SCREENS.items()
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30, help="measured request bundles per case")
    parser.add_argument("--upstream-ms", type=float, default=20, help="synthetic origin delay per call")
    parser.add_argument("--redis-ms", type=float, default=1, help="synthetic Redis delay per pipeline or SET")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(benchmark(args.samples, args.upstream_ms, args.redis_ms)), indent=2))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
