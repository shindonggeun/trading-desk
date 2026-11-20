"""#747 CME futures from 18:00 New York (Monday to Thursday): yfinance keeps only the next session's live
daily row, dated that evening, and drops the session that ended at 17:00 - the price layer puts both back."""

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta

import pytest

from desk import gap_fill, playbook, prices, session_progress
from desk.broker import Bar
from desk.market_calendar import ET
from desk.prices import yahoo_candles as real_yahoo_candles  # conftest 가 막기 전의 것

TUESDAY = date(2026, 11, 17)
EVENING = datetime(2026, 11, 17, 20, 0, tzinfo=ET)       # Tuesday 20:00 New York: Wednesday's session trades


def _stamp(day: date) -> str:
    return prices._ny_midnight(day)


def _daily(day: date, o: float, h: float, low: float, c: float, v: float = 1000.0) -> Bar:
    return Bar(at=_stamp(day), open=o, high=h, low=low, close=c, volume=v)


def _weekdays(last: date, n: int) -> list[date]:
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def as_yfinance_gives() -> list[Bar]:
    """90 quiet sessions at 100 (ATR 1) up to Monday 11-16, then the live row of Wednesday's session dated
    Tuesday - opening 4 ATR under Monday's close because Tuesday fell all day, not because of a gap."""
    bars = [_daily(d, 100.0, 100.5, 99.5, 100.0) for d in _weekdays(date(2026, 11, 16), 90)]
    return [*bars, _daily(TUESDAY, 96.0, 96.4, 95.8, 96.2, v=300.0)]


def hourly() -> list[Bar]:
    """Tuesday's session, 18:00 Monday to 17:00 Tuesday, sliding from 100 to 96 - then Wednesday's first hours."""
    out = []
    start = datetime(2026, 11, 16, 18, 0, tzinfo=ET)
    for k in range(23):                                    # 18:00 ... 16:00, 23 hours
        o = 100.0 - k * 4 / 23
        c = 100.0 - (k + 1) * 4 / 23
        out.append(Bar(at=(start + timedelta(hours=k)).astimezone(UTC).isoformat(timespec="seconds"),
                       open=o, high=o + 0.1, low=c - 0.1, close=c, volume=10.0))
    for k, (o, c) in enumerate(((96.0, 96.1), (96.1, 96.2))):   # Tuesday 18:00 and 19:00: the live session
        out.append(Bar(at=datetime(2026, 11, 17, 18 + k, 0, tzinfo=ET).astimezone(UTC).isoformat(timespec="seconds"),
                       open=o, high=c + 0.2, low=o - 0.2, close=c, volume=150.0))
    return out


@pytest.fixture(autouse=True)
def _fresh():
    prices._FINISHED.clear()
    yield
    prices._FINISHED.clear()


def _dicts(bars: list[Bar]) -> list[dict]:
    return [asdict(b) for b in bars]


def test_the_finished_session_comes_back_and_the_live_row_moves_to_its_own_date():
    fixed = prices.with_finished_session("NQ=F", as_yfinance_gives(), EVENING, hourly)
    tuesday, wednesday = fixed[-2], fixed[-1]
    assert tuesday.at == "2026-11-17T05:00:00+00:00" and wednesday.at == "2026-11-18T05:00:00+00:00"
    assert tuesday.open == 100.0 and tuesday.close == pytest.approx(96.0)
    assert tuesday.high == pytest.approx(100.1) and tuesday.low == pytest.approx(95.9)
    assert tuesday.volume == 230.0                                   # only 18:00 Monday ... 17:00 Tuesday
    assert (wednesday.open, wednesday.close, wednesday.volume) == (96.0, 96.2, 300.0)
    assert len(fixed) == len(as_yfinance_gives()) + 1


def _session_itself() -> list[Bar]:
    """Right after 18:00, before Yahoo has a live row: the last bar is Tuesday's finished session."""
    return [*as_yfinance_gives()[:-1], _daily(TUESDAY, 100.0, 100.1, 95.9, 96.0)]


