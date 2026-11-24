"""The observation reuses the price source and authentication of chart-read."""

from datetime import UTC, datetime, time, timedelta
from math import sin

import pytest

from desk import api, daily_resistance, market_calendar
from desk import prices as prices_module
from desk.broker import Bar, MockBroker
from desk.prices import PriceSource
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import _client

URL = "/api/chart-read"
NOW = datetime(2026, 11, 18, 22, tzinfo=UTC)


@pytest.fixture
def daily_feed(monkeypatch):
    class ChartClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", ChartClock)
    monkeypatch.setattr(prices_module, "_now", lambda: NOW.isoformat())
    days = [NOW.date() - timedelta(days=n) for n in reversed(range(100))
            if market_calendar.is_open("us", NOW.date() - timedelta(days=n))]
    history = []
    for i, day in enumerate(days[:-1]):
        at = datetime.combine(day, time(), market_calendar.ET).astimezone(UTC).isoformat()
        close = 100 + 4 * sin(i / 2)
        history.append(Bar(at, close - .2, close + 1, close - 1, close, 1000))
    at = datetime.combine(days[-1], time(), market_calendar.ET).astimezone(UTC).isoformat()
    history.append(Bar(at, 101, 111, 100, 110, 1800))

    asked = []

    def yahoo(ticker, interval, count):
        asked.append((ticker, interval))
        return history[-count:] if interval == "1d" else []

    def quote(ticker):
        raise AssertionError("daily observation must not request a quote")

    return PriceSource(MockBroker(), yahoo_candles=yahoo, yahoo_quote=quote), asked, at


def test_daily_observation_reuses_the_chart_fetch_and_cache(db_url, daily_feed):
    prices, asked, at = daily_feed
    args = {"ticker": "soxx", "tf": "1d"}
    with _client(db_url, prices, fake_runs=True) as client:
        assert client.get(URL, params=args).status_code == 401
        assert not asked
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        response = client.get(URL, params=args)
        assert response.status_code == 200, response.text
        result = response.json()
        evidence = result["daily_reaction"]
        assert result["timeframes"][0]["timeframe"] == "1d"
        assert {"accumulation", "legs", "summary"} <= result.keys()
        assert evidence["ticker"] == "SOXX" and evidence["status"] == "confirmed"
        assert evidence["bar"]["at"] == at and evidence["bar"]["close"] == 110
        assert evidence["previous_close"]["at"] < evidence["bar"]["at"]
        assert evidence["source"] == "yahoo"
        assert sorted(asked) == [("SOXX", "1d"), ("SOXX", "1w")]
        assert client.get(URL, params=args).json() == result
        assert len(asked) == 2
        assert "daily_reaction" not in client.get(URL, params={"ticker": "SOXX", "tf": "5m"}).json()
        assert "daily_reaction" not in client.get(URL, params={"ticker": "NVDA", "tf": "1d"}).json()
        cfd = client.get(URL, params={"ticker": "NAS100", "tf": "1d"}).json()["daily_reaction"]
        assert cfd["status"] == "unavailable" and cfd["reason"] == "product_unsupported"
        assert cfd["bar"] is None and cfd["resistance"] is None


def test_reaction_computation_failure_preserves_the_chart(db_url, daily_feed, monkeypatch):
    prices, _, _ = daily_feed

    def fail(*args):
        raise RuntimeError("raw source detail")

    monkeypatch.setattr(daily_resistance, "build", fail)
    with _client(db_url, prices, fake_runs=True) as client:
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        response = client.get(URL, params={"ticker": "SOXX", "tf": "1d"})
        assert response.status_code == 200 and "raw source detail" not in response.text
        body = response.json()
        assert body["timeframes"][0]["timeframe"] == "1d"
        assert body["daily_reaction"]["reason"] == "reaction_error"


def test_chart_read_cache_cannot_cross_the_open_or_close_phase(db_url, daily_feed, monkeypatch):
    prices, _, _ = daily_feed
    clock = datetime(2026, 11, 18, 14, 29, 59, tzinfo=UTC)

    class ChartClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", ChartClock)
    monkeypatch.setattr(prices_module, "_now", lambda: clock.isoformat())
    args = {"ticker": "SOXX", "tf": "1d"}
    with _client(db_url, prices, fake_runs=True) as client:
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        before_open = client.get(URL, params=args).json()["daily_reaction"]
        assert before_open["session"]["trade_date"] == "2026-11-17"
        clock += timedelta(seconds=2)
        open_view = client.get(URL, params=args).json()["daily_reaction"]
        assert open_view["session"]["trade_date"] == "2026-11-18"
        assert open_view["status"] == "in_progress" and open_view["reason"] == "session_open"
        clock = datetime(2026, 11, 18, 21, 14, 59, tzinfo=UTC)
        settling = client.get(URL, params=args).json()["daily_reaction"]
        assert settling["reason"] == "awaiting_confirmation"
        clock += timedelta(seconds=2)
        after = client.get(URL, params=args).json()["daily_reaction"]
        assert after["status"] != "in_progress"
        assert after["observed_at"] == clock.isoformat()
