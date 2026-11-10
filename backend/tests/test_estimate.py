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
          ("BRKBUSDT", "2026-11-06T20:59:00+00:00"): "481.00",
          ("SAMSUNGUSDT", "2026-11-06T10:59:00+00:00"): "166.00"}      # 20:00 KST, the NXT close (#204)
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
        if path == "/fapi/v1/premiumIndex":   # no symbol: every contract's mark in one list
            return httpx.Response(200, json=[{"symbol": k, "markPrice": v} for k, v in MARK.items()])
        return httpx.Response(404)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _estimator(now, closes=CLOSES, binance=None, clock=None, after_hours=None):
    binance = binance or FakeBinance()
    http = httpx.AsyncClient(base_url="https://fapi.test", transport=httpx.MockTransport(binance))
    return BinanceEstimator(http=http, daily_closes=lambda t: closes[t], now=lambda: now,
                            clock=clock or Clock(), after_hours=after_hours), binance


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
    assert binance.calls.count("/fapi/v1/premiumIndex") == 2   # BRK-B shares the marks NVDA just read (#123)
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
        if path == "/fapi/v1/premiumIndex":
            return httpx.Response(200, json=[{"symbol": k, "markPrice": v} for k, v in {**MARK, **WIDE_MARK}.items()])
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


def test_yahoo_gaps_are_filled_from_the_hourly_bars(monkeypatch):
    import sys
    import types

    import pandas as pd

    from desk import estimate

    ny = "America/New_York"
    daily = pd.DataFrame({"Close": [200.0, float("nan")]},
                         index=pd.DatetimeIndex(["2026-11-04", "2026-11-05"]).tz_localize(ny))
    hourly = pd.DataFrame({"Close": [183.1, 181.57, 182.4]},
                          index=pd.DatetimeIndex(["2026-11-05 14:30", "2026-11-05 15:30", "2026-11-05 16:30"]).tz_localize(ny))

    class Ticker:
        def __init__(self, t):
            pass

        def history(self, period, interval, auto_adjust):
            return daily if interval == "1d" else hourly

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    estimate._CLOSES.clear()
    rows = estimate.yahoo_daily_closes("NVDA")
    assert rows == [(date(2026, 11, 4), 200.0), (date(2026, 11, 5), 181.57)]   # the 15:30 bar, not the after-hours one
    estimate._CLOSES.clear()


def test_futures_closes_all_come_from_the_hourly_bar_before_17h(monkeypatch):
    import sys
    import types

    import pandas as pd

    from desk import estimate

    ny = "America/New_York"
    daily = pd.DataFrame({"Close": [88.0, 91.5]},   # Yahoo's settlement-ish daily closes
                         index=pd.DatetimeIndex(["2026-11-04", "2026-11-05"]).tz_localize(ny))
    hourly = pd.DataFrame({"Close": [88.12, 90.0, 91.2, 91.3]},
                          index=pd.DatetimeIndex(["2026-11-04 16:00", "2026-11-05 15:00", "2026-11-05 16:00",
                                                  "2026-11-05 18:00"]).tz_localize(ny))

    class Ticker:
        def __init__(self, t):
            pass

        def history(self, period, interval, auto_adjust):
            return daily if interval == "1d" else hourly

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    estimate._CLOSES.clear()
    assert estimate.yahoo_daily_closes("CL=F") == [(date(2026, 11, 4), 88.12), (date(2026, 11, 5), 91.2)]
    estimate._CLOSES.clear()


def test_long_closes_page_past_the_bar_limit_and_leverage_never_goes_negative(monkeypatch):
    from desk import estimate

    monkeypatch.setattr(estimate, "CANDLE_LIMIT", 3)

    class Paged(WideBinance):
        def __call__(self, request):
            params = request.url.params
            if request.url.path == "/fapi/v1/markPriceKlines" and params["interval"] == "5m":
                start = int(params["startTime"])
                n = 3 if start < 1_800_000_000_000 and not hasattr(self, "_paged") else 1
                self._paged = True
                return httpx.Response(200, json=[[start + i * 300_000, "250", "250", "100", "250", "0"] for i in range(n)])
            return super().__call__(request)

    est, _ = _estimator(SATURDAY_KST_10AM, closes=WIDE_CLOSES, binance=Paged())
    bars = asyncio.run(est.candles("SOXS", "5m"))["bars"]
    assert len(bars) == 4                                  # 3 + the second page's 1
    assert all(b["low"] > 0 for b in bars)


