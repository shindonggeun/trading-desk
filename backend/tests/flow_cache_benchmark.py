"""Offline flow cache benchmark. Run from backend: python -m tests.flow_cache_benchmark.

The timer spans the in-process ASGI request, including session lookup and
response encoding. The Redis tier and origin delays are synthetic. Login and
priming are excluded. Cold starts with neither cache populated, collector_warm
primes a separate producer with API L1 empty, and api_warm primes API L1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from desk import db, futures_flow, shared_cache
from desk.api import create_app
from desk.auth import Auth
from desk.broker.base import FlowRecord, IndexPrice
from desk.broker.mock import MockBroker
from desk.market_flow import MarketFlow
from desk.settings import Settings
from tests.test_futures_flow import INVESTOR_OUT, PRICE_OUT

NOW = datetime(2026, 11, 6, 1, tzinfo=UTC)
DAY = NOW.date().isoformat()
STAMP = NOW.isoformat()
USER = "flow-bench@example.test"
PASSWORD = "flow-bench-fixture-only"
ENDPOINTS = ("market_flow", "futures")
STATES = ("cold", "collector_warm", "api_warm")
URLS = {
    "market_flow": "/api/market/flow?market=KOSPI&interval=1d&count=1",
    "futures": "/api/market/futures",
}


class _RedisRead:
    """The GET/PTTL pipeline used by the production async cache adapter."""

    def __init__(self, redis):
        self.redis = redis
        self.key = None

    def get(self, key):
        self.key = key

    def pttl(self, key):
        assert key == self.key

    async def execute(self):
        await asyncio.sleep(self.redis.delay)
        row = self.redis.entries.get(self.key)
        if row is None:
            return None, -2
        value, expires = row
        left = math.ceil((expires - time.monotonic()) * 1000)
        return (value, left) if left > 0 else (None, -2)


class _MemoryRedis:
    def __init__(self, delay_ms: float):
        self.delay = delay_ms / 1000
        self.entries = {}

    def pipeline(self, *, transaction):
        assert not transaction
        return _RedisRead(self)

    async def set(self, key, value, *, px):
        await asyncio.sleep(self.delay)
        self.entries[key] = (value, time.monotonic() + px / 1000)


class _Spot(MockBroker):
    def __init__(self, delay, counts):
        self.delay = delay
        self.counts = counts

    async def investor_flow(self, market, interval, count, until=None):
        self.counts["spot_flow"] += 1
        await asyncio.sleep(self.delay)
        return [FlowRecord(DAY, STAMP, {"foreigner": (300_000_000, 200_000_000)}, {})], None

    async def index_prices(self, symbols):
        self.counts["spot_index"] += 1
        await asyncio.sleep(self.delay)
        return [IndexPrice(symbol, 2812.45, STAMP) for symbol in symbols]


class _FuturesRest:
    def __init__(self, delay, counts):
        self.delay = delay
        self.counts = counts

    async def get(self, path, _tr, _params):
        if path == futures_flow.PRICE_PATH:
            source, payload = "futures_price", PRICE_OUT
        elif path == futures_flow.INVESTOR_PATH:
            source, payload = "futures_investors", INVESTOR_OUT
        else:
            raise AssertionError(f"unexpected fake futures origin: {path}")
        self.counts[source] += 1
        await asyncio.sleep(self.delay)
        return payload

    async def aclose(self):
        pass


class _Sources:
    def __init__(self, delay_ms):
        delay = delay_ms / 1000
        self.counts: Counter[str] = Counter()
        self.spot = MarketFlow(_Spot(delay, self.counts), now=lambda: NOW)
        self.futures = futures_flow.FuturesFlow(_FuturesRest(delay, self.counts), self.spot, now=lambda: NOW)

    def clear_l1(self):
        # Clear both tiers of API-local results: futures also reads the spot service.
        self.spot._cache.clear()
        self.futures._cache = None

    async def collect(self, endpoint):
        if endpoint == "market_flow":
            await self.spot.get("KOSPI", "1d", 1, fresh=True)
        else:
            await self.futures.get(fresh=True)


async def _request(client, endpoint):
    start = time.perf_counter_ns()
    response = await client.get(URLS[endpoint])
    latency_ms = (time.perf_counter_ns() - start) / 1_000_000
    response.raise_for_status()
    body = response.json()
    assert body["supported"] and body["error"] is None, f"{endpoint} did not return a valid benchmark payload"
    if endpoint == "market_flow":
        assert body["records"][0]["date"] == DAY
    else:
        assert body["price"]["price"] == 412.35 and body["investors"]["foreign"]["net_contracts"] == -4100
    return latency_ms


async def _case(client, api_sources, collector, endpoint, enabled, state, samples, redis_delay_ms):
    redis = _MemoryRedis(redis_delay_ms) if enabled else None
    shared_cache.use(None, redis)
    api_sources.clear_l1()
    collector.clear_l1()
    if state == "collector_warm":
        await collector.collect(endpoint)
    elif state == "api_warm":
        await _request(client, endpoint)
    before = api_sources.counts.copy()
    old_hits = shared_cache.stats()["hits"]
    latencies = []
    for _ in range(samples):
        if state != "api_warm":
            api_sources.clear_l1()
        if state == "cold" and redis is not None:
            redis.entries.clear()
        latencies.append(await _request(client, endpoint))
    counted = api_sources.counts - before
    return {
        "endpoint": URLS[endpoint], "redis_enabled": enabled, "state": state, "samples": samples,
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(sorted(latencies)[math.ceil(samples * .95) - 1], 3),
        "api_origin_calls": sum(counted.values()), "api_origin_by_method": dict(counted),
        "redis_hits": shared_cache.stats()["hits"] - old_hits,
    }


async def benchmark(samples: int = 30, upstream_ms: float = 20, redis_ms: float = 1) -> dict:
    if samples < 1 or any(not math.isfinite(v) or v < 0 for v in (upstream_ms, redis_ms)):
        raise ValueError("samples must be positive and delays must be finite and non-negative")
    api_sources, collector = _Sources(upstream_ms), _Sources(upstream_ms)
    with TemporaryDirectory(prefix="desk-flow-bench-") as temp:
        url = "sqlite:///" + (Path(temp) / "desk.db").as_posix()
        settings = Settings(database_url=url, role="api", fake_runs=True, cookie_secure=False,
                            macro_interval_sec=0, report_every_days=0, redis_url="", redis_sentinels=(), redis_cache_key="")
        app = create_app(settings, flow=api_sources.spot, futures=api_sources.futures)
        engine = db.make_engine(url)
        try:
            Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(USER, PASSWORD)
        finally:
            engine.dispose()
        try:
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench.test",
                                  trust_env=False) as client,
            ):
                login = await client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
                login.raise_for_status()
                measurements = []
                for endpoint in ENDPOINTS:
                    # Warm the HTTP/DB machinery outside any measured cache scenario.
                    await _request(client, endpoint)
                    for enabled in (False, True):
                        for state in STATES:
                            measurements.append(await _case(client, api_sources, collector, endpoint,
                                                            enabled, state, samples, redis_ms))
        finally:
            shared_cache.use(None, None)
    return {"transport": "in-process ASGI", "redis": "in-memory synthetic", "fixture_at_utc": STAMP,
            "origin_delay_ms": upstream_ms, "redis_delay_ms": redis_ms, "cases": measurements}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30, help="requests per case")
    parser.add_argument("--upstream-ms", type=float, default=20, help="synthetic origin delay per call")
    parser.add_argument("--redis-ms", type=float, default=1, help="synthetic Redis pipeline/SET delay")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(benchmark(args.samples, args.upstream_ms, args.redis_ms)), indent=2))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
