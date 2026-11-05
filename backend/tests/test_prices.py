import asyncio
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.broker import Bar, MockBroker, TossBroker
from desk.levels import fill_levels
from desk.prices import QUOTE_TTL_SEC, PriceSource, broker_symbol
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _wait_done


class LiveBroker(MockBroker):
    name = "toss"

    def __init__(self, price=181.5, fail=False):
        self.price, self.fail, self.calls = price, fail, 0

    async def quote(self, symbol):
        self.calls += 1
        if self.fail:
            raise ConnectionError("403 not an allowed IP")
        return self.price, "USD"

    async def candles(self, symbol, interval, count):
        return [Bar("2026-11-04T14:30:00+00:00", 1, 2, 0.5, 1.5, 100)]


def _yahoo(price=180.0):
    calls = []

    def quote(ticker):
        calls.append(ticker)
        return price, "USD"
    return quote, calls


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_broker_symbols_drop_the_yahoo_suffix():
    assert broker_symbol("005930.KS") == "005930"
    assert broker_symbol("247540.kq") == "247540"
    assert broker_symbol("BRK-B") == "BRK.B"
    assert broker_symbol("NVDA") == "NVDA"


def test_toss_quote_is_cached_for_a_few_seconds():
    broker, clock = LiveBroker(), Clock()
    yahoo, yahoo_calls = _yahoo()
    source = PriceSource(broker, yahoo_quote=yahoo, clock=clock)
    first = asyncio.run(source.quote("NVDA"))
    assert (first.price, first.currency, first.source) == (181.5, "USD", "toss")
    asyncio.run(source.quote("NVDA"))
    assert broker.calls == 1
    clock.t += QUOTE_TTL_SEC + 1
    asyncio.run(source.quote("NVDA"))
    assert broker.calls == 2 and yahoo_calls == []


@pytest.mark.parametrize("broker", [MockBroker(), LiveBroker(fail=True)], ids=["no-market-data", "toss-fails"])
def test_falls_back_to_yahoo_and_says_so(broker):
    yahoo, calls = _yahoo(179.0)
    quote = asyncio.run(PriceSource(broker, yahoo_quote=yahoo).quote("005930.KS"))
    assert (quote.price, quote.source) == (179.0, "yahoo")
    assert calls == ["005930.KS"]


def test_candles_prefer_the_broker_and_reject_odd_intervals():
    source = PriceSource(LiveBroker(), yahoo_candles=lambda *_: pytest.fail("should not reach Yahoo"))
    result = asyncio.run(source.candles("NVDA", "1m", 5))
    assert result["source"] == "toss" and result["bars"][0]["close"] == 1.5
    with pytest.raises(ValueError):
        asyncio.run(source.candles("NVDA", "5h", 5))
    fallback = PriceSource(MockBroker(), yahoo_candles=lambda t, i, c: [Bar("2026-11-04T00:00:00+00:00", 1, 1, 1, 1, 0)])
    assert asyncio.run(fallback.candles("NVDA"))["source"] == "yahoo"


def _toss(handler) -> TossBroker:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return handler(request)
    return TossBroker("id", "secret", http=httpx.AsyncClient(base_url="https://toss.test",
                                                                transport=httpx.MockTransport(route)))


def test_toss_reads_string_numbers_from_prices_and_candles():
    def handler(request):
        if request.url.path == "/api/v1/prices":
            assert request.url.params["symbols"] == "005930"
            return httpx.Response(200, json={"result": [
                {"symbol": "005930", "lastPrice": "74,800", "currency": "KRW"}]})
        assert request.url.params["interval"] == "1m"
        return httpx.Response(200, json={"result": {"candles": [
            {"timestamp": "2026-11-04T09:01:00+09:00", "openPrice": "74700", "highPrice": "74900",
             "lowPrice": "74600", "closePrice": "74800", "volume": "1200"},
            {"timestamp": "2026-11-04T09:00:00+09:00", "openPrice": "74500", "highPrice": "74800",
             "lowPrice": "74400", "closePrice": "74700", "volume": "900"},
        ]}})
    broker = _toss(handler)
    assert asyncio.run(broker.quote("005930")) == (74800.0, "KRW")
    bars = asyncio.run(broker.candles("005930", "1m", 2))
    assert [b.at for b in bars] == ["2026-11-04T00:00:00+00:00", "2026-11-04T00:01:00+00:00"]
    assert bars[1].close == 74800.0


