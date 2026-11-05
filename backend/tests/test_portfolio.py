import asyncio
import json
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from desk.api import create_app
from desk.auth import COOKIE, Auth
from desk.broker import Holding, MockBroker
from desk.db import make_engine
from desk.portfolio import FX_TTL_SEC, HOLDINGS_TTL_SEC, SNAPSHOT_TTL_SEC, Portfolio, totals, value_row
from desk.prices import QUOTE_TTL_SEC, PriceSource
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import Clock, _toss

NVDA = Holding("NVDA", "NVDA", "NVIDIA", "US", "USD", 12, 168.4, 181.2)
SAMSUNG = Holding("005930", "005930.KS", "삼성전자", "KR", "KRW", 40, 71200, 74800)


class Book(MockBroker):
    """Mock holdings plus switchable live prices and rate."""

    name = "toss"

    def __init__(self, prices=None, fx=1400.0):
        self.prices = dict(prices or {"NVDA": 190.0, "005930": 75000.0})
        self.fx, self.fail_holdings, self.calls = fx, False, {"holdings": 0, "fx": 0}

    async def holdings(self):
        self.calls["holdings"] += 1
        if self.fail_holdings:
            raise ConnectionError("403 not an allowed IP")
        return [NVDA, SAMSUNG]

    async def quote(self, symbol):
        price = self.prices.get(symbol)
        if price is None:
            raise ConnectionError(f"no price for {symbol}")
        return price, None

    async def usd_krw(self):
        self.calls["fx"] += 1
        if self.fx is None:
            raise ConnectionError("rate down")
        return self.fx


def _no_yahoo(*_):
    raise ConnectionError("yahoo down")


def _book(broker, clock=None, yahoo_fx=_no_yahoo):
    clock = clock or Clock()
    prices = PriceSource(broker, yahoo_quote=_no_yahoo, clock=clock)
    return Portfolio(broker, prices, yahoo_fx=yahoo_fx, clock=clock), clock


def test_us_holding_converts_at_the_rate_in_the_response():
    row = value_row(NVDA, 190.0, 1400.0)
    assert row["value"] == {"krw": 3_192_000.0, "usd": 2280.0}             # 12 x 190 x 1400
    assert row["cost"] == {"krw": round(12 * 168.4 * 1400), "usd": 2020.8}
    assert row["pnl"]["usd"] == 259.2 and row["pnl_rate"] == 12.83
    kr = value_row(SAMSUNG, 75000.0, 1400.0)
    assert kr["value"] == {"krw": 3_000_000.0, "usd": round(3_000_000 / 1400, 2)}
    assert kr["pnl"]["krw"] == 152_000.0 and kr["pnl_rate"] == 5.34


def test_totals_are_the_sum_of_the_rows():
    snap = asyncio.run(_book(Book())[0].snapshot())
    rows = snap["holdings"]
    total = snap["total"]
    assert total["krw"] == sum(r["value"]["krw"] for r in rows)
    assert total["usd"] == round(sum(r["value"]["usd"] for r in rows), 2)
    assert total["pnl_krw"] == sum(r["pnl"]["krw"] for r in rows)
    cost_krw = sum(r["cost"]["krw"] for r in rows)
    assert total["pnl_rate"] == round(total["pnl_krw"] / cost_krw * 100, 2)
    assert snap["fx"]["usd_krw"] == 1400.0 and snap["fx"]["source"] == "toss"
    assert [r["symbol"] for r in snap["groups"]["US"]] == ["NVDA"]
    assert [r["symbol"] for r in snap["groups"]["KR"]] == ["005930"]
    nvda = snap["groups"]["US"][0]
    assert nvda["last_price"] == 190.0 and nvda["price_source"] == "toss"   # live price, not Holding.last_price
    assert nvda["value"]["krw"] == round(nvda["value"]["usd"] * snap["fx"]["usd_krw"])
    assert snap["error"] is None


def test_without_any_rate_krw_totals_are_left_empty():
    assert totals([value_row(NVDA, 190.0, None), value_row(SAMSUNG, 75000.0, None)])["krw"] is None


