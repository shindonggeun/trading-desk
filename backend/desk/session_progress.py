"""How far today's regular session has run (#432): the clock of a live plan, not a plan rule.

A live plan's last daily bar can be the session still trading - or one not open yet (a US bar that
carries pre-market trades). ``elapsed`` says how much of that session has passed, so desk.setup_filter
does not hold a part-day's volume against a whole day's average. The backtest never calls this (its
bars are finished sessions), so desk.backtest_reports.DATA_MODULES leaves it out of the rule fingerprint.

- The bar's day is the market's own, as desk.sr._local_day reads it: a Korean bar opens at local
  midnight (15:00 UTC the day before), a US bar carries its own date in UTC (Yahoo's 04:00/05:00 UTC,
  a broker's date-only bar as UTC midnight).
- Korean (.KS/.KQ) and US stocks and ETFs only. Futures, FX, crypto and indices have no one daily
  session to measure (a futures day runs 18:00 to 17:00): None, the volume read as it is.
- A US early close (13:00 ET, desk.market_calendar.us_early_close) ends the session there.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time

from . import market_calendar
from .estimate import KRX, US

US_EARLY_CLOSE = clock_time(13, 0)


def _korean(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ"))


def bar_day(at: str, ticker: str) -> date:
    """The market's own date of a daily bar (desk.sr._local_day's rule; a bare date is read as is)."""
    stamp = datetime.fromisoformat(at)
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(UTC)
        if _korean(ticker):
            stamp += timedelta(hours=9)
    return stamp.date()


def elapsed(ticker: str, daily: list[dict], now: datetime | None = None) -> float | None:
    """0.0 before the open up to 1.0 at the close, when the last daily bar is today's session and that
    session has not closed. None for a finished session, no bars, or a market without one daily session."""
    t = ticker.upper()
    if _korean(t):
        session = KRX
    elif t.startswith("^") or t.endswith(("=F", "=X", "-USD")):
        return None
    else:
        session = US
    if not daily or not daily[-1].get("at"):
        return None
    local = (now or datetime.now(UTC)).astimezone(session.zone)
    try:
        if bar_day(daily[-1]["at"], t) != local.date():
            return None
    except (TypeError, ValueError):
        return None
    opens, closes = session.hours(local.date())
    if session is US and market_calendar.us_early_close(local.date()):
        closes = min(closes, US_EARLY_CLOSE)
    start = datetime.combine(local.date(), opens, session.zone)
    end = datetime.combine(local.date(), closes, session.zone)
    if end <= start or local >= end:
        return None
    return min(1.0, max(0.0, (local - start) / (end - start)))
