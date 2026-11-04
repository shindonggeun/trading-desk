import asyncio
import time

from desk.broker.mock import MockBroker
from desk.db import make_engine, migrate
from desk.universe import Universe, name_key, yahoo_symbol
from tests.test_api import OWNER, PASSWORD, _client

CREDS = {"username": OWNER, "password": PASSWORD}


def _universe(db_url) -> Universe:
    migrate(db_url)
    universe = Universe(make_engine(db_url))
    asyncio.run(universe.refresh(MockBroker()))
    return universe


def test_yahoo_symbols_follow_the_market():
    assert yahoo_symbol("005930", "KOSPI") == "005930.KS"
    assert yahoo_symbol("247540", "KOSDAQ") == "247540.KQ"
    assert yahoo_symbol("BRK.B", "NYSE") == "BRK-B"
    assert yahoo_symbol("NVDA", "NASDAQ") == "NVDA"
    assert name_key(" KODEX 레버리지 ") == "kodex레버리지"


def test_search_by_korean_name_ticker_and_code(db_url):
    universe = _universe(db_url)
    first = lambda q: universe.search(q)[0]
    assert first("엔비")["ticker"] == "NVDA"
    assert first("엔비디아")["name"] == "엔비디아"
    assert first("nvda")["name"] == "엔비디아"
    assert first("삼성")["ticker"] == "005930.KS"
    assert first("005930")["name"] == "삼성전자"
    assert first("에코프로")["ticker"] == "247540.KQ"
    assert first("kodex레버리지")["ticker"] == "122630.KS"     # spaces and case do not matter
    assert first("KODEX 레버")["security_type"] == "ETF"
    assert universe.search("") == []
    assert universe.search("없는종목이름") == []
    assert universe.search("%") == []                           # LIKE wildcards are literal
    assert universe.lookup("NVDA") == {"name": "엔비디아", "market": "NASDAQ"}


def test_refresh_replaces_the_list(db_url):
    universe = _universe(db_url)
    before = universe.status()["count"]
    asyncio.run(universe.refresh(MockBroker()))
    assert universe.status()["count"] == before > 10


def test_instrument_routes_need_login_and_runs_keep_the_name(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/instruments?q=엔비").status_code == 401
        assert client.post("/api/auth/login", json=CREDS).status_code == 204
        for _ in range(100):                 # the listing loads in the background at startup
            if client.get("/api/instruments/status").json()["count"]:
                break
            time.sleep(0.02)
        status = client.get("/api/instruments/status").json()
        assert status["source"] == "mock" and status["count"] > 10 and status["updated_at"]
        hits = client.get("/api/instruments", params={"q": "엔비디아"}).json()
        assert hits[0] == {"symbol": "NVDA", "ticker": "NVDA", "name": "엔비디아",
                           "market": "NASDAQ", "security_type": "STOCK"}

        run = client.post("/api/runs", json={"ticker": "NVDA"}).json()       # name looked up
        assert run["options"]["name"] == "엔비디아"
        run = client.post("/api/runs", json={"ticker": "005930.KS", "name": "삼성전자"}).json()
        assert run["options"]["name"] == "삼성전자"


def test_toss_waits_out_the_rate_limit(monkeypatch):
    import httpx

    from desk.broker import toss

    calls = {"list": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        calls["list"] += 1
        if calls["list"] == 1:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": {"code": "rate-limit-exceeded"}})
        return httpx.Response(200, json={"result": [{"symbol": "NVDA", "name": "엔비디아"}]})

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(toss.asyncio, "sleep", fake_sleep)
    http = httpx.AsyncClient(base_url=toss.BASE_URL, transport=httpx.MockTransport(handler))
    broker = toss.TossBroker("id", "secret", http=http)
    listing = asyncio.run(broker.list_stocks("NASDAQ"))
    assert [item.symbol for item in listing] == ["NVDA"]
    assert calls["list"] == 2 and slept == [1.0]


def test_toss_gives_up_after_repeated_rate_limits(monkeypatch):
    import httpx
    import pytest

    from desk.broker import toss

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(429, headers={"retry-after": "99"})

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(toss.asyncio, "sleep", fake_sleep)
    http = httpx.AsyncClient(base_url=toss.BASE_URL, transport=httpx.MockTransport(handler))
    broker = toss.TossBroker("id", "secret", http=http)
    with pytest.raises(toss.TossError, match="429"):
        asyncio.run(broker.list_stocks("NYSE"))
    assert slept == [toss.MAX_RETRY_AFTER_SEC] * toss.RATE_LIMIT_RETRIES
