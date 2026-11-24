"""The public funding data is exposed through the desk's existing session boundary."""

from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from desk.api import create_app
from desk.auth import Auth
from desk.binance_funding import FundingHistory
from desk.db import make_engine
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD

NOW = datetime(2026, 11, 24, 2, tzinfo=UTC)


def _client(db_url, service=None):
    settings = Settings(database_url=db_url, fake_delay=0, fake_runs=True)
    app = create_app(settings, runner=make_fake_runner(0), funding_history=service)
    engine = make_engine(db_url)
    try:
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    finally:
        engine.dispose()
    return TestClient(app, base_url="https://testserver")


def test_funding_requires_session_validates_query_and_returns_confirmed_history(db_url):
    asked = []
    midnight = NOW.replace(hour=0)
    stamps = [midnight - timedelta(hours=8 * i) for i in range(22)]

    def respond(req):
        assert req.method == "GET" and req.url.host == "fapi.binance.com"
        assert "authorization" not in req.headers and "x-mbx-apikey" not in req.headers
        asked.append(req.url.path)
        if req.url.path.endswith("/exchangeInfo"):
            return httpx.Response(200, json={"symbols": [{
                "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT",
                "contractType": "PERPETUAL", "status": "TRADING", "onboardDate": 1567965300000,
            }]})
        if req.url.path.endswith("/fundingInfo"):
            return httpx.Response(200, json=[{"symbol": "BTCUSDT", "fundingIntervalHours": 8}])
        assert req.url.path.endswith("/fundingRate") and req.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json=[
            {"symbol": "BTCUSDT", "fundingTime": int(at.timestamp() * 1000), "markPrice": "90000",
             "fundingRate": "-0.0001"}
            for at in reversed(stamps)
        ])

    service = FundingHistory(transport=httpx.MockTransport(respond), clock=lambda: NOW)
    with _client(db_url, service) as client:
        assert client.get("/api/positioning/funding?symbol=BTCUSDT").status_code == 401
        assert asked == []
        login = client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert login.status_code == 204
        for query in ("", "?symbol=UNKNOWN", "?symbol=BTC%2FUSDT", "?symbol=BTCUSDT&days=0",
                      "?symbol=BTCUSDT&days=31", "?symbol=BTCUSDT&days=seven"):
            assert client.get("/api/positioning/funding" + query).status_code == 422
        assert asked == []
        response = client.get("/api/positioning/funding?symbol=btcusdt&days=7")
        assert response.status_code == 200
        body = response.json()
        assert body["symbol"] == "BTCUSDT" and body["status"] == "available" and body["source"] == "binance"
        latest = body["latest_settlement"]
        assert datetime.fromisoformat(latest["settled_at"]) == midnight
        assert (latest["rate"], latest["mark_price"], latest["rate_type"]) == (-0.0001, 90000, "regular")
        assert len(body["daily"]) == 7 and body["daily"][-1]["status"] == "complete"
        assert body["daily"][-1]["average_7d"] == -0.0003
        assert body["negative_streak_days"] == 7
        assert body["current_day"]["status"] == "partial" and body["current_day"]["settlement_count"] == 1
        default = client.get("/api/positioning/funding?symbol=BTCUSDT").json()
        assert default["days"] == 30 and len(asked) == 3


def test_funding_fake_runs_are_offline_and_domestic_positioning_stays_available(db_url):
    with _client(db_url) as client:
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        funding = client.get("/api/positioning/funding?symbol=SOXXUSDT").json()
        assert funding["status"] == "source_error" and funding["reason"] == "fake_runs"
        assert funding["underlying"]["kind"] == "etf"
        assert funding["latest_settlement"] is None
        positioning = client.get("/api/positioning?ticker=005930.KS")
        assert positioning.status_code == 200 and "kr_short" in positioning.json()
