"""Which trading session a ticker is in now (#250): one answer for the quote board.

    pre | regular | post | overnight | closed

- regular: the exchange's own session, as desk.live.is_open answers it (the broker calendar first,
  the clock when the calendar is unknown).
- Korean stocks (KST): pre 08:00-09:00 (NXT pre-market from 08:00), post 15:30-20:00 (the KRX
  after-hours auctions 15:40-18:00 and NXT's after-market to 20:00). Anything else, weekends and
  holidays, is closed.
- US stocks and ETFs (New York clock, desk.broker.kis_stream.us_session): pre 04:00-09:30, post
  16:00-20:00, overnight 20:00-04:00 (the day market, Sunday night to Friday morning). The overnight
  session belongs to the next trading day: Sunday night trades for Monday, so a Monday holiday
  closes it too.
- Everything else (indices, futures, FX, crypto) is regular while desk.live.is_open says open, else
  closed - there is no off-hours board price to look for.

A holiday is the broker calendar's (desk.live.Calendar.trading_day); an unknown calendar leaves the
clock in charge, as desk.live does.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from . import live, market_calendar
from .broker import kis_stream

SESSIONS = ("pre", "regular", "post", "overnight", "closed")
KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
KR_PRE = (clock_time(8, 0), clock_time(9, 0))
KR_POST = (clock_time(15, 30), clock_time(20, 0))


def kr_clock(now: datetime) -> str:
    """The Korean session by the clock alone (no holidays): pre / regular / post / closed."""
    kst = now.astimezone(KST)
    if kst.weekday() >= 5:
        return "closed"
    t = kst.time()
    if KR_PRE[0] <= t < KR_PRE[1]:
        return "pre"
    if KR_PRE[1] <= t < KR_POST[0]:
        return "regular"
    if KR_POST[0] <= t < KR_POST[1]:
        return "post"
    return "closed"


def us_trading_day(session: str, now: datetime) -> date:
    """The New York trading day a US off-hours session belongs to (overnight after 20:00 is the next)."""
    return market_calendar.us_phase_day(session, now)


async def _trading(calendar, market: str, day: date) -> bool:
    """False only when the broker calendar says the day is not a trading day."""
    if calendar is None:
        return True
    known = await calendar.trading_day(market, day)
    return known is not False


async def session(calendar, ticker: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if await live.is_open(calendar, ticker, now):
        return "regular"
    market = kis_stream.market_of(ticker)
    if market == "KR":
        clock = kr_clock(now)
        if clock in ("pre", "post") and await _trading(calendar, "KR", now.astimezone(KST).date()):
            return clock
        return "closed"
    if market == "US":
        clock = kis_stream.us_session(now)
        if clock in ("pre", "post", "overnight") and await _trading(calendar, "US", us_trading_day(clock, now)):
            return clock
        return "closed"
    return "closed"