def test_a_leveraged_estimate_floors_above_zero(monkeypatch):
    monkeypatch.setitem(WIDE_MARK, "SMHUSDT", "400.00")    # SMH +60% -> SOXS x -3 = -180%
    e = asyncio.run(_wide().estimate("SOXS"))
    assert e.change_pct == -180.0 and e.estimate == 0.1     # 1% of the 10.0 close, never negative


def test_board_live_path_and_one_failure(db_url, monkeypatch):
    from desk import live
    from desk.prices import Quote

    est = _wide()
    real = est.estimate

    async def flaky(t):
        if t == "NVDA":
            raise RuntimeError("Binance down")
        return await real(t)

    monkeypatch.setattr(est, "estimate", flaky)

    async def is_open(_cal, ticker, now=None):
        return ticker == "NVDA"

    monkeypatch.setattr(live, "is_open", is_open)
    with _client(db_url, est) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        from desk import api as api_mod  # noqa: F401 - the app holds its own PriceSource

        async def quote(self, t):
            return Quote(t, 190.0, "USD", "toss", "2026-11-07T01:00:00+00:00")

        monkeypatch.setattr("desk.prices.PriceSource.quote", quote)
        rows = {r["ticker"]: r for r in client.get("/api/estimates?tickers=NVDA,SOXS").json()["items"]}
    assert rows["NVDA"]["state"] == "live" and rows["NVDA"]["price"] == 190.0 and rows["NVDA"]["change_pct"] is None
    assert rows["SOXS"]["state"] == "estimate"


# --- #204: the NXT after-market trade is the anchor when there was one after the close -------------

NXT_FRIDAY_20H = (221000.0, datetime(2026, 11, 6, 11, 0, 0, tzinfo=UTC), False)   # 20:00 KST, NXT closed


def test_a_korean_estimate_moves_the_nxt_after_market_close_not_the_krx_close():
    est, _ = _estimator(SATURDAY_KST_10AM, after_hours=lambda code: NXT_FRIDAY_20H if code == "005930" else None)
    r = asyncio.run(est.estimate("005930.KS"))
    assert r.anchor_source == "nxt" and r.anchor_price == 221000.0 and r.anchor_at == "2026-11-06T11:00:00+00:00"
    assert r.estimate == round(221000 * 167.28 / 166.0)                  # only the contract's move since 20:00
    assert r.contract_move_pct == round((167.28 / 166.0 - 1) * 100, 2)
    assert r.change_pct == round((r.estimate / 218000 - 1) * 100, 2)      # still against the KRX close
    assert r.last_close == 218000.0 and r.contract_price_at_close == 164.0


def test_no_nxt_trade_or_an_old_one_keeps_the_krx_close():
    for source in (lambda code: None,
                   lambda code: (219000.0, datetime(2026, 11, 5, 11, 0, tzinfo=UTC))):   # Thursday's: before the close
        est, _ = _estimator(SATURDAY_KST_10AM, after_hours=source)
        r = asyncio.run(est.estimate("005930.KS"))
        assert r.anchor_source == "close" and r.estimate == 222360.0 and r.change_pct == 2.0
        assert r.contract_move_pct == 2.0


def test_us_tickers_never_ask_for_an_nxt_trade():
    asked = []
    est, _ = _estimator(datetime(2026, 11, 7, 15, 0, tzinfo=UTC), after_hours=lambda code: asked.append(code))
    r = asyncio.run(est.estimate("NVDA"))
    assert r.anchor_source == "close" and asked == []


def test_naver_after_market_quote_is_read_and_anything_else_is_none(monkeypatch):
    from desk import estimate as mod

    def reply(body):
        return lambda url, **kw: httpx.Response(200, json=body, request=httpx.Request("GET", url))
    monkeypatch.setattr(mod.httpx, "get", reply({"overMarketPriceInfo": {
        "tradingSessionType": "AFTER_MARKET", "overPrice": "193,400", "localTradedAt": "2026-11-06T20:00:00+09:00"}}))
    assert mod.naver_after_hours("005930") == (193400.0, datetime(2026, 11, 6, 11, 0, tzinfo=UTC), False)
    monkeypatch.setattr(mod.httpx, "get", reply({"overMarketPriceInfo": {
        "tradingSessionType": "PRE_MARKET", "overMarketStatus": "OPEN", "overPrice": "190,000",
        "localTradedAt": "2026-11-09T08:10:00+09:00"}}))
    assert mod.naver_after_hours("005930") == (190000.0, datetime(2026, 11, 8, 23, 10, tzinfo=UTC), True)
    monkeypatch.setattr(mod.httpx, "get", reply({"overMarketPriceInfo": {"tradingSessionType": "REGULAR",
                                                                        "overPrice": "190,000"}}))
    assert mod.naver_after_hours("005930") is None
    monkeypatch.setattr(mod.httpx, "get", reply({}))
    assert mod.naver_after_hours("005930") is None

    def boom(url, **kw):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(mod.httpx, "get", boom)
    assert mod.naver_after_hours("005930") is None


