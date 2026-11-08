"""#171 step 7: different instruments run side by side up to DESK_MAX_CONCURRENT_RUNS, the same one
never twice at once, and a provider rate limit drops the desk to one run at a time for a while."""

import asyncio
import threading
import time
from datetime import timedelta as td

from fastapi.testclient import TestClient

from desk import api
from desk.auth import Auth
from desk.db import make_engine
from desk.runner import _Gate, make_fake_runner, rate_limited
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _wait_done


class RateLimitError(Exception):        # what openai raises, by name
    status_code = 429


def test_rate_limits_are_found_in_the_chain_and_in_error_strings():
    assert rate_limited(RateLimitError("slow down"))
    wrapped = RuntimeError("graph step failed")
    wrapped.__cause__ = RateLimitError("x")
    assert rate_limited(wrapped)
    assert rate_limited("RateLimitError: Error code: 429 - usage_limit_reached")
    assert rate_limited(ConnectionError("HTTP 429 Too Many Requests"))
    assert not rate_limited(ValueError("entry 1429.5 is far from 1300"))
    assert not rate_limited("ConnectionError: no network in tests")
    assert not rate_limited(None)


def test_the_gate_lets_two_in_and_one_after_a_rate_limit():
    now = [0.0]
    gate = _Gate(2, cooldown=60, clock=lambda: now[0])
    assert gate.limit() == 2
    assert gate.back_off() is True and gate.limit() == 1
    assert gate.back_off() is False                # already on: extended, not announced again
    now[0] = 61
    assert gate.limit() == 2

    async def scenario():
        order = []

        async def run(name, hold):
            async with gate:
                order.append(f"+{name}")
                await asyncio.sleep(hold)
                order.append(f"-{name}")

        await asyncio.gather(run("a", 0.05), run("b", 0.05), run("c", 0.01))
        return order

    order = asyncio.run(scenario())
    assert order[:2] == ["+a", "+b"] and order.index("+c") > order.index("-a")


def _desk(db_url, graph, limit=2):
    client = TestClient(api.create_app(Settings(database_url=db_url, fake_delay=0, max_concurrent_runs=limit),
                                       runner=graph), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client


def _tracking(hold=0.3, fail=None):
    """A scripted graph that records how many runs it had at once (and may fail one ticker)."""
    fake, lock, state = make_fake_runner(0), threading.Lock(), {"active": 0, "peak": 0}

    def graph(request, emit, portfolio):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(hold)
            if fail and request.ticker == fail:
                raise RateLimitError("Error code: 429 - usage_limit_reached")
            return fake(request, emit, portfolio)
        finally:
            with lock:
                state["active"] -= 1

    return graph, state


def _together(client, *tickers):
    client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
    ids = [client.post("/api/runs", json={"ticker": t, "trade_date": "2026-11-04"}).json()["id"] for t in tickers]
    return [_wait_done(client, i) for i in ids]


def test_two_instruments_run_side_by_side(db_url):
    graph, state = _tracking()
    with _desk(db_url, graph) as client:
        done = _together(client, "NVDA", "AAPL")
    assert [d["status"] for d in done] == ["done", "done"] and state["peak"] == 2


def test_the_same_instrument_waits_even_as_a_leveraged_product(db_url):
    graph, state = _tracking(hold=0.15)
    with _desk(db_url, graph) as client:
        done = _together(client, "NVDA", "NVDA", "SOXX", "SOXL")
    assert all(d["status"] == "done" for d in done)
    assert state["peak"] == 2                        # NVDA beside SOXX, never NVDA beside NVDA
    first, second = done[0], done[1]
    assert second["started_at"] >= first["finished_at"]


def test_one_limit_is_the_old_behaviour(db_url):
    graph, state = _tracking(hold=0.1)
    with _desk(db_url, graph, limit=1) as client:
        _together(client, "NVDA", "AAPL")
    assert state["peak"] == 1


def test_a_rate_limit_drops_the_desk_to_one_run_at_a_time(db_url):
    graph, state = _tracking(hold=0.15, fail="TSLA")
    with _desk(db_url, graph) as client:
        failed, = _together(client, "TSLA")
        assert failed["status"] == "failed" and "429" in failed["error"]
        state["peak"] = 0
        _together(client, "NVDA", "AAPL")
    assert state["peak"] == 1
