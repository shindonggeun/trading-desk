"""The weekly observation extends the existing authenticated chart read."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from desk import api
from desk import prices as prices_module
from desk.broker import Bar, MockBroker
from desk.prices import PriceSource
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import _client

NY = ZoneInfo("America/New_York")


@pytest.fixture
def chart_now(monkeypatch):
    stamp = datetime(2026, 11, 25, 20, tzinfo=UTC)

    class ChartClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return stamp.astimezone(tz) if tz else stamp.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", ChartClock)
    monkeypatch.setattr(prices_module, "_now", lambda: stamp.isoformat())
    return stamp


def _feed(now, *, weekly_down=False):
    local = now.astimezone(NY)
    monday = local.date() - timedelta(days=local.weekday())
    # Include a potential forming bar. Let the observation apply the actual cutoff.
    weeks = [
        _bar(datetime.combine(monday - timedelta(weeks=offset), time(), NY), 170 - offset / 2)
        for offset in reversed(range(80))
    ]
    at = now.replace(second=0, microsecond=0) - timedelta(minutes=5)
    intra = [_bar(at - timedelta(minutes=5 * offset), 180 - offset / 4) for offset in reversed(range(80))]
    daily = [_bar((at - timedelta(days=offset)).replace(hour=0), 175 - offset / 2) for offset in reversed(range(80))]
    asked = []

    def yahoo(ticker, interval, count):
        asked.append((ticker, interval))
        if weekly_down and interval == "1w":
            raise ConnectionError("synthetic private error detail")
        return {"1w": weeks, "1d": daily}.get(interval, intra)[-count:]

    def quote(ticker):
        asked.append((ticker, "quote"))
        raise AssertionError("chart evidence should reuse the existing candles")

    return PriceSource(MockBroker(), yahoo_candles=yahoo, yahoo_quote=quote), asked, intra[-1]


def _bar(at, close):
    return Bar(at.astimezone(UTC).isoformat(timespec="seconds"), close, close + 1, close - 1, close, 100)


def test_chart_weekly_observation_reuses_candles_and_normal_chart_cache(db_url, chart_now):
    prices, asked, price = _feed(chart_now)
    query = "/api/chart-read?ticker=nq%3Df&tf=5m,1d"
    with _client(db_url, prices, fake_runs=True) as client:
        assert client.get(query).status_code == 401
        assert asked == []
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        response = client.get(query)
        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "NQ=F"
        assert [frame["timeframe"] for frame in body["timeframes"]] == ["5m", "1d"]
        assert "accumulation" in body and "legs" in body

        weekly = body["weekly_drawdown"]
        assert weekly["status"] == "available"
        assert weekly["week"]["candle"] == "closed"
        assert weekly["week"]["source"] == "yahoo"
        assert weekly["current"]["kind"] == "chart_bar_close"
        assert weekly["current"]["timeframe"] == "5m"
        assert weekly["current"]["asOf"] == price.at
        assert weekly["current"]["price"] == price.close
        assert set(weekly["evidence"]) == {"bollinger", "rsi", "channel", "fib"}
        assert sorted(asked) == [("NQ=F", "1d"), ("NQ=F", "1w"), ("NQ=F", "5m")]

        assert client.get("/api/chart-read?ticker=NQ%3DF&tf=1d,5m").json() == body
        assert len(asked) == 3
        assert "weekly_drawdown" not in client.get("/api/chart-read?ticker=NQ%3DF&tf=5m").json()
        assert "weekly_drawdown" not in client.get("/api/chart-read?ticker=ES%3DF&tf=1d").json()
        assert client.get("/api/chart-read?ticker=NQ%3DF&tf=1w").status_code == 422
        assert all(interval != "quote" for _, interval in asked)


def test_weekly_unavailability_does_not_break_chart_read_or_expose_source_error(db_url, chart_now):
    prices, _, _ = _feed(chart_now, weekly_down=True)
    with _client(db_url, prices, fake_runs=True) as client:
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        response = client.get("/api/chart-read?ticker=NQ%3DF&tf=1d")
        assert response.status_code == 200
        assert response.json()["timeframes"][0]["timeframe"] == "1d"
        observation = response.json()["weekly_drawdown"]
        assert observation["status"] == "unavailable" and observation["reasonCode"]
        assert "private error detail" not in response.text