def test_estimated_bars_after_the_nxt_trade_move_from_its_price():
    close_ms = int(datetime(2026, 11, 6, 6, 30, tzinfo=UTC).timestamp() * 1000)
    nxt_ms = int(NXT_FRIDAY_20H[1].timestamp() * 1000)

    def binance(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if request.url.path == "/fapi/v1/markPriceKlines" and params["interval"] == "1m":
            start = int(params["startTime"])
            price = {close_ms - 60_000: "164.00", nxt_ms - 60_000: "166.00"}.get(start)
            return httpx.Response(200, json=[[start, price, price, price, price, "1"]] if price else [])
        if request.url.path == "/fapi/v1/markPriceKlines":          # the 5-minute bars: one before, one after 20:00
            return httpx.Response(200, json=[[nxt_ms - 300_000, "165.0", "165.0", "165.0", "165.0", "1"],
                                             [nxt_ms, "167.0", "167.0", "167.0", "167.0", "1"]])
        return httpx.Response(404)

    est, _ = _estimator(SATURDAY_KST_10AM, binance=binance, after_hours=lambda code: NXT_FRIDAY_20H)
    got = asyncio.run(est.candles("005930.KS", "5m"))
    before, after = got["bars"]
    assert before["close"] == round(218000 * 165 / 164)               # still from the KRX close
    assert after["close"] == round(221000 * 167 / 166)                # from the NXT trade
    assert got["anchor_source"] == "nxt" and got["anchor_price"] == 221000.0


# --- #209: while NXT trades, its last trade is the price; after 20:00 the estimate takes over -------

def test_while_nxt_trades_the_price_is_its_last_trade_not_an_estimate():
    friday_17h = datetime(2026, 11, 6, 8, 0, tzinfo=UTC)                  # 17:00 KST, NXT after-market open
    trade = (220000.0, datetime(2026, 11, 6, 7, 59, 30, tzinfo=UTC), True)
    ANCHOR[("SAMSUNGUSDT", "2026-11-06T07:58:00+00:00")] = "165.00"
    try:
        est, _ = _estimator(friday_17h, after_hours=lambda code: trade)
        r = asyncio.run(est.estimate("005930.KS"))
    finally:
        del ANCHOR[("SAMSUNGUSDT", "2026-11-06T07:58:00+00:00")]
    assert r.market_state == "nxt" and r.estimate == 220000.0 and r.anchor_source == "nxt"
    assert r.change_pct == round((220000 / 218000 - 1) * 100, 2)


def test_after_20h_the_estimate_moves_the_nxt_close_and_the_state_is_closed():
    est, _ = _estimator(SATURDAY_KST_10AM, after_hours=lambda code: NXT_FRIDAY_20H)
    r = asyncio.run(est.estimate("005930.KS"))
    assert r.market_state == "closed" and r.estimate == round(221000 * 167.28 / 166.0)


def test_the_board_shows_an_open_nxt_trade_as_a_live_price(db_url):
    friday_17h = datetime(2026, 11, 6, 8, 0, tzinfo=UTC)
    ANCHOR[("SAMSUNGUSDT", "2026-11-06T07:58:00+00:00")] = "165.00"
    try:
        est, _ = _estimator(friday_17h, after_hours=lambda code: (
            220000.0, datetime(2026, 11, 6, 7, 59, 30, tzinfo=UTC), True))
        with _client(db_url, est) as client:
            client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
            row = client.get("/api/estimates?tickers=005930.KS").json()["items"][0]
    finally:
        del ANCHOR[("SAMSUNGUSDT", "2026-11-06T07:58:00+00:00")]
    assert row["state"] == "live" and row["quote_source"] == "nxt" and row["price"] == 220000.0


# --- #238: US pre / after market and the overnight session are real trades on KIS ----------------

class _FakeKis:
    """The KIS stream as the board sees it (#238/#257): trades by ticker with their age."""

    def __init__(self):
        self.connected = True
        self.trades: dict = {}            # ticker -> (price, age seconds)
        self.subs: set = set()
        self.on_want = None               # called with the ticker on its first want (a trade arriving)

    def want(self, ticker):
        if ticker not in self.subs:
            self.subs.add(ticker)
            if self.on_want:
                self.on_want(ticker)

    def subscribed(self, ticker):
        return ticker in self.subs

    def latest(self, ticker, max_age=60.0):
        from desk.broker.kis_stream import Tick
        hit = self.trades.get(ticker)
        if not hit or ticker not in self.subs or hit[1] > max_age:
            return None
        return Tick(hit[0], None, "2026-11-09T07:49:00+00:00")

    async def run(self):
        import asyncio
        await asyncio.Event().wait()

    async def aclose(self):
        pass

    def status(self):
        return {}


def test_off_hours_us_trades_from_kis_are_live_and_without_one_the_estimate_stays(db_url, monkeypatch):
    import threading

    from desk import live
    from desk.broker import kis_stream

    est = _wide()

    async def closed(_cal, ticker, now=None):
        return False

    monkeypatch.setattr(live, "is_open", closed)
    monkeypatch.setattr(kis_stream, "us_session", lambda now=None: "overnight")
    fake = _FakeKis()
    monkeypatch.setattr(kis_stream, "make", lambda settings: fake)
    # #257: the first trade of a new subscription arrives a moment after the board asks for it
    fake.on_want = lambda t: threading.Timer(0.3, lambda: fake.trades.setdefault(t, (12.0, 0))).start()         if t == "SOXS" else None
    with _client(db_url, est) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        rows = {r["ticker"]: r for r in client.get("/api/estimates?tickers=SOXS,CL=F").json()["items"]}
        soxs = rows["SOXS"]
        assert (soxs["state"], soxs["price"], soxs["quote_source"], soxs["session"]) == ("live", 12.0, "kis", "overnight")
        assert soxs["change_pct"] == round((12.0 / 10.0 - 1) * 100, 2)
        assert rows["CL=F"]["state"] == "estimate"            # not a US stock: no KIS stream
        fake.trades["SOXS"] = (12.5, 300)                     # a thin ETF: last trade 5 minutes ago
        assert client.get("/api/estimates?tickers=SOXS").json()["items"][0]["price"] == 12.5
        fake.trades["SOXS"] = (12.5, 900)                     # 15 minutes: the estimate again
        assert client.get("/api/estimates?tickers=SOXS").json()["items"][0]["state"] == "estimate"
        monkeypatch.setattr(kis_stream, "us_session", lambda now=None: None)      # weekend
        fake.trades["SOXS"] = (12.5, 0)
        assert client.get("/api/estimates?tickers=SOXS").json()["items"][0]["state"] == "estimate"


def test_a_quiet_subscription_does_not_hold_the_board(db_url, monkeypatch):
    import time

    from desk import live
    from desk.broker import kis_stream

    async def closed(_cal, ticker, now=None):
        return False

    monkeypatch.setattr(live, "is_open", closed)
    monkeypatch.setattr(kis_stream, "us_session", lambda now=None: "overnight")
    fake = _FakeKis()
    fake.subs.add("SOXS")                                     # subscribed already, no trade lately
    monkeypatch.setattr(kis_stream, "make", lambda settings: fake)
    with _client(db_url, _wide()) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        started = time.monotonic()
        row = client.get("/api/estimates?tickers=SOXS").json()["items"][0]
        assert row["state"] == "estimate" and time.monotonic() - started < 1.5


def test_a_ticker_that_never_subscribes_is_waited_for_once_not_every_poll(db_url, monkeypatch):
    """#257 review: a stream that is down (or a refused ticker) must not slow every board poll."""
    import time

    from desk import live
    from desk.broker import kis_stream

    async def closed(_cal, ticker, now=None):
        return False

    monkeypatch.setattr(live, "is_open", closed)
    monkeypatch.setattr(kis_stream, "us_session", lambda now=None: "overnight")
    fake = _FakeKis()
    fake.connected = False
    fake.subscribed = lambda t: False                        # never gets on the socket
    monkeypatch.setattr(kis_stream, "make", lambda settings: fake)
    with _client(db_url, _wide()) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        started = time.monotonic()
        assert client.get("/api/estimates?tickers=SOXS").json()["items"][0]["state"] == "estimate"
        first = time.monotonic() - started
        started = time.monotonic()
        assert client.get("/api/estimates?tickers=SOXS").json()["items"][0]["state"] == "estimate"
        again = time.monotonic() - started
    assert first >= 1.5 and again < 1.0
