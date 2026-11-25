"""#271: Binance futures traders' long / short split per ticker."""

import asyncio
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from desk import longshort
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.longshort import LongShort
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD

T0 = 1794290400000          # ms
STEP = 300_000


def _accounts(n=longshort.DAY_POINTS, first=0.6, last=0.74):
    return [{"symbol": "X", "longAccount": str(first if i < n - 1 else last),
             "shortAccount": str(round(1 - (first if i < n - 1 else last), 4)),
             "longShortRatio": "2.8462", "timestamp": T0 + i * STEP} for i in range(n)]


def _taker(n=longshort.TAKER_HOUR):
    return [{"buySellRatio": "1.5", "buyVol": "30", "sellVol": "10" if i else "50", "timestamp": T0 + i * STEP}
            for i in range(n)]


CONTRACTS = {"NVDA": ("NVDAUSDT", "binance_perp_mark", None, 1.0),
             "SOXL": ("SMHUSDT", "leveraged", "SOXX", 3.0),
             "005930.KS": ("SAMSUNGUSDT", "binance_perp_mark", None, 1.0)}


def _source(fail=(), calls=None, accounts=None):
    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params["symbol"]
        if calls is not None:
            calls.append((request.url.path, symbol))
        if symbol in fail:
            return httpx.Response(500, json={"code": -1})
        path = request.url.path
        if path.endswith("globalLongShortAccountRatio"):
            return httpx.Response(200, json=accounts if accounts is not None else _accounts())
        if path.endswith("topLongShortPositionRatio"):
            return httpx.Response(200, json=_accounts(1, last=0.85))
        return httpx.Response(200, json=_taker())

    async def resolve(ticker):
        return CONTRACTS.get(ticker.upper())

    now = [0.0]
    ls = LongShort(resolve, httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://b"),
                   clock=lambda: now[0])
    return ls, now


def test_ratios_become_percentages_with_the_24h_change_and_the_taker_hour():
    ls, _ = _source()
    got = asyncio.run(ls.get("NVDA"))
    assert got["contract"] == "NVDAUSDT" and got["basis"] is None and got["source"] == "binance"
    assert got["accounts"] == {"long_pct": 74.0, "short_pct": 26.0, "ratio": 2.8462, "at": got["accounts"]["at"]}
    assert got["accounts"]["at"].startswith("2026-11-")
    assert got["top_positions"]["long_pct"] == 85.0
    assert got["change_24h"] == {"long_pct": 14.0}                     # 60 -> 74
    # the hour: 12 x 30 bought, 50 + 11 x 10 sold -> 360 / 520
    assert got["taker"]["buy_pct_1h"] == round(100 * 360 / 520, 1) and got["taker"]["buy_sell_ratio"] == 1.5


def test_a_short_history_has_no_24h_change_and_bad_rows_do_not_read():
    ls, _ = _source(accounts=_accounts(10))
    assert asyncio.run(ls.get("NVDA"))["change_24h"] == {"long_pct": None}
    assert longshort.split({"longAccount": "x", "shortAccount": "0.3"}) is None
    assert longshort.split({"longAccount": "0", "shortAccount": "0"}) is None
    assert longshort.split({"longAccount": "nan", "shortAccount": "0.3"}) is None


def test_no_contract_is_null_and_a_leveraged_etf_names_its_base():
    ls, _ = _source()
    assert asyncio.run(ls.get("AAPL")) == {"ticker": "AAPL", "contract": None}
    soxl = asyncio.run(ls.get("SOXL"))
    assert soxl["contract"] == "SMHUSDT" and soxl["basis"] == "SOXX" and soxl["multiple"] == 3.0


def test_a_failing_contract_is_null_for_that_ticker_only_and_kept_a_minute():
    calls: list = []
    ls, now = _source(fail={"NVDAUSDT"}, calls=calls)

    async def both():
        return await asyncio.gather(ls.get("NVDA"), ls.get("005930.KS"))

    nvda, samsung = asyncio.run(both())
    assert nvda["accounts"] is None and nvda["error"] and nvda["contract"] == "NVDAUSDT"
    assert samsung["accounts"]["long_pct"] == 74.0
    before = len(calls)
    now[0] = longshort.FAIL_TTL_SEC - 1
    asyncio.run(ls.get("NVDA"))
    assert len(calls) == before                                         # the failure is remembered
    now[0] = longshort.FAIL_TTL_SEC + 1
    asyncio.run(ls.get("NVDA"))
    assert len(calls) > before


