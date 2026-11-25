"""Offline API latency benchmark (#859).

Run in a dedicated process from backend: python -m tests.secondary_api_latency_benchmark.
"""

from __future__ import annotations

import argparse
import asyncio
import calendar as calendar_days
import json
import math
import secrets
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from desk import api, db, earnings, events_calendar, market_calendar, paper
from desk.auth import Auth
from tests.screen_cache_benchmark import _block_network, _isolated_app_state, _settings

CALENDAR = "/api/calendar"
LINE_WATCH = "/api/notify/line-watch"
PAPER = "/api/paper/summary"
HEALTH = "/api/health"
ROUTES = (CALENDAR, LINE_WATCH, PAPER, HEALTH)
THRESHOLDS_MS = {CALENDAR: 500, LINE_WATCH: 500, PAPER: 500, HEALTH: 100}
USER = "secondary-api-benchmark@example.test"
ROWS = 120
SIGNALS = 60
FIXTURE_START = datetime(2020, 1, 1, 14, tzinfo=UTC)
TICKER = "SPY"
EVENT_ID = "fixture-cpi"


def _calendar_sources(month: str) -> tuple[events_calendar.Calendar, earnings.Earnings]:
    at = datetime.fromisoformat(f"{month}-12T13:30:00+00:00")
    events = events_calendar.Calendar()
    events.by_source = {"bls": [events_calendar.Event(EVENT_ID, "Synthetic CPI", at, 2, "bls")]}
    reports = earnings.Earnings()
    day = at.date().isoformat()
    reports.us = {day: [earnings.blank(ticker=TICKER, name="Synthetic SPY", date=day, market="us")]}
    reports.us_at = {day: at.isoformat()}
    return events, reports


def _seed_rows(database_url: str, password: str) -> dict[str, float]:
    engine = db.make_engine(database_url)
    rates, runs, signals = {}, [], []
    try:
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(USER, password)
        for i in range(ROWS):
            at = FIXTURE_START + timedelta(days=i)
            day = at.date().isoformat()
            rates[day] = 1350.0
            ticker = TICKER if i % 2 else "005930.KS"
            price = 100.0 if ticker == TICKER else 10_000.0
            outcome = {"status": "target", "plan_at": at.isoformat(), "r": 2.0,
                       "filled_at": (at + timedelta(hours=1)).isoformat(),
                       "exited_at": (at + timedelta(hours=2)).isoformat()}
            runs.append({
                "id": f"secondary-bench-{i:04}", "ticker": ticker, "trade_date": day, "status": "done",
                "options": {"name": ticker, "horizon": "swing"},
                "plan": {"bias": "long", "entry_price": price, "stop_loss": price * .95,
                         "price_target": price * 1.1, "rating": "Buy", "horizon": "swing",
                         "live_price": {"fetched_at": at.isoformat()}},
                "outcome": outcome, "outcome_status": "target", "created_at": at,
                "scored_at": at + timedelta(hours=3),
            })
        for i in range(SIGNALS):
            at = FIXTURE_START + timedelta(days=i)
            signals.append({
                "trade_date": at.date().isoformat(), "ticker": TICKER, "timeframe": "5m", "event": "touch",
                "rule": "touch_support", "line_id": f"benchmark-support-{i:02}", "at": at.isoformat(),
                "price": 101, "line_price": 100, "text": "Synthetic support touch", "created_at": at,
            })
        with engine.begin() as conn:
            conn.execute(db.runs.insert(), runs)
            conn.execute(db.line_signals.insert(), signals)
    finally:
        engine.dispose()
    return rates


async def _configure(client: httpx.AsyncClient, password: str) -> None:
    login = await client.post("/api/auth/login", json={"username": USER, "password": password})
    login.raise_for_status()
    (await client.post("/api/favorites", json={"ticker": TICKER})).raise_for_status()
    (await client.put(LINE_WATCH, json={"enabled": True, "tickers": [TICKER], "timeframes": ["5m"]})).raise_for_status()


def _check_body(route: str, body: dict, month: str) -> None:
    if route == CALENDAR:
        rows = body["days"]
        days = calendar_days.monthrange(*map(int, month.split("-")))[1]
        watched = {r["ticker"] for d in rows for r in d["earnings"] if r["watched"]}
        events = {e["id"] for d in rows for e in d["events"]}
        valid = body["month"] == month and len(rows) == days and TICKER in watched and EVENT_ID in events
    elif route == LINE_WATCH:
        rows = body["recent"]
        valid = (body["can_edit"] is True and body["enabled"] is True and body["tickers"] == [TICKER]
                 and body["timeframes"] == ["5m"] and len(rows) == 20
                 and rows[0]["line_id"] == f"benchmark-support-{SIGNALS - 1:02}"
                 and rows[0]["price"] == 101 and rows[0]["line_price"] == 100)
    elif route == PAPER:
        valid = (body["settings_version"] == 0 and body["trades"]["closed"] == ROWS
                 and math.isfinite(body["realized_pnl"]) and body["realized_pnl"] > 0
                 and body["balance"] == body["start_capital"] + body["realized_pnl"])
    else:
        valid = body == {"ok": True}
    if not valid:
        raise RuntimeError(f"unexpected fixture response from {route}")