def test_failed_price_and_rate_keep_the_last_values_and_say_so():
    broker = Book()
    book, clock = _book(broker)
    first = asyncio.run(book.snapshot())
    broker.prices.pop("NVDA")
    broker.fx = None
    clock.t += max(QUOTE_TTL_SEC, FX_TTL_SEC) + 1
    second = asyncio.run(book.snapshot())
    assert second["fx"] == first["fx"]
    assert second["groups"]["US"][0]["last_price"] == 190.0
    assert second["total"]["krw"] == first["total"]["krw"]
    assert "price NVDA" in second["error"] and "fx" in second["error"]


def test_rate_falls_back_to_yahoo():
    broker = Book(fx=None)
    book, _ = _book(broker, yahoo_fx=lambda: 1390.25)
    snap = asyncio.run(book.snapshot())
    assert snap["fx"]["usd_krw"] == 1390.25 and snap["fx"]["source"] == "yahoo" and snap["error"] is None


def test_holdings_outage_keeps_the_last_book():
    broker = Book()
    book, clock = _book(broker)
    first = asyncio.run(book.snapshot())
    broker.fail_holdings = True
    clock.t += HOLDINGS_TTL_SEC + 1
    second = asyncio.run(book.snapshot())
    assert len(second["holdings"]) == 2 and second["total"]["krw"] == first["total"]["krw"]
    assert second["error"].startswith("holdings:")


def test_streams_share_cached_calls():
    broker = Book()
    book, clock = _book(broker)

    async def many():
        await asyncio.gather(*(book.snapshot() for _ in range(5)))
    asyncio.run(many())
    assert broker.calls == {"holdings": 1, "fx": 1}
    clock.t += SNAPSHOT_TTL_SEC + 0.1      # new snapshot, but holdings and rate are still fresh
    asyncio.run(book.snapshot())
    assert broker.calls == {"holdings": 1, "fx": 1}


def test_toss_rate_uses_the_mid_rate():
    def handler(request):
        assert request.url.path == "/api/v1/exchange-rate"
        assert dict(request.url.params) == {"baseCurrency": "USD", "quoteCurrency": "KRW"}
        return httpx.Response(200, json={"result": {
            "baseCurrency": "USD", "quoteCurrency": "KRW", "rate": "1380.5", "midRate": "1375",
            "basisPoint": "40", "rateChangeType": "UP",
            "validFrom": "2026-03-25T09:30:00+09:00", "validUntil": "2026-03-25T09:31:00+09:00"}})
    assert asyncio.run(_toss(handler).usd_krw()) == 1375.0


# --- API ------------------------------------------------------------------------------

def _client(db_url, broker, **overrides) -> TestClient:
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0, **overrides)
    book, _ = _book(broker)
    client = TestClient(create_app(settings, runner=make_fake_runner(0), book=book),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client


def test_portfolio_needs_a_session(db_url):
    with _client(db_url, Book()) as client:
        assert client.get("/api/portfolio").status_code == 401
        assert client.get("/api/portfolio/stream").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/portfolio").json()
        assert set(body) >= {"broker", "fx", "total", "groups", "holdings", "error"}
        assert body["total"]["krw"] > 0


def test_portfolio_stream_sends_snapshots_until_the_session_ends(db_url, monkeypatch):
    with _client(db_url, Book(), portfolio_stream_sec=0) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        token = client.cookies.get(COOKIE)
        rounds = 0
        real_sleep = asyncio.sleep

        async def sleep(_seconds):
            nonlocal rounds
            rounds += 1
            if rounds == 2:     # sign out from elsewhere after the second snapshot
                engine = make_engine(db_url)
                Auth(engine, timedelta(hours=1), timedelta(hours=1)).close_session(token)
                engine.dispose()
            await real_sleep(0)
        monkeypatch.setattr("desk.api.asyncio.sleep", sleep)

        with client.stream("GET", "/api/portfolio/stream") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = list(response.iter_lines())
        snapshots = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
        assert len(snapshots) == 2      # the stream closed by itself once the session was gone
        assert lines[0] == "retry: 2000"   # 0 s in settings is clamped to 2 s
        assert snapshots[0]["fx"]["usd_krw"] == 1400.0
