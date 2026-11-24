"""The monitor is a bounded, authenticated read over existing candles."""

from datetime import UTC, datetime

from desk import api
from desk import prices as prices_module
from desk.broker import Bar, MockBroker
from desk.prices import PriceSource
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import _client

URL = "/api/market/watchboard"
NOW = datetime(2026, 11, 24, 6, tzinfo=UTC)


def test_watchboard_auth_bounds_and_daily_cache(db_url, monkeypatch):
    class WatchboardClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", WatchboardClock)
    monkeypatch.setattr(prices_module, "_now", lambda: NOW.isoformat())
    asked = []

    def yahoo(ticker, interval, count):
        asked.append((ticker, interval, count))
        close = 200 if ticker == "ETH-USD" else 100
        return [Bar("2026-11-22T00:00:00+00:00", close, close, close, close, 0),
                Bar("2026-11-23T00:00:00+00:00", close * 1.1, close * 1.1, close, close * 1.1, 0)]

    prices = PriceSource(MockBroker(), yahoo_candles=yahoo)
    with _client(db_url, prices, fake_runs=True) as client:
        assert client.get(URL).status_code == 401
        assert not asked
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        assert client.get(URL, params={"group": "not-a-group"}).status_code == 422
        assert client.get(URL, params={"days": 0}).status_code == 422
        assert client.get(URL, params={"days": 91}).status_code == 422
        assert not asked
        response = client.get(URL, params={"group": "crypto", "days": 7})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["group"] == "crypto" and body["days"] == 7 and body["as_of"] == NOW.isoformat()
        assert {item["ticker"] for item in body["items"]} == {"BTC-USD", "ETH-USD"}
        assert all(item["latest"]["trade_date"] == "2026-11-23" for item in body["items"])
        assert body["comparison"]["base_date"] == "2026-11-22"
        assert sorted(asked) == [("BTC-USD", "1d", 300), ("ETH-USD", "1d", 300)]
        assert client.get(URL, params={"group": "crypto", "days": 30}).status_code == 200
        assert len(asked) == 2


def test_default_group_reads_local_listings_without_broker_or_raw_errors(db_url, monkeypatch):
    monkeypatch.setattr(prices_module, "_now", lambda: NOW.isoformat())
    asked, broker_calls = [], []

    async def broker_read(*args):
        broker_calls.append(args)
        raise AssertionError("watchboard must not use the broker")

    def yahoo(ticker, interval, count):
        asked.append((ticker, interval, count))
        if ticker == "285A.T":
            raise ConnectionError("private provider detail")
        return []

    broker = MockBroker()
    monkeypatch.setattr(broker, "candles", broker_read)
    prices = PriceSource(broker, yahoo_candles=yahoo)
    with _client(db_url, prices, fake_runs=True) as client:
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        response = client.get(URL)
        assert response.status_code == 200 and "private provider detail" not in response.text
        body = response.json()
        assert body["group"] == "semiconductors"
        items = {item["ticker"]: item for item in body["items"]}
        assert items["285A.T"]["reason"] == "source_error"
        assert items["2330.TW"]["currency"] == "TWD" and items["2330.TW"]["kind"] == "stock"
        assert items["TSM"]["currency"] == "USD" and items["TSM"]["kind"] == "adr"
        assert set(asked) == {(ticker, "1d", 300) for ticker in items}
        assert not broker_calls
