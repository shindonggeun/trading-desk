import asyncio
import time
from datetime import date, timedelta

from desk.broker.base import Fill
from desk.review import _realize, build_review, first_touch, ticker_for
from tests.test_api import OWNER, PASSWORD, _client, _wait_done


def _fill(oid, symbol, side, qty, price, day, fee=0.0, currency="USD"):
    return Fill(oid, symbol, side, qty, price, fee, currency, f"{day}T10:00:00+09:00")


def _bars(rows):
    return [{"at": f"{d}T00:00:00+00:00", "open": 0, "high": h, "low": lo, "close": 0, "volume": 0} for d, h, lo in rows]


def test_tickers_follow_the_listing_market_and_fall_back_sensibly():
    assert ticker_for("247540", "KOSDAQ") == "247540.KQ"
    assert ticker_for("005930", None) == "005930.KS"
    assert ticker_for("BRK.B", None) == "BRK-B"
    assert ticker_for("NVDA", "NASDAQ") == "NVDA"


def test_first_touch_reads_bars_after_the_fill_day_only():
    bars = _bars([("2026-10-01", 250, 100),           # the fill day itself: ignored
                  ("2026-10-02", 205, 195),
                  ("2026-10-03", 212, 199)])
    fill_day = date(2026, 10, 1)
    assert first_touch(bars, fill_day, stop=190, target=210, long=True) == {"result": "target", "on": "2026-10-03"}
    assert first_touch(bars, fill_day, stop=196, target=210, long=True) == {"result": "stop", "on": "2026-10-02"}
    assert first_touch(bars, fill_day, stop=190, target=230, long=True) == {"result": "open", "on": None}
    assert first_touch(bars, fill_day, stop=196, target=204, long=True)["result"] == "both"
    # short: stop above, target below
    assert first_touch(bars, fill_day, stop=211, target=196, long=False) == {"result": "target", "on": "2026-10-02"}


def test_realized_pnl_is_fifo_with_fees_and_unknown_cost_stays_unmatched():
    fills = [_fill("b1", "NVDA", "BUY", 10, 100, "2026-09-01", fee=10),
             _fill("b2", "NVDA", "BUY", 10, 120, "2026-09-02"),
             _fill("s1", "NVDA", "SELL", 15, 130, "2026-09-03", fee=15),
             _fill("s2", "AAPL", "SELL", 3, 200, "2026-09-04")]           # bought before the window
    out = _realize(fills)
    # 10 @ (100 + 1 fee) and 5 @ 120, sold @ 130 - 1 fee each
    assert out["s1"]["matched_quantity"] == 15 and out["s1"]["unmatched_quantity"] == 0
    assert out["s1"]["realized_pnl"] == (10 * (129 - 101) + 5 * (129 - 120))
    assert out["s1"]["realized_rate"] == round(325 / 1610 * 100, 2)
    assert out["s2"] == {"matched_quantity": 0.0, "unmatched_quantity": 3, "realized_pnl": None, "realized_rate": None}


