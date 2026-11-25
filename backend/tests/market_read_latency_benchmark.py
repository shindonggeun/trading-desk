"""#859: replay fixed market reads in a dedicated, network-isolated process."""

import argparse
import asyncio
import json
import math
import os
import socket
import time
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from unittest.mock import patch

import httpx
from sqlalchemy import insert

DAY = date(2026, 11, 10)
SYMBOLS = ("BTC", "ETH", "SOL")
BARS_PER_DAY = 421
HISTORY_DAYS = 20
HEALTH_OFFSET_SEC = 0.025
ROUTES = {
    "candles": ("/api/candles", {"ticker": "QQQ", "interval": "1d", "count": 130, "indicators": "rsi,stoch"}),
    "longshort": ("/api/longshort", {"tickers": ",".join(SYMBOLS)}),
    "intraday": ("/api/market/flow/intraday", {"date": DAY.isoformat(), "bucket": 5}),
    "health": ("/api/health", {}),
}


def _seed(database_url: str, password: str) -> None:
    from desk.auth import Auth
    from desk.db import futures_snapshots, make_engine

    rows = []
    day = DAY
    for _ in range(HISTORY_DAYS + 1):
        start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(minutes=15)
        for minute in range(BARS_PER_DAY):
            net = minute * 8 - (minute // 210) * 3800
            rows.append({"at": start + timedelta(minutes=minute), "trade_date": day.isoformat(), "code": "A01612",
                         "price": 700 + minute / 10, "foreign_contracts": net, "kospi_foreign_100m": net / 2,
                         "kospi_program_total_100m": net / 3})
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    engine = make_engine(database_url)
    try:
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user("bench@example.test", password)
        with engine.begin() as conn:
            conn.execute(insert(futures_snapshots), rows)
    finally:
        engine.dispose()


def _validate(route: str, response: httpx.Response) -> None:
    response.raise_for_status()
    body = response.json()
    if route == "candles":
        bars = body["bars"]
        valid = (body["ticker"] == "QQQ" and body["source"] == "yahoo" and len(bars) == 130
                 and bars[-1]["rsi"] is not None and bars[0]["at"] < bars[-1]["at"])
    elif route == "longshort":
        valid = [(item["contract"], item["accounts"]["long_pct"], item["taker"]["buy_pct_1h"])
                 for item in body["items"]] == [(ticker + "USDT", 60.0, 75.0) for ticker in SYMBOLS]
    elif route == "intraday":
        valid = (body["date"] == DAY.isoformat() and body["bucket_min"] == 5
                 and len(body["futures"]) == 85 and body["spot"]["kospi"][-1]["foreign"] is not None)
    else:
        valid = body == {"ok": True}
    if not valid:
        raise ValueError(f"unexpected {route} payload")


async def _window(client: httpx.AsyncClient, bundles: int) -> tuple[dict[str, list[float]], bool]:
    async def read(route: str, scheduled: float | None = None):
        path, params = ROUTES[route]
        start = time.perf_counter() if scheduled is None else scheduled
        response = await client.get(path, params=params)
        return route, response, (time.perf_counter() - start) * 1000

    async def health():
        await asyncio.sleep(max(0, deadline - time.perf_counter()))
        overlap = any(not job.done() for job in jobs)
        return await read("health", deadline), overlap

    values = {route: [] for route in ROUTES}
    deadline = time.perf_counter() + HEALTH_OFFSET_SEC
    async with asyncio.TaskGroup() as group:
        jobs = [group.create_task(read(route)) for _ in range(bundles) for route in ROUTES if route != "health"]
        probe = group.create_task(health())
    health_result, overlap = probe.result()
    for route, response, ms in [*(job.result() for job in jobs), health_result]:
        _validate(route, response)
        values[route].append(ms)
    return values, overlap


def _metrics(values: list[float], precision: int = 3) -> dict:
    return {"n": len(values), "p95_ms": round(sorted(values)[math.ceil(len(values) * 0.95) - 1], precision),
            "max_ms": round(max(values), precision)}


async def benchmark(samples: int, bundles: int, origin_ms: float) -> dict:
    # desk.settings reads dotenv and other modules resolve cache paths on import.
    with (TemporaryDirectory(prefix="desk-market-latency-") as temp,
          patch.dict(os.environ, {"HOME": temp, "USERPROFILE": temp}, clear=True),
          patch("dotenv.load_dotenv", return_value=False)):
        return await _measure(Path(temp), samples, bundles, origin_ms)


async def _measure(temp: Path, samples: int, bundles: int, origin_ms: float) -> dict:
    from desk import api, flow_intraday, program_flow
    from desk.broker.base import Bar
    from desk.broker.mock import MockBroker
    from desk.futures_history import FuturesHistory
    from desk.longshort import LongShort
    from desk.prices import PriceSource
    from desk.settings import Settings

    calls: Counter[str] = Counter()
    phase: dict[str, list[float]] = {name: [] for name in ("flow_sql", "flow_daily", "flow_bands", "flow_view")}
    lock = Lock()
    intraday, daily, bands, view = FuturesHistory.intraday, FuturesHistory.daily, program_flow.bands, flow_intraday.view

    def counted(name, operation):
        def run(*args, **kwargs):
            started = time.perf_counter()
            try:
                return operation(*args, **kwargs)
            finally:
                with lock:
                    phase[name].append((time.perf_counter() - started) * 1000)
                    calls[name] += 1
        return run

    def yahoo(_ticker, _interval, count):
        with lock:
            calls["yahoo_candles"] += 1
        time.sleep(origin_ms / 1000)
        return [Bar((DAY - timedelta(days=count - i)).isoformat() + "T00:00:00+00:00",
                    100 + i, 102 + i, 99 + i, 101 + i, 1000) for i in range(count)]

    async def resolve(ticker):
        calls["binance_resolution"] += 1
        return (ticker + "USDT", "binance_perp_mark", None, 1.0) if ticker in SYMBOLS else None

    async def binance(request):
        calls["binance_http"] += 1
        await asyncio.sleep(origin_ms / 1000)
        row = {"timestamp": 1794268800000, "longAccount": "0.60", "shortAccount": "0.40",
               "longShortRatio": "1.5", "buyVol": "3", "sellVol": "1", "buySellRatio": "3"}
        return httpx.Response(200, json=[row])

    def blocked(*_args, **_kwargs):
        calls["unexpected_network"] += 1
        raise RuntimeError("network disabled for this benchmark")

    states = ("cold", "warm")
    timings = {state: {route: [] for route in ROUTES} for state in states}
    counts = {state: Counter() for state in states}
    stages = {state: {name: [] for name in phase} for state in states}
    with ExitStack() as stack:
        for method in ("connect", "connect_ex", "sendto"):
            stack.enter_context(patch.object(socket.socket, method, blocked))
        stack.enter_context(patch.object(socket, "getaddrinfo", blocked))
        stack.enter_context(patch.object(program_flow, "_cache", {}))
        for target, attr, name, operation in (
            (FuturesHistory, "intraday", "flow_sql", intraday), (FuturesHistory, "daily", "flow_daily", daily),
            (program_flow, "bands", "flow_bands", bands), (flow_intraday, "view", "flow_view", view),
        ):
            stack.enter_context(patch.object(target, attr, counted(name, operation)))

        settings = Settings(database_url=f"sqlite:///{(temp / 'desk.db').as_posix()}", role="api", fake_runs=True,
                            market_stream=False, macro_interval_sec=0, report_every_days=0, score_interval_sec=0,
                            retention_days=0, morning_brief_at="", futures_snapshot_min=0)
        prices = PriceSource(MockBroker(), yahoo_candles=yahoo)
        mock = httpx.AsyncClient(base_url="https://fixture.test", transport=httpx.MockTransport(binance), trust_env=False)
        longshort = LongShort(resolve, mock)
        password = "isolated-synthetic-password"
        try:
            app = api.create_app(settings, prices=prices, longshort=longshort)
            async with (app.router.lifespan_context(app),
                        httpx.AsyncClient(base_url="https://bench.test", transport=httpx.ASGITransport(app=app),
                                          trust_env=False) as client):
                _seed(settings.database_url, password)
                login = await client.post("/api/auth/login", json={"username": "bench@example.test", "password": password})
                login.raise_for_status()
                initial, _ = await _window(client, bundles)
                for _ in range(samples):
                    prices._candles.clear()
                    await longshort.settled()
                    longshort._cache.clear()
                    longshort._good.clear()         # #859 the last good summaries too: a cold read
                    program_flow._cache.clear()
                    for state in states:
                        previous = calls.copy()
                        for values in phase.values():
                            values.clear()
                        window, overlap = await _window(client, bundles)
                        delta = calls - previous
                        cold = state == "cold"
                        if delta["yahoo_candles"] != int(cold) or delta["binance_http"] != 3 * len(SYMBOLS) * cold:
                            raise RuntimeError(f"unexpected {state} origin calls: {dict(delta)}")
                        counts[state].update(delta)
                        counts[state]["overlapping_health"] += int(overlap)
                        for route, values in window.items():
                            timings[state][route].extend(values)
                        for name, values in phase.items():
                            stages[state][name].extend(values)
        finally:
            await mock.aclose()
    if calls["unexpected_network"]:
        raise RuntimeError("unexpected network used")
    return {
        "samples": samples, "bundles": bundles, "origin_delay_ms": origin_ms, "health_offset_ms": HEALTH_OFFSET_SEC * 1000,
        "snapshots": (HISTORY_DAYS + 1) * BARS_PER_DAY,
        "initial_ms": {name: round(max(values), 3) for name, values in initial.items()},
        "states": {state: {
            "routes": {name: _metrics(values) for name, values in timings[state].items()},
            "stages": {name: _metrics(values) for name, values in stages[state].items() if values},
            "calls": dict(counts[state]),
        } for state in states},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--bundles", type=int, default=4)
    parser.add_argument("--origin-ms", type=float, default=50)
    args = parser.parse_args()
    if args.samples < 1 or args.bundles < 1 or not math.isfinite(args.origin_ms) or args.origin_ms < 0:
        parser.error("samples and bundles must be positive, origin delay finite and non-negative")
    print(json.dumps(asyncio.run(benchmark(args.samples, args.bundles, args.origin_ms)), indent=2))


if __name__ == "__main__":
    main()
