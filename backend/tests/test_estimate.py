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
          ("SAMSUNGUSDT", "2026-11-04T06:29:00+00:00"): "164.00",
          ("SAMSUNGUSDT", "2026-11-19T07:29:00+00:00"): "164.00",
          ("NVDAUSDT", "2026-11-27T17:59:00+00:00"): "205.00",
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
        if path == "/fapi/v1/markPriceKlines":
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
    assert binance.calls.count("/fapi/v1/markPriceKlines") == 2                # one anchor per contract and close


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
        assert body["method"] == "binance_perp_mark" and body["fetched_at"] and body["close_at"]
        assert client.get("/api/estimate?ticker=AAPL").json() == {"ticker": "AAPL", "supported": False}
        assert client.get("/api/estimate?ticker=NV%20DA").status_code == 422


def test_estimate_endpoint_is_503_when_the_anchor_is_missing(db_url):
    est, _ = _estimator(SATURDAY_KST_10AM, binance=FakeBinance(missing_anchor=True))
    with _client(db_url, est) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/estimate?ticker=005930.KS").status_code == 503


def test_nan_close_rows_are_not_bars():
    closes = {"005930.KS": [(date(2026, 11, 5), 215000.0), (date(2026, 11, 6), float("nan"))]}
    est, _ = _estimator(SATURDAY_KST_10AM, closes, binance=FakeBinance())
    with pytest.raises(ValueError):            # 11-05 has no anchor in the fake: proves 11-06 was skipped
        asyncio.run(est.estimate("005930.KS"))
    ANCHOR[("SAMSUNGUSDT", "2026-11-05T06:29:00+00:00")] = "160.00"
    try:
        est, _ = _estimator(SATURDAY_KST_10AM, closes)
        result = asyncio.run(est.estimate("005930.KS"))
        assert result.last_close_date == "2026-11-05" and result.last_close == 215000.0
    finally:
        del ANCHOR[("SAMSUNGUSDT", "2026-11-05T06:29:00+00:00")]


def test_right_after_todays_close_the_anchor_is_today_and_the_market_is_closed():
    closes = {"005930.KS": [(date(2026, 11, 3), 210000.0), (date(2026, 11, 4), 218000.0)]}
    est, _ = _estimator(datetime(2026, 11, 4, 6, 45, tzinfo=UTC), closes)     # 15:45 KST
    result = asyncio.run(est.estimate("005930.KS"))
    assert result.last_close_date == "2026-11-04" and result.market_state == "closed"


def test_us_half_day_anchors_on_the_1pm_close():
    closes = {"NVDA": [(date(2026, 11, 27), 204.2)]}
    est, _ = _estimator(datetime(2026, 11, 28, 15, 0, tzinfo=UTC), closes)
    result = asyncio.run(est.estimate("NVDA"))
    assert result.close_at == "2026-11-27T18:00:00+00:00"          # 13:00 EST


def test_csat_day_opens_and_closes_an_hour_late():
    closes = {"005930.KS": [(date(2026, 11, 18), 210000.0), (date(2026, 11, 19), 218000.0)]}
    during = datetime(2026, 11, 19, 6, 45, tzinfo=UTC)                          # 15:45 KST, still trading
    ANCHOR[("SAMSUNGUSDT", "2026-11-18T06:29:00+00:00")] = "160.00"
    try:
        est, _ = _estimator(during, closes)
        result = asyncio.run(est.estimate("005930.KS"))
        assert result.market_state == "open" and result.last_close_date == "2026-11-18"
    finally:
        del ANCHOR[("SAMSUNGUSDT", "2026-11-18T06:29:00+00:00")]
    est, _ = _estimator(datetime(2026, 11, 19, 8, 0, tzinfo=UTC), closes)       # 17:00 KST
    result = asyncio.run(est.estimate("005930.KS"))
    assert result.close_at == "2026-11-19T07:30:00+00:00" and result.market_state == "closed"


def test_a_failure_is_remembered_for_the_cache_window():
    clock = Clock()
    binance = FakeBinance(missing_anchor=True)
    est, _ = _estimator(SATURDAY_KST_10AM, binance=binance, clock=clock)
    for _ in range(3):
        with pytest.raises(ValueError):
            asyncio.run(est.estimate("005930.KS"))
    assert binance.calls.count("/fapi/v1/markPriceKlines") == 1
    clock.t += 16
    with pytest.raises(ValueError):
        asyncio.run(est.estimate("005930.KS"))
    assert binance.calls.count("/fapi/v1/markPriceKlines") == 2


# --- #123: commodities, proxies, leveraged ETFs, estimated bars, the board ---

WIDE_INFO = {"symbols": EXCHANGE_INFO["symbols"] + [
    {"symbol": f"{b}USDT", "baseAsset": b, "quoteAsset": "USDT", "status": "TRADING",
     "contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"} for b in ("QQQ", "SMH", "SOXL")]}
FRI_US_CLOSE = "2026-11-06T20:59:00+00:00"   # 16:00 New York (EST)
FRI_CME_CLOSE = "2026-11-06T21:59:00+00:00"  # 17:00 New York
WIDE_CLOSES = {**CLOSES, "CL=F": [(date(2026, 11, 6), 60.0)], "^NDX": [(date(2026, 11, 6), 21000.0)],
               "SOXS": [(date(2026, 11, 6), 10.0)], "SOXL": [(date(2026, 11, 6), 40.0)]}
WIDE_ANCHOR = {("CLUSDT", FRI_CME_CLOSE): "61.00", ("QQQUSDT", FRI_US_CLOSE): "500.00",
               ("SMHUSDT", FRI_US_CLOSE): "250.00", ("SOXLUSDT", FRI_US_CLOSE): "40.00"}
