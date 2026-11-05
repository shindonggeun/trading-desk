"""#60: more bar intervals (minute, month, year) and RSI(14) on /api/candles."""

import asyncio
from datetime import date, timedelta

import pytest

from desk.broker import Bar, MockBroker
from desk.indicators import RSI_WARMUP, rsi
from desk.prices import BROKER_MAX_COUNT, HISTORY_LIMIT_DAYS, PriceSource, _yahoo_period, group_bars
from desk.prices import yahoo_candles as REAL_YAHOO_CANDLES  # held before conftest's offline stub
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import LiveBroker, _client

# Wilder's worked example as StockCharts publishes it (closes, then RSI from the 15th close).
CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
          46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35,
          44.03, 44.18, 44.22, 44.57, 43.42, 42.66, 43.13]
EXPECTED = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42,
            39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def test_rsi_matches_wilders_worked_example():
    # The published table uses closes with four decimals; these are rounded to two.
    values = rsi(CLOSES)
    assert values[:14] == [None] * 14
    assert values[14:] == pytest.approx(EXPECTED, abs=0.1)


def test_rsi_matches_an_independent_wilder_reference():
    import pandas as pd

    closes = pd.Series([100 + (i * 37 % 23) - (i * 11 % 17) * 0.7 for i in range(300)], dtype=float)
    change = closes.diff()
    gain, loss = change.clip(lower=0), (-change).clip(lower=0)

    def wilder(series):
        seeded = series.copy()
        seeded.iloc[:14] = float("nan")
        seeded.iloc[14] = series.iloc[1:15].mean()        # first average: plain mean of 14 changes
        return seeded.ewm(alpha=1 / 14, adjust=False, ignore_na=True).mean()
    avg_gain, avg_loss = wilder(gain), wilder(loss)
    expected = (100 - 100 / (1 + avg_gain / avg_loss)).round(2).tolist()
    values = rsi(closes.tolist())
    assert values[:14] == [None] * 14
    assert values[14:] == pytest.approx(expected[14:], abs=0.01)


def test_rsi_edge_cases():
    assert rsi([1.0] * 5) == [None] * 5                      # too short
    assert rsi([1.0] * 20)[-1] == 50.0                       # flat: no gains, no losses
    assert rsi([float(i) for i in range(20)])[-1] == 100.0    # only gains


def _bar(at: str, o, h, low, c, v=10.0) -> Bar:
    return Bar(at, o, h, low, c, v)