def _raises():
    raise RuntimeError("429")


@pytest.mark.parametrize(("ticker", "now", "bars", "rows"), [
    ("NQ=F", datetime(2026, 11, 17, 17, 30, tzinfo=ET), as_yfinance_gives, hourly),   # the shut hour
    ("NQ=F", datetime(2026, 11, 18, 0, 30, tzinfo=ET), as_yfinance_gives, hourly),    # after midnight
    ("NQ=F", datetime(2026, 11, 15, 20, 0, tzinfo=ET), as_yfinance_gives, hourly),    # Sunday: nothing dropped
    ("NQ=F", datetime(2026, 11, 20, 20, 0, tzinfo=ET), as_yfinance_gives, hourly),    # Friday: no session
    ("QQQ", EVENING, as_yfinance_gives, hourly),                                      # an ETF's own session
    ("NQ=F", EVENING, _session_itself, hourly),                                       # no live row yet
    ("NQ=F", EVENING, as_yfinance_gives, _raises),                                    # no hourly bars
    ("NQ=F", EVENING, as_yfinance_gives, list),
])
def test_the_daily_bars_stay_as_they_came(ticker, now, bars, rows):
    given = bars()
    assert prices.with_finished_session(ticker, given, now, rows) == given


def test_a_finished_session_is_built_once():
    calls = []

    def counted():
        calls.append(1)
        return hourly()

    first = prices.with_finished_session("ES=F", as_yfinance_gives(), EVENING, counted)
    later = prices.with_finished_session("ES=F", as_yfinance_gives(), EVENING + timedelta(minutes=30), counted)
    assert first == later and len(calls) == 1


def test_yahoo_candles_mends_daily_bars_only(monkeypatch):
    seen = []
    monkeypatch.setattr(prices, "_yahoo_rows", lambda t, period, interval: [(None, b) for b in as_yfinance_gives()])
    monkeypatch.setattr(prices, "with_finished_session",
                        lambda t, bars, now, rows: seen.append((t, len(bars))) or bars[:-1])
    assert len(real_yahoo_candles("NQ=F", "1d", 50)) == 50 and seen == [("NQ=F", 91)]
    real_yahoo_candles("NQ=F", "60m", 50)
    assert len(seen) == 1


def test_the_gap_odds_see_no_gap_from_the_mended_bars():
    """#741 kept the live row from opening a gap; with the session back there is no gap to keep out."""
    raw, fixed = _dicts(as_yfinance_gives()), _dicts(prices.with_finished_session("NQ=F", as_yfinance_gives(),
                                                                                  EVENING, hourly))
    assert [g["day"] for g in gap_fill.measure(raw, "NQ=F")] == ["2026-11-17"]   # the bug: a whole day as a gap
    assert gap_fill.measure(fixed, "NQ=F") == []
    assert not gap_fill.evening_session(fixed, "NQ=F", EVENING)          # #746's guard has nothing to do
    rep = gap_fill.report(lambda t: fixed, "NQ=F", now=EVENING)
    assert rep["open"] == [] and rep["last_close"] == 96.2


def test_the_session_clock_and_the_playbook_gaps_read_the_mended_bars(monkeypatch):
    fixed = _dicts(prices.with_finished_session("NQ=F", as_yfinance_gives(), EVENING, hourly))
    # #568: Wednesday's session before New York's regular open - its volume is not judged yet
    assert session_progress.elapsed("NQ=F", fixed, EVENING) == 0.0
    monkeypatch.setattr(playbook, "GAP_FILL_WEIGHT", 1.0)               # off in production; on to look
    assert playbook.gap_fills(_dicts(as_yfinance_gives()), 1.0)          # the bug: a false daily gap
    assert playbook.gap_fills(fixed, 1.0) == []
