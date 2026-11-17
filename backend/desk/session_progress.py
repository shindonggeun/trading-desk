"""How far today's regular session has run (#432): the clock of a live plan, not a plan rule.

A live plan's last daily bar can be the session still trading - or one not open yet (a US bar that
carries pre-market trades). ``elapsed`` says how much of that session has passed, so desk.setup_filter
does not hold a part-day's volume against a whole day's average. The backtest never calls this (its
bars are finished sessions), so desk.backtest_reports.DATA_MODULES leaves it out of the rule fingerprint.

- The bar's day is the market's own, as desk.sr._local_day reads it: a Korean bar opens at local
  midnight (15:00 UTC the day before), a US bar carries its own date in UTC (Yahoo's 04:00/05:00 UTC,
  a broker's date-only bar as UTC midnight).
- Korean (.KS/.KQ) and US stocks and ETFs, and (#559) the Korean and US indices on their market's
  session, CME futures (=F) on their own day (18:00 ET the evening before to 17:00 ET) and crypto
  (-USD) on the UTC day. FX (=X) has no volume worth judging, and other indices (another market's
  session): None, the volume read as it is.
- A US early close (13:00 ET, desk.market_calendar.us_early_close) ends the session there.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time

from . import market_calendar
from .estimate import KRX, US

US_EARLY_CLOSE = clock_time(13, 0)
# #559 the chart's volume tag and the entry summary read NQ=F's day so far against whole days
US_INDICES = frozenset({"^GSPC", "^NDX", "^IXIC", "^DJI", "^SOX", "^RUT"})
FUTURES_OPENS = clock_time(18, 0)    # CME Globex: the session dated D runs 18:00 ET on D-1 ...
FUTURES_CLOSES = clock_time(17, 0)   # ... to 17:00 ET on D (equity index futures; a holiday is not modelled)


def _korean(ticker: str) -> bool:
    t = ticker.upper()
    return t.endswith((".KS", ".KQ")) or t.startswith(("^KS", "^KQ"))   # ^KS11 · ^KQ11 · ^KS200


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
    if not daily or not daily[-1].get("at") or t.endswith("=X"):
        return None
    if t.endswith("=F"):
        return _futures(t, daily, now)
    if t.endswith("-USD"):
        return _utc_day(t, daily, now)
    if _korean(t):
        session = KRX
    elif t.startswith("^") and t not in US_INDICES:
        return None
    else:
        session = US
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


def _futures(t: str, daily: list[dict], now: datetime | None) -> float | None:
    """#559 a CME futures day: the bar dated D trades from 18:00 ET on D-1 to 17:00 ET on D (after
    17:00 the next day's session is the one trading; none is dated a Saturday or a Sunday)."""
    zone = US.zone
    local = (now or datetime.now(UTC)).astimezone(zone)
    day = local.date() if local.time() < FUTURES_CLOSES else local.date() + timedelta(days=1)
    if day.weekday() >= 5:
        return None
    try:
        if bar_day(daily[-1]["at"], t) != day:
            return None
    except (TypeError, ValueError):
        return None
    start = datetime.combine(day - timedelta(days=1), FUTURES_OPENS, zone)
    end = datetime.combine(day, FUTURES_CLOSES, zone)
    return min(1.0, max(0.0, (local - start) / (end - start)))


def _utc_day(t: str, daily: list[dict], now: datetime | None) -> float | None:
    """#559 crypto trades around the clock; its daily bar is the UTC day."""
    local = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        if bar_day(daily[-1]["at"], t) != local.date():
            return None
    except (TypeError, ValueError):
        return None
    start = datetime.combine(local.date(), clock_time(0, 0), UTC)
    return (local - start) / timedelta(days=1)