def test_toss_without_a_price_raises_so_yahoo_takes_over():
    broker = _toss(lambda request: httpx.Response(200, json={"result": []}))
    yahoo, _ = _yahoo(70000.0)
    quote = asyncio.run(PriceSource(broker, yahoo_quote=yahoo).quote("005930.KS"))
    assert quote.source == "yahoo"


def test_live_quote_moves_a_computed_entry_off_the_stale_close():
    live = {"price": 210.0, "source": "toss", "fetched_at": "2026-11-04T05:00:00+00:00"}
    plan = fill_levels({"bias": "long", "entry_price": None, "stop_loss": None, "price_target": None},
                       "NVDA", "2026-11-04", lambda *_: (200.0, 6.0), live=live)
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (210.0, 201.0, 228.0)
    assert plan["reference_price"] == {"price": 210.0, "source": "toss"}
    assert plan["live_price"] == live
    model = fill_levels({"bias": "long", "entry_price": 195.0, "stop_loss": 190.0, "price_target": 230.0},
                        "NVDA", "2026-11-04", lambda *_: pytest.fail("complete plan"), live=live)
    assert model["entry_price"] == 195.0 and model["live_price"] == live


def _client(db_url, prices, **overrides):
    from datetime import timedelta

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine

    settings = Settings(database_url=db_url, fake_delay=0, **overrides)
    client = TestClient(create_app(settings, runner=make_fake_runner(0), prices=prices),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client


def test_quote_endpoint_needs_a_session_and_reports_the_source(db_url):
    yahoo, _ = _yahoo()
    with _client(db_url, PriceSource(LiveBroker(), yahoo_quote=yahoo), fake_runs=True) as client:
        assert client.get("/api/quote?ticker=NVDA").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/quote?ticker=nvda").json()
        assert body["ticker"] == "NVDA" and body["price"] == 181.5 and body["source"] == "toss" and body["fetched_at"]
        assert client.get("/api/quote?ticker=NV%20DA").status_code == 422


def test_quote_endpoint_is_503_when_every_source_fails(db_url):
    def down(_):
        raise ConnectionError("yahoo down")
    with _client(db_url, PriceSource(LiveBroker(fail=True), yahoo_quote=down), fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/quote?ticker=NVDA").status_code == 503


def test_same_day_run_records_the_live_quote_in_its_plan(db_url):
    yahoo, _ = _yahoo()
    with _client(db_url, PriceSource(LiveBroker(), yahoo_quote=yahoo), fake_runs=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": date.today().isoformat()}).json()
        done = _wait_done(client, run["id"])
        assert done["status"] == "done"
        assert done["plan"]["live_price"]["price"] == 181.5
        assert done["plan"]["live_price"]["source"] == "toss"
        assert done["plan"]["entry_vs_live"]["live"] == 181.5   # the fake plan enters at 182.5


def test_past_date_run_does_not_fetch_a_quote(db_url):
    broker = LiveBroker()
    with _client(db_url, PriceSource(broker), fake_runs=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-01-02"}).json()
        assert _wait_done(client, run["id"])["status"] == "done"
    assert broker.calls == 0


def test_live_quote_far_from_the_close_is_ignored_for_levels():
    live = {"price": 18.0, "source": "toss", "fetched_at": "2026-11-04T05:00:00+00:00"}  # after a 10:1 split
    plan = fill_levels({"bias": "long", "entry_price": None, "stop_loss": None, "price_target": None},
                       "NVDA", "2026-11-04", lambda *_: (180.0, 6.0), live=live)
    assert plan["entry_price"] == 180.0 and plan["reference_price"]["source"] == "close"
    assert "levels_warning" in plan


def test_cache_drops_expired_tickers_once_it_is_full(monkeypatch):
    from desk import prices

    monkeypatch.setattr(prices, "CACHE_MAX", 3)
    clock = Clock()
    yahoo, _ = _yahoo()
    source = PriceSource(MockBroker(), yahoo_quote=yahoo, clock=clock)
    for ticker in ("A", "B", "C"):
        asyncio.run(source.quote(ticker))
    clock.t += QUOTE_TTL_SEC + 1
    asyncio.run(source.quote("D"))
    assert list(source._quotes) == ["D"]


def test_run_still_finishes_when_no_price_source_answers(db_url):
    def down(_):
        raise ConnectionError("yahoo down")
    with _client(db_url, PriceSource(LiveBroker(fail=True), yahoo_quote=down), fake_runs=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": date.today().isoformat()}).json()
        done = _wait_done(client, run["id"])
        assert done["status"] == "done" and "live_price" not in done["plan"]
