import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.estimate import BinanceEstimator
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD

SATURDAY_KST_10AM = datetime(2026, 11, 7, 1, 0, tzinfo=UTC)
WEDNESDAY_KST_10AM = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)

CLOSES = {
    "005930.KS": [(date(2026, 11, 5), 215000.0), (date(2026, 11, 6), 218000.0)],
    "NVDA": [(date(2026, 11, 5), 200.0), (date(2026, 11, 6), 204.2)],
    "BRK-B": [(date(2026, 11, 6), 480.0)],
}
EXCHANGE_INFO = {"symbols": [
    {"symbol": "NVDAUSDT", "baseAsset": "NVDA", "quoteAsset": "USDT", "status": "TRADING",
     "contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"},
    {"symbol": "BRKBUSDT", "baseAsset": "BRKB", "quoteAsset": "USDT", "status": "TRADING",
     "contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"},
    {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING",
     "contractType": "PERPETUAL", "underlyingType": "COIN"},
]}
# Contract price at each anchor close (the 1m bar that ends there) and now.
ANCHOR = {("SAMSUNGUSDT", "2026-11-06T06:29:00+00:00"): "164.00",
          ("SAMSUNGUSDT", "2026-11-03T06:29:00+00:00"): "160.00",
          ("NVDAUSDT", "2026-11-06T20:59:00+00:00"): "205.00",
          ("BRKBUSDT", "2026-11-06T20:59:00+00:00"): "481.00"}
MARK = {"SAMSUNGUSDT": "167.28", "NVDAUSDT": "209.10", "BRKBUSDT": "481.00"}


class FakeBinance:
    def __init__(self, missing_anchor=False):
        self.calls = []
        self.missing_anchor = missing_anchor

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        self.calls.append(path)
        if path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json=EXCHANGE_INFO)
        if path == "/fapi/v1/klines":
            assert params["interval"] == "1m" and params["limit"] == "1"
            start = int(params["startTime"])
            key = (params["symbol"], datetime.fromtimestamp(start / 1000, UTC).isoformat())
            if self.missing_anchor or key not in ANCHOR:
                return httpx.Response(200, json=[])
            price = ANCHOR[key]
            return httpx.Response(200, json=[[start, price, price, price, price, "10"]])
        if path == "/fapi/v1/premiumIndex":
            return httpx.Response(200, json={"symbol": params["symbol"], "markPrice": MARK[params["symbol"]]})
        return httpx.Response(404)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _estimator(now, closes=CLOSES, binance=None, clock=None):
    binance = binance or FakeBinance()
    http = httpx.AsyncClient(base_url="https://fapi.test", transport=httpx.MockTransport(binance))
    return BinanceEstimator(http=http, daily_closes=lambda t: closes[t], now=lambda: now,
                            clock=clock or Clock()), binance


def test_saturday_samsung_moves_fridays_close_by_the_contract():
    est, _ = _estimator(SATURDAY_KST_10AM)
    result = asyncio.run(est.estimate("005930.KS"))
    assert result.contract == "SAMSUNGUSDT"
    assert result.last_close == 218000.0 and result.last_close_date == "2026-11-06"
    assert result.close_at == "2026-11-06T06:30:00+00:00"          # 15:30 KST
    assert result.contract_price_at_close == 164.0 and result.contract_price == 167.28
    assert result.estimate == 222360.0 and result.change_pct == 2.0
    assert result.market_state == "closed"


def test_us_anchor_is_the_new_york_close_after_daylight_saving_ends():
    est, _ = _estimator(SATURDAY_KST_10AM)
    result = asyncio.run(est.estimate("nvda"))
    assert result.close_at == "2026-11-06T21:00:00+00:00"          # 16:00 EST
    assert result.estimate == 208.28                                # 204.2 x 209.10 / 205
    assert result.change_pct == 2.0


def test_during_the_session_the_anchor_is_the_previous_day_and_the_market_is_open():
    closes = {"005930.KS": [(date(2026, 11, 3), 210000.0), (date(2026, 11, 4), 999999.0)]}
    est, _ = _estimator(WEDNESDAY_KST_10AM, closes)
    result = asyncio.run(est.estimate("005930.KS"))
    assert result.last_close_date == "2026-11-03" and result.market_state == "open"
    assert result.contract_price_at_close == 160.0


def test_a_weekday_holiday_has_no_bar_today_and_reads_closed():
    closes = {"005930.KS": [(date(2026, 11, 3), 210000.0)]}
    est, _ = _estimator(WEDNESDAY_KST_10AM, closes)
    assert asyncio.run(est.estimate("005930.KS")).market_state == "closed"


def test_contracts_cover_us_by_base_asset_and_korea_by_table():
    est, _ = _estimator(SATURDAY_KST_10AM)
    assert asyncio.run(est.contract_for("BRK-B")) == "BRKBUSDT"
    assert asyncio.run(est.contract_for("000660.KS")) == "SKHYNIXUSDT"
    assert asyncio.run(est.contract_for("BTC")) is None              # a coin, not a stock contract
    assert asyncio.run(est.contract_for("AAPL")) is None
    assert asyncio.run(est.estimate("247540.KQ")) is None


def test_estimates_are_cached_and_the_contract_list_is_read_once():
    clock = Clock()
    est, binance = _estimator(SATURDAY_KST_10AM, clock=clock)
    asyncio.run(est.estimate("NVDA"))
    asyncio.run(est.estimate("NVDA"))
    assert binance.calls.count("/fapi/v1/premiumIndex") == 1
    clock.t += 16
    asyncio.run(est.estimate("NVDA"))
    asyncio.run(est.estimate("BRK-B"))
    assert binance.calls.count("/fapi/v1/premiumIndex") == 3
    assert binance.calls.count("/fapi/v1/exchangeInfo") == 1
    assert binance.calls.count("/fapi/v1/klines") == 2                # one anchor per contract and close


def test_no_bar_at_the_anchor_close_is_an_error_not_a_guess():
    est, _ = _estimator(SATURDAY_KST_10AM, binance=FakeBinance(missing_anchor=True))
    with pytest.raises(ValueError):
        asyncio.run(est.estimate("005930.KS"))


def _client(db_url, estimator):
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0)
    client = TestClient(create_app(settings, runner=make_fake_runner(0), estimator=estimator),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client


def test_estimate_endpoint(db_url):
    est, _ = _estimator(SATURDAY_KST_10AM)
    with _client(db_url, est) as client:
        assert client.get("/api/estimate?ticker=NVDA").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/estimate?ticker=005930.ks").json()
        assert body["supported"] is True and body["estimate"] == 222360.0
        assert body["method"] == "binance_perp" and body["fetched_at"] and body["close_at"]
        assert client.get("/api/estimate?ticker=AAPL").json() == {"ticker": "AAPL", "supported": False}
        assert client.get("/api/estimate?ticker=NV%20DA").status_code == 422


def test_estimate_endpoint_is_503_when_the_anchor_is_missing(db_url):
    est, _ = _estimator(SATURDAY_KST_10AM, binance=FakeBinance(missing_anchor=True))
    with _client(db_url, est) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/estimate?ticker=005930.KS").status_code == 503