WIDE_MARK = {"CLUSDT": "62.22", "QQQUSDT": "505.00", "SMHUSDT": "255.00", "SOXLUSDT": "42.00"}


class WideBinance(FakeBinance):
    def __call__(self, request):
        path, params = request.url.path, request.url.params
        if path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json=WIDE_INFO)
        if path == "/fapi/v1/markPriceKlines":
            start = int(params["startTime"])
            if params["interval"] == "1m":
                key = (params["symbol"], datetime.fromtimestamp(start / 1000, UTC).isoformat())
                if key in WIDE_ANCHOR:
                    p = WIDE_ANCHOR[key]
                    return httpx.Response(200, json=[[start, p, p, p, p, "1"]])
                return super().__call__(request)
            step = {"5m": 300_000, "1h": 3_600_000}[params["interval"]]
            # four bars from the anchor: 250, 255 (high 260, low 245), 252.5, 255 on SMH
            prices = [("250", "250", "250", "250"), ("250", "260", "245", "255"),
                      ("255", "255", "252.5", "252.5"), ("252.5", "255", "252.5", "255")]
            return httpx.Response(200, json=[[start + i * step, *p, "0"] for i, p in enumerate(prices)])
        if path == "/fapi/v1/premiumIndex" and params["symbol"] in WIDE_MARK:
            return httpx.Response(200, json={"symbol": params["symbol"], "markPrice": WIDE_MARK[params["symbol"]]})
        return super().__call__(request)


def _wide(now=SATURDAY_KST_10AM):
    return _estimator(now, closes=WIDE_CLOSES, binance=WideBinance())[0]


def test_oil_follows_its_commodity_contract_from_the_cme_close():
    e = asyncio.run(_wide().estimate("CL=F"))
    assert e.contract == "CLUSDT" and e.close_at == "2026-11-06T22:00:00+00:00"
    assert e.estimate == 61.2 and e.change_pct == 2.0 and e.market_state == "closed"


def test_weekend_nasdaq_moves_the_index_with_its_etf():
    e = asyncio.run(_wide().estimate("^NDX"))
    assert (e.method, e.basis, e.contract) == ("proxy", "QQQ", "QQQUSDT")
    assert e.estimate == 21210.0 and e.change_pct == 1.0


def test_an_unlisted_inverse_etf_is_its_base_times_the_multiple():
    e = asyncio.run(_wide().estimate("SOXS"))   # SOXX has no contract: SMH stands in, x -3
    assert (e.method, e.basis, e.multiple, e.contract) == ("leveraged", "SOXX", -3.0, "SMHUSDT")
    assert e.change_pct == -6.0 and e.estimate == 9.4
    listed = asyncio.run(_wide().estimate("SOXL"))   # SOXL has its own contract
    assert listed.method == "binance_perp_mark" and listed.estimate == 42.0


def test_estimated_bars_scale_the_contract_and_merge_ten_minutes():
    est = _wide()
    five = asyncio.run(est.candles("SOXS", "5m"))
    assert five["method"] == "leveraged" and len(five["bars"]) == 4 and all(b["estimated"] for b in five["bars"])
    b = five["bars"][1]   # SMH 250 -> 260 / 245 is +4% / -2%: SOXS x -3 -> 8.8 / 10.6 (high and low swap)
    assert (b["open"], b["high"], b["low"], b["close"]) == (10.0, 10.6, 8.8, 9.4)
    ten = asyncio.run(est.candles("SOXS", "10m"))
    assert len(ten["bars"]) == 2 and ten["bars"][0]["open"] == 10.0 and ten["bars"][0]["close"] == 9.4
    with pytest.raises(ValueError):
        asyncio.run(est.candles("SOXS", "2m"))
    assert asyncio.run(est.candles("AAPL", "5m")) is None


def test_futures_hours():
    from desk.estimate import futures_open

    assert not futures_open(SATURDAY_KST_10AM)
    assert futures_open(datetime(2026, 11, 4, 15, 0, tzinfo=UTC))         # Wednesday 10:00 NY
    assert not futures_open(datetime(2026, 11, 4, 22, 30, tzinfo=UTC))    # the 17:00 break
    assert futures_open(datetime(2026, 11, 8, 23, 30, tzinfo=UTC))        # Sunday 18:30 NY


def test_board_endpoint_mixes_estimates_and_unavailable(db_url):
    est = _wide()
    with _client(db_url, est) as client:
        assert client.get("/api/estimates?tickers=NVDA").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/estimates?tickers=CL=F,soxs,AAPL,soxs").json()["items"]
        assert [r["ticker"] for r in body] == ["CL=F", "SOXS", "AAPL"]
        oil, soxs, aapl = body
        assert oil["state"] == "estimate" and oil["price"] == 61.2 and oil["source"]["contract"] == "CLUSDT"
        assert soxs["source"] == {"kind": "leveraged", "contract": "SMHUSDT", "basis": "SOXX", "multiple": -3.0}
        assert aapl["state"] == "unavailable" and aapl["price"] is None
        assert client.get("/api/estimates?tickers=" + ",".join(f"T{i}" for i in range(31))).status_code == 422
        bars = client.get("/api/estimate/candles?ticker=SOXS&interval=10m").json()
        assert len(bars["bars"]) == 2 and bars["close"] == 10.0
        assert client.get("/api/estimate/candles?ticker=SOXS&interval=3m").status_code == 422
        assert client.get("/api/estimate/candles?ticker=AAPL").json() == {"ticker": "AAPL", "supported": False}