async def _window(client: httpx.AsyncClient, month: str, bundles: int) -> dict[str, list[float]]:
    async def send(route: str) -> tuple[str, httpx.Response, float]:
        params = {"month": month} if route == CALENDAR else None
        response = await client.get(route, params=params)
        return route, response, (time.perf_counter() - dispatched) * 1000

    # A common dispatch time counts any scheduling wait on the event loop. The
    # health request is submitted in the same window after all domain requests.
    dispatched = time.perf_counter()
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(send(route)) for route in ROUTES[:-1] for _ in range(bundles)]
        tasks.append(group.create_task(send(HEALTH)))
    times = {route: [] for route in ROUTES}
    for task in tasks:
        route, response, elapsed = task.result()
        response.raise_for_status()
        _check_body(route, response.json(), month)
        times[route].append(elapsed)
    return times


def _metrics(values: list[float], threshold: int) -> dict:
    ordered = sorted(values)
    p95 = ordered[math.ceil(len(ordered) * .95) - 1]
    return {"samples": len(ordered), "p95_ms": round(p95, 3), "max_ms": round(ordered[-1], 3),
            "threshold_ms": threshold, "pass": p95 <= threshold}


async def benchmark(samples: int = 30, bundles: int = 4, fx_ms: float = 50) -> dict:
    if samples < 1 or bundles < 1 or not math.isfinite(fx_ms) or fx_ms < 0:
        raise ValueError("samples and bundles must be positive, and FX delay finite and non-negative")
    month = datetime.now(market_calendar.KST).strftime("%Y-%m")
    events, reports = _calendar_sources(month)
    elapsed = {state: {route: [] for route in ROUTES} for state in ("cold", "fx_due", "warm")}
    origins = {state: Counter() for state in elapsed}
    calls = Counter()
    simulate = paper.simulate

    def simulate_counted(*args, **kwargs):
        # Paper.result serializes simulate and rate reads under its compute lock.
        calls["paper_computes"] += 1
        return simulate(*args, **kwargs)

    def rates_counted():
        calls["fx_reads"] += 1
        time.sleep(fx_ms / 1000)
        return rates

    with (_block_network(), _isolated_app_state(), TemporaryDirectory(prefix="desk-secondary-bench-") as temp,
          patch.object(events_calendar, "CACHE", events), patch.object(earnings, "CACHE", reports),
          patch.object(paper, "simulate", simulate_counted)):
        settings = _settings(Path(temp) / "desk.db")
        app = api.create_app(settings)
        app.state.paper.fetch_rates = rates_counted
        caches = (market_calendar._closed, market_calendar._kr_data)
        password = secrets.token_urlsafe(24)
        async with app.router.lifespan_context(app):
            rates = _seed_rows(settings.database_url, password)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://bench.test",
                                         trust_env=False) as client:
                await _configure(client, password)
                # Keep initial client/worker setup separate from the app-cache p95.
                initial = await _window(client, month, bundles)
                for _ in range(samples):
                    for cache in caches:
                        cache.cache_clear()
                    app.state.paper._cache = None
                    app.state.paper._rates = None
                    app.state.paper._rates_next = 0.0
                    for state in elapsed:
                        if state == "fx_due":
                            # #859 the six-hourly case: rates held but due, the result to recompute
                            app.state.paper._cache = None
                            app.state.paper._rates_next = 0.0
                        before = calls.copy()
                        misses = sum(cache.cache_info().misses for cache in caches)
                        window = await _window(client, month, bundles)
                        if state == "fx_due":
                            for _ in range(int(fx_ms / 10) + 500):        # the background refetch lands
                                if not getattr(app.state.paper, "_fx_refreshing", False):
                                    break
                                await asyncio.sleep(0.01)
                        delta = calls - before
                        expected = 1 if state == "cold" else 0
                        wanted = {"paper_computes": 1 if state != "warm" else 0, "fx_reads": expected}
                        if state == "fx_due":
                            wanted["fx_reads"] = 1
                        if any(delta[key] != value for key, value in wanted.items()):
                            raise RuntimeError(f"wrong paper cache state in {state} window: {dict(delta)}")
                        calendar_misses = sum(cache.cache_info().misses for cache in caches) - misses
                        if bool(calendar_misses) != bool(expected):
                            raise RuntimeError(f"wrong calendar cache state in {state} window: {calendar_misses} misses")
                        origins[state].update(delta)
                        origins[state]["calendar_local_misses"] += calendar_misses
                        for route, values in window.items():
                            elapsed[state][route].extend(values)

    states = {state: {
        "routes": {route: _metrics(values, THRESHOLDS_MS[route]) for route, values in routes.items()},
        "counters": {key: origins[state][key] for key in ("paper_computes", "fx_reads", "calendar_local_misses")},
    } for state, routes in elapsed.items()}
    return {
        "timing": "in-process ASGI elapsed from concurrent dispatch, login excluded",
        "cold": "calendar local caches and paper result/FX invalidated; SQLite pages retained",
        "fx_due": "paper result invalidated and its held FX rates due (the six-hourly refetch, #859)",
        "warm": "same requests immediately after the cold window",
        "line_watch": "same uncached database reads in both windows",
        "samples_per_state": samples, "bundles_per_window": bundles, "health_probes_per_window": 1,
        "paper_runs": ROWS, "line_signals": SIGNALS, "synthetic_fx_delay_ms": fx_ms, "month": month,
        "initial_window_max_ms": {route: round(max(values), 3) for route, values in initial.items()},
        "states": states,
        "pass": all(metric["pass"] for state in states.values() for metric in state["routes"].values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30, help="independent cold/warm window pairs")
    parser.add_argument("--bundles", type=int, default=4, help="concurrent requests per domain route in each window")
    parser.add_argument("--fx-ms", type=float, default=50, help="synthetic blocking FX delay per cold window")
    args = parser.parse_args()
    try:
        result = asyncio.run(benchmark(args.samples, args.bundles, args.fx_ms))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