def test_240_minute_bars_never_span_two_sessions():
    day1 = [(date(2026, 11, 4), _bar(f"2026-11-04T{14 + i}:30:00+00:00", 10 + i, 12 + i, 9 + i, 11 + i))
            for i in range(7)]                              # a US session: 7 hourly bars
    day2 = [(date(2026, 11, 5), _bar(f"2026-11-05T{14 + i}:30:00+00:00", 20 + i, 22 + i, 19 + i, 21 + i))
            for i in range(2)]
    bars = group_bars(day1 + day2, size=4)
    assert [b.at for b in bars] == ["2026-11-04T14:30:00+00:00", "2026-11-04T18:30:00+00:00",
                                    "2026-11-05T14:30:00+00:00"]
    first = bars[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (10, 15, 9, 14, 40.0)
    assert (bars[1].open, bars[1].close, bars[1].volume) == (14, 17, 30.0)   # the session's last 3 hours
    assert (bars[2].open, bars[2].close) == (20, 22)


def test_yearly_bars_merge_the_months_of_each_year():
    months = [(2025, _bar("2025-11-01T04:00:00+00:00", 100, 120, 90, 110)),
              (2025, _bar("2025-12-01T05:00:00+00:00", 110, 130, 105, 125)),
              (2026, _bar("2026-01-01T05:00:00+00:00", 125, 126, 80, 85))]
    years = group_bars(months)
    assert [(b.at[:4], b.open, b.high, b.low, b.close) for b in years] == [
        ("2025", 100, 130, 90, 125), ("2026", 125, 126, 80, 85)]


def test_yahoo_periods_stay_inside_its_intraday_limits():
    assert _yahoo_period("5m", 5000) == f"{HISTORY_LIMIT_DAYS['5m'] - 1}d"
    assert _yahoo_period("240m", 2000) == f"{HISTORY_LIMIT_DAYS['240m'] - 1}d"
    assert _yahoo_period("15m", 10) == "6d"
    assert _yahoo_period("1y", 10) == "max"
    assert _yahoo_period("1M", 12) == f"{12 * 31 + 62}d"


class Daily(LiveBroker):
    def __init__(self):
        super().__init__()
        self.asked = []

    async def candles(self, symbol, interval, count):
        self.asked.append((interval, count))
        start = date(2026, 1, 1)
        return [_bar(f"{start + timedelta(days=i)}T00:00:00+00:00", 1, 1, 1, CLOSES[i % len(CLOSES)])
                for i in range(count)]


def test_rsi_fetches_warm_up_bars_and_returns_only_the_ones_asked_for():
    broker = Daily()
    source = PriceSource(broker, yahoo_candles=lambda *_: pytest.fail("should stay on Toss"))
    count = BROKER_MAX_COUNT - RSI_WARMUP
    result = asyncio.run(source.candles("NVDA", "1d", count, ("rsi14",)))
    assert broker.asked == [("1d", count + RSI_WARMUP)]
    assert len(result["bars"]) == count and result["indicators"] == ["rsi14"]
    assert all(bar["rsi"] is not None for bar in result["bars"])          # settled from the first bar
    plain = asyncio.run(source.candles("NVDA", "1d", 20))
    assert "rsi" not in plain["bars"][0] and plain["indicators"] == []
    assert plain["history_limit_days"] is None and plain["truncated"] is False


def test_rsi_keeps_toss_as_the_source_when_the_chart_itself_fits():
    broker = Daily()
    source = PriceSource(broker, yahoo_candles=lambda *_: pytest.fail("should stay on Toss"))
    result = asyncio.run(source.candles("NVDA", "1d", 180, ("rsi14",)))
    assert broker.asked == [("1d", BROKER_MAX_COUNT)] and result["source"] == "toss"
    assert len(result["bars"]) == 180 and result["bars"][-1]["rsi"] is not None


def test_rsi_and_plain_answers_are_cached_apart():
    broker = Daily()
    source = PriceSource(broker)
    with_rsi = asyncio.run(source.candles("NVDA", "1d", 30, ("rsi14",)))
    plain = asyncio.run(source.candles("NVDA", "1d", 30))
    assert "rsi" in with_rsi["bars"][0] and "rsi" not in plain["bars"][0]
    assert len(broker.asked) == 2


def test_warm_up_past_toss_limit_falls_to_yahoo_and_short_history_is_flagged():
    calls = []

    def yahoo(ticker, interval, count):
        calls.append((interval, count))
        return [_bar(f"2026-01-{d:02d}T00:00:00+00:00", 1, 1, 1, float(d)) for d in range(1, 31)]
    source = PriceSource(Daily(), yahoo_candles=yahoo)
    result = asyncio.run(source.candles("NVDA", "1d", 250, ("rsi14",)))   # past Toss' 200
    assert calls == [("1d", 250 + RSI_WARMUP)] and result["source"] == "yahoo"
    assert result["truncated"] is True and len(result["bars"]) == 30
    assert result["bars"][0]["rsi"] is None and result["bars"][-1]["rsi"] == 100.0


@pytest.mark.parametrize("interval", ["5m", "15m", "30m", "60m", "240m", "1w", "1M", "1y"])
def test_intervals_toss_does_not_serve_go_to_yahoo(interval):
    calls = []

    def yahoo(ticker, asked, count):
        calls.append(asked)
        return [_bar("2026-01-01T00:00:00+00:00", 1, 1, 1, 1)]
    result = asyncio.run(PriceSource(Daily(), yahoo_candles=yahoo).candles("NVDA", interval, 10))
    assert calls == [interval] and result["source"] == "yahoo"
    assert result["history_limit_days"] == HISTORY_LIMIT_DAYS.get(interval)


def test_unknown_interval_or_indicator_is_refused():
    source = PriceSource(MockBroker())
    with pytest.raises(ValueError):
        asyncio.run(source.candles("NVDA", "7m"))
    with pytest.raises(ValueError):
        asyncio.run(source.candles("NVDA", "1d", 10, ("macd",)))


def test_candles_endpoint_takes_new_intervals_and_rsi(db_url):
    def yahoo(ticker, interval, count):
        return [_bar(f"2026-01-{d:02d}T00:00:00+00:00", 1, 1, 1, CLOSES[d]) for d in range(1, 31)][-count:]
    with _client(db_url, PriceSource(Daily(), yahoo_candles=yahoo), fake_runs=True) as client:
        assert client.get("/api/candles?ticker=NVDA&interval=15m").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/candles?ticker=NVDA&interval=15m&count=20&indicators=rsi14").json()
        assert body["interval"] == "15m" and body["source"] == "yahoo" and body["indicators"] == ["rsi14"]
        assert body["history_limit_days"] == 60 and "rsi" in body["bars"][-1]
        monthly = client.get("/api/candles?ticker=NVDA&interval=1M&count=5").json()
        assert monthly["interval"] == "1M" and len(monthly["bars"]) == 5
        assert client.get("/api/candles?ticker=NVDA&interval=1d&indicators=macd").status_code == 422
        assert client.get("/api/candles?ticker=NVDA&interval=2h").status_code == 422


class _FakeTicker:
    """yf.Ticker stand-in: hourly or monthly bars indexed in the exchange's time zone."""

    frame = None

    def __init__(self, ticker):
        pass

    def history(self, period, interval, auto_adjust):
        return _FakeTicker.frame


def test_yahoo_240m_groups_by_the_exchange_day_and_cleans_nans(monkeypatch):
    import pandas as pd
    import yfinance

    # New York, 2026-11-04: seven hourly bars, the last ones past 20:00 UTC (still that day there),
    # plus a bar with no prices and one with no volume.
    index = pd.date_range("2026-11-04 09:30", periods=7, freq="60min", tz="America/New_York").append(
        pd.date_range("2026-11-05 09:30", periods=2, freq="60min", tz="America/New_York"))
    frame = pd.DataFrame({"Open": [float(i) for i in range(9)], "High": [i + 1.0 for i in range(9)],
                          "Low": [i - 1.0 for i in range(9)], "Close": [i + 0.5 for i in range(9)],
                          "Volume": [10.0] * 9}, index=index)
    frame.iloc[1, frame.columns.get_loc("Volume")] = float("nan")
    frame.iloc[8, frame.columns.get_loc("Close")] = float("nan")
    _FakeTicker.frame = frame
    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    bars = REAL_YAHOO_CANDLES("NVDA", "240m", 10)
    assert [b.at for b in bars] == ["2026-11-04T14:30:00+00:00", "2026-11-04T18:30:00+00:00",
                                    "2026-11-05T14:30:00+00:00"]
    assert bars[0].volume == 30.0                     # NaN volume counted as 0
    assert (bars[1].open, bars[1].close) == (4.0, 6.5)   # 18:30-20:30 UTC stays on 11-04
    assert bars[2].close == 7.5                       # the NaN-price bar was dropped


def test_yahoo_yearly_bars_group_by_calendar_year(monkeypatch):
    import pandas as pd
    import yfinance

    index = pd.DatetimeIndex(["2025-11-01", "2025-12-01", "2026-01-01"], tz="Asia/Seoul")
    _FakeTicker.frame = pd.DataFrame({"Open": [1.0, 2.0, 3.0], "High": [5.0, 6.0, 7.0], "Low": [0.5, 1.0, 2.0],
                                      "Close": [2.0, 3.0, 4.0], "Volume": [1.0, 1.0, 1.0]}, index=index)
    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    years = REAL_YAHOO_CANDLES("005930.KS", "1y", 5)
    assert [(b.open, b.high, b.close, b.volume) for b in years] == [(1.0, 6.0, 3.0, 2.0), (3.0, 7.0, 4.0, 1.0)]