def test_each_contract_is_read_once_per_five_minutes():
    calls: list = []
    ls, now = _source(calls=calls)
    asyncio.run(ls.get("NVDA"))
    assert len(calls) == 3
    now[0] = longshort.CACHE_TTL_SEC - 1
    asyncio.run(ls.get("NVDA"))
    assert len(calls) == 3
    now[0] = longshort.CACHE_TTL_SEC + 1

    async def expired():
        got = await ls.get("NVDA")             # #859 a quick refresh is waited for (REFRESH_WAIT_SEC)
        assert got["accounts"] and len(calls) == 6
        await ls.settled()
        return got
    asyncio.run(expired())
    assert len(calls) == 6


def test_the_endpoint_needs_a_session_and_answers_per_ticker(db_url):
    ls, _ = _source(fail={"SAMSUNGUSDT"})
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0)
    with TestClient(create_app(settings, runner=make_fake_runner(0), longshort=ls),
                    base_url="https://testserver") as client:
        engine = make_engine(db_url)
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
        engine.dispose()
        assert client.get("/api/longshort?tickers=NVDA").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        items = client.get("/api/longshort?tickers=nvda,AAPL,005930.KS,NVDA").json()["items"]
        assert [i["ticker"] for i in items] == ["NVDA", "AAPL", "005930.KS"]
        assert items[0]["accounts"]["long_pct"] == 74.0 and items[1]["contract"] is None
        assert items[2]["accounts"] is None and items[2]["error"]
        assert client.get("/api/longshort?tickers=" + ",".join(f"T{i}" for i in range(31))).status_code == 422
        assert client.get("/api/longshort?tickers=NV%20DA").status_code == 422


def test_a_failing_contract_list_is_not_retried_for_every_ticker():
    asked = []

    async def resolve(ticker):
        asked.append(ticker)
        raise RuntimeError("exchangeInfo 429")

    now = [0.0]
    ls = LongShort(resolve, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)),
                                              base_url="https://b"), clock=lambda: now[0])
    first, second = asyncio.run(ls.get("NVDA")), asyncio.run(ls.get("TSLA"))
    assert first["contract"] is None and first["error"] and second["error"] and asked == ["NVDA"]
    now[0] = longshort.FAIL_TTL_SEC + 1
    asyncio.run(ls.get("TSLA"))
    assert asked == ["NVDA", "TSLA"]



def test_past_five_minutes_a_reader_gets_the_last_summary_while_one_refresh_runs():
    # #859 a board's first poll after the expiry used to wait out Binance in waves of PARALLEL
    calls: list = []
    fail: set = set()
    ls, now = _source(calls=calls, fail=fail)

    async def run():
        first = await ls.get("NVDA")
        now[0] = longshort.CACHE_TTL_SEC + 1
        fail.add("NVDAUSDT")                                   # Binance now fails for it
        a, b = await asyncio.gather(ls.get("NVDA"), ls.get("NVDA"))
        await ls.settled()
        assert a == b == first and len(calls) == 6             # the last good one to both, from one refresh
        assert await ls.get("NVDA") == first                   # its failure kept: still the last good one
        assert len(calls) == 6                                 # ... and no retry before FAIL_TTL_SEC
        now[0] = longshort.CACHE_TTL_SEC + longshort.STALE_MAX_SEC + 1
        late = await ls.get("NVDA")                            # too old to answer: read now, and it fails
        assert late["error"] and late["accounts"] is None
    asyncio.run(run())



def test_a_slow_refresh_is_not_waited_out():
    # #859 Binance slower than REFRESH_WAIT_SEC: the last good summary now, the fresh one on a later poll
    gate = asyncio.Event()
    calls: list = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) > 3:
            await gate.wait()
        path = request.url.path
        if path.endswith("globalLongShortAccountRatio"):
            return httpx.Response(200, json=_accounts(last=0.7 if len(calls) > 3 else 0.74))
        if path.endswith("topLongShortPositionRatio"):
            return httpx.Response(200, json=_accounts(1, last=0.85))
        return httpx.Response(200, json=_taker())

    async def resolve(ticker):
        return CONTRACTS.get(ticker.upper())

    async def run():
        now = [0.0]
        ls = LongShort(resolve, httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://b"),
                       clock=lambda: now[0])
        first = await ls.get("NVDA")
        now[0] = longshort.CACHE_TTL_SEC + 1
        assert await ls.get("NVDA") == first                   # waited REFRESH_WAIT_SEC, then the last one
        gate.set()
        await ls.settled()
        fresh = await ls.get("NVDA")
        assert fresh != first and fresh["accounts"]["long_pct"] == 70.0
        await ls.aclose()
    asyncio.run(run())