def test_review_links_each_fill_to_the_latest_earlier_plan():
    plans = [
        {"id": "r2", "ticker": "NVDA", "trade_date": "2026-10-05", "options": {"name": "엔비디아"},
         "plan": {"rating": "Overweight", "bias": "long", "entry_price": 180.0, "stop_loss": 170.0, "price_target": 200.0}},
        {"id": "r1", "ticker": "NVDA", "trade_date": "2026-09-01", "options": {},
         "plan": {"rating": "Sell", "bias": "short", "entry_price": 190.0, "stop_loss": 200.0, "price_target": 170.0}},
    ]
    fills = [_fill("f1", "NVDA", "BUY", 2, 183.6, "2026-10-06"),     # after r2 (long): with plan
             _fill("f2", "NVDA", "BUY", 1, 175.0, "2026-09-03"),     # after r1 (short): against plan
             _fill("f3", "NVDA", "BUY", 1, 175.0, "2026-08-01"),     # before any plan
             _fill("f4", "005930", "BUY", 5, 70000, "2026-10-06", currency="KRW")]   # never analysed
    seen = {}

    async def candles(ticker, count):
        seen[ticker] = count
        return _bars([("2026-10-07", 201, 179)])

    result = asyncio.run(build_review(fills, lambda tickers, since: [p for p in plans if p["ticker"] in tickers],
                                      lambda symbol: {"name": "엔비디아", "market": "NASDAQ"} if symbol == "NVDA" else None,
                                      candles, date(2026, 7, 15), date(2026, 10, 10)))
    rows = {r["order_id"]: r for r in result["fills"]}
    assert [r["order_id"] for r in result["fills"]] == ["f1", "f4", "f2", "f3"]     # newest first
    assert rows["f1"]["plan"]["run_id"] == "r2" and rows["f1"]["alignment"] == "with_plan"
    assert rows["f1"]["entry_gap_pct"] == 2.0 and rows["f1"]["outcome"] == {"result": "target", "on": "2026-10-07"}
    assert rows["f2"]["plan"]["run_id"] == "r1" and rows["f2"]["alignment"] == "against_plan"
    assert rows["f2"]["outcome"] is None                                         # only plan-following fills
    assert rows["f3"]["plan"] is None and rows["f3"]["alignment"] == "no_plan"
    assert rows["f4"]["ticker"] == "005930.KS" and rows["f4"]["alignment"] == "no_plan"
    assert seen == {"NVDA": 200}                                                 # one request per instrument
    assert result["summary"]["with_plan"] == 1 and result["summary"]["no_plan"] == 2
    assert result["summary"]["outcomes"]["target"] == 1


def test_review_route_needs_login_and_reads_the_mock_book(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/review").status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        # A plan the day before the sample NVDA buy (40 days ago), so the buy lines up with it.
        day = (date.today() - timedelta(days=41)).isoformat()
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": day}).json()
        assert _wait_done(client, run["id"])["status"] == "done"
        body = client.get("/api/review?days=90").json()
        assert body["supported"] is True and body["source"] == "mock"
        assert body["summary"]["fills"] == 4
        buy = next(r for r in body["fills"] if r["order_id"] == "m-1")
        assert buy["plan"]["run_id"] == run["id"] and buy["alignment"] == "with_plan"
        assert buy["outcome"] is None and any("NVDA" in e for e in body["errors"])   # offline prices: said so
        sell = next(r for r in body["fills"] if r["order_id"] == "m-3")
        assert sell["realized"]["matched_quantity"] == 5
        t0 = time.monotonic()
        assert client.get("/api/review?days=90").json() == body                         # cached
        assert time.monotonic() - t0 < 1
        assert client.get("/api/review?days=7").json()["summary"]["fills"] == 1         # only the recent sell



def test_any_days_reuses_one_broker_fetch_and_a_failure_hides_the_body(db_url, monkeypatch):
    from desk.broker.mock import MockBroker

    calls = []
    real = MockBroker.fills

    async def counting(self, start, end):
        calls.append((start, end))
        return await real(self, start, end)

    monkeypatch.setattr(MockBroker, "fills", counting)
    with _client(db_url) as client:
        for days in (90, 7, 180, 33, 90):
            assert client.get(f"/api/review?days={days}").status_code == 200
        assert len(calls) == 1 and (calls[0][1] - calls[0][0]).days == 180           # widest window, once

    async def failing(self, start, end):
        calls.append("fail")
        raise RuntimeError("Toss GET /api/v1/orders failed 403: {\"secret\": \"response body\"}")

    monkeypatch.setattr(MockBroker, "fills", failing)
    calls.clear()
    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.runner import make_fake_runner
    from desk.settings import Settings

    fresh = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0))
    with TestClient(fresh, base_url="https://testserver") as client:                 # new app, empty cache
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        first = client.get("/api/review").json()
        assert first["errors"] == ["RuntimeError: Toss GET /api/v1/orders failed 403"]  # no body
        client.get("/api/review?days=30")
        assert calls == ["fail"]                                                     # failure cached too
