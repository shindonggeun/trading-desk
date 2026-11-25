"""#977 chart-read carries the daily trend-following read on its own daily fetch."""

from datetime import UTC, datetime, time, timedelta

from desk import api, market_calendar
from desk import prices as prices_module
from desk.broker import Bar, MockBroker
from desk.prices import PriceSource
from tests.test_api import OWNER, PASSWORD
from tests.test_daily_resistance_api import daily_feed  # noqa: F401 - the fixture
from tests.test_prices import _client

URL = "/api/chart-read"


def _login(client):
    assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204


def test_chart_read_adds_trend_follow_on_the_daily_only(db_url, daily_feed):  # noqa: F811
    prices, asked, at = daily_feed
    with _client(db_url, prices, fake_runs=True) as client:
        assert client.get(URL, params={"ticker": "SOXX", "tf": "1d"}).status_code == 401
        _login(client)
        result = client.get(URL, params={"ticker": "SOXX", "tf": "1d"}).json()
        read = result["trend_follow"]
        assert read["ticker"] == "SOXX" and read["timeframe"] == "1d"
        assert read["status"] == "confirmed" and read["as_of"] == at
        assert read["instrument"]["kind"] == "etf" and read["instrument"]["provider"] == "yahoo"
        assert read["stage"]["state"] in {"first_rebound", "awaiting_confirmation", "breakout_confirmed",
                                          "failed_breakout", "none"}
        assert {e["kind"] for e in read["evidence"]} == {"quarter_level", "channel_quarter", "fib_extension",
                                                        "divergence", "stochastic", "overlap"}
        assert read["lead"] is None
        assert sorted(asked) == [("SOXX", "1d"), ("SOXX", "1w")]          # no fetch of its own
        assert "trend_follow" not in client.get(URL, params={"ticker": "SOXX", "tf": "5m"}).json()


def test_kospi_lead_reads_the_other_two_daily_bars(db_url, monkeypatch):
    now = datetime(2026, 11, 24, 9, tzinfo=UTC)                     # after the KRX close (06:30 UTC)

    class ChartClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", ChartClock)
    monkeypatch.setattr(prices_module, "_now", lambda: now.isoformat())
    days = [now.date() - timedelta(days=n) for n in reversed(range(140)) if (now.date() - timedelta(days=n)).weekday() < 5]
    history = []
    for i, day in enumerate(days):
        stamp = datetime.combine(day, time(), market_calendar.KST).astimezone(UTC).isoformat()
        close = 100 + (i % 8 if i % 8 < 4 else 8 - i % 8)
        history.append(Bar(stamp, close - .2, close + .5, close - .5, close, 1000))
    asked = []

    def yahoo(ticker, interval, count):
        asked.append((ticker, interval))
        return history[-count:] if interval == "1d" else []

    feed = PriceSource(MockBroker(), yahoo_candles=yahoo, yahoo_quote=lambda t: None)
    with _client(db_url, feed, fake_runs=True) as client:
        _login(client)
        read = client.get(URL, params={"ticker": "^KS11", "tf": "1d"}).json()["trend_follow"]
    assert read["instrument"]["kind"] == "cash_index"
    assert {("000660.KS", "1d"), ("005930.KS", "1d")} <= set(asked)
    assert [i["ticker"] for i in read["lead"]["items"]] == ["000660.KS", "005930.KS"]
    states = {i["state"] for i in read["lead"]["items"]}               # the same bars all round: never ahead
    assert len(states) == 1 and states <= {"same", "waiting"}


def test_a_failing_read_leaves_the_rest_of_the_chart(db_url, daily_feed, monkeypatch):  # noqa: F811
    from desk import trend_follow

    def boom(*args, **kwargs):
        raise RuntimeError("bad bars")

    monkeypatch.setattr(trend_follow, "build", boom)
    prices, _, _ = daily_feed
    with _client(db_url, prices, fake_runs=True) as client:
        _login(client)
        response = client.get(URL, params={"ticker": "SOXX", "tf": "1d"})
    assert response.status_code == 200
    result = response.json()
    assert result["trend_follow"]["status"] == "unavailable" and result["trend_follow"]["reason"] == "trend_follow_error"
    assert result["daily_reaction"]["status"] == "confirmed" and result["timeframes"][0]["timeframe"] == "1d"
