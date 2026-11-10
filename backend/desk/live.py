"""The live chart's feed (issue #26): the current quote and the newest 1-minute bars of a ticker,
pushed over SSE while its market is open.

Toss has no push feed, so this polls desk.prices. PriceSource caches quotes (~10 s) and candles
(~30 s) per ticker on the server, so any number of open tabs on the same ticker cost Toss one
call per window — the MARKET_DATA rate limit is the cache's job, not each stream's.

Outside regular hours the stream sends one ``closed`` frame and then only checks the clock: no
quote or candle calls while nothing trades. Crypto (-USD) never closes. For KRX and US tickers the
broker's market calendar (Toss market-calendar KR · US) decides: holidays and shortened or delayed
sessions follow it. When the calendar cannot be read the weekday clock below stands in.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime

from .broker.base import MarketDay
from .estimate import KRX, US, session_of

log = logging.getLogger(__name__)

TICK_SEC = 5.0
# With a KIS websocket trade for the ticker (desk.broker.kis_stream) the stream sends this often;
# the session check and the bars stay on TICK_SEC (bars are cached ~30 s anyway).
FAST_TICK_SEC = 1.0
# While the market is closed the stream fetches nothing, but still re-checks the session this
# often (docs/security.md: a logout ends an open stream within 15 s).
CLOSED_SEC = 15.0
# After this many rounds in a row with neither quote nor bars (a typo ticker, Toss 403 with Yahoo
# down), poll this much slower: failures are not cached, so each round would call out again.
FAIL_ROUNDS = 3
FAILING_SEC = 60.0
BARS = 3            # the newest bars each frame carries: enough to finish the bar that just closed
MAX_STREAMS = 6     # open live streams per session (each one polls)


def market_open(ticker: str, now: datetime | None = None) -> bool:
    """Regular session of the ticker's exchange (KRX for .KS/.KQ, else US); crypto is always open."""
    t = ticker.upper()
    if t.endswith("-USD"):
        return True
    if t.endswith("=X"):   # FX: Sunday 17:00 to Friday 17:00 New York time
        ny = (now or datetime.now(UTC)).astimezone(US.zone)
        day, hour = ny.weekday(), ny.hour
        return not (day == 5 or (day == 4 and hour >= 17) or (day == 6 and hour < 17))
    # Korean indices (^KS11 KOSPI, ^KQ11 KOSDAQ, ^KS200) trade in KRX hours
    session = KRX if t.startswith(("^KS", "^KQ")) else session_of(ticker)
    local = (now or datetime.now(UTC)).astimezone(session.zone)
    if local.weekday() >= 5:
        return False
    opens, closes = session.hours(local.date())
    return opens <= local.time() < closes


CALENDAR_TTL_SEC = 6 * 3600.0     # a day's sessions do not change; the date rollover refetches too
CALENDAR_RETRY_SEC = 600.0        # after a failed read, the clock decides this long
CALENDAR_TIMEOUT_SEC = 5.0


def calendar_market(ticker: str) -> str | None:
    """Which broker calendar governs the ticker: KR (KRX stocks, Korean indices), US (US stocks and
    indices), or None (crypto, FX, futures — they trade outside the exchange calendar)."""
    t = ticker.upper()
    if t.endswith(("-USD", "=X", "=F")):
        return None
    if t.startswith(("^KS", "^KQ")) or t.endswith((".KS", ".KQ")):
        return "KR"
    return "US"


class Calendar:
    """The broker's market calendar, cached per market: one call per market every few hours, not
    one per stream tick. ``is_open`` answers None when the calendar is unknown."""

    def __init__(self, broker, clock=time.monotonic):
        self._broker = broker
        self._clock = clock
        # keyed by (market, the market's own date asked for): whatever came back — even a list
        # without that date — is used until it expires, so a tick never turns into a broker call
        self._days: dict[tuple[str, date], tuple[float, list[MarketDay] | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _cached(self, key):
        hit = self._days.get(key)
        return hit if hit and self._clock() < hit[0] else None

    async def _load(self, market: str, today: date) -> list[MarketDay] | None:
        key = (market, today)
        if hit := self._cached(key):
            return hit[1]
        async with self._locks.setdefault(market, asyncio.Lock()):
            if hit := self._cached(key):
                return hit[1]
            try:
                # bounded: the lock holds every stream of this market, and each re-checks its session
                days = await asyncio.wait_for(self._broker.market_calendar(market, today), CALENDAR_TIMEOUT_SEC)
                ttl = CALENDAR_TTL_SEC if days else CALENDAR_RETRY_SEC   # an empty answer is a failure
            except NotImplementedError:
                days, ttl = None, CALENDAR_TTL_SEC   # the mock broker: the clock decides
            except Exception as exc:  # noqa: BLE001 - incl. timeout: the clock stands in
                log.warning("market calendar %s failed: %s", market, type(exc).__name__ if isinstance(exc, TimeoutError) else exc)
                days, ttl = None, CALENDAR_RETRY_SEC
            if len(self._days) > 16:
                self._days.clear()
            self._days[key] = (self._clock() + ttl, days or None)
            return days or None

    async def is_open(self, market: str, now: datetime | None = None) -> bool | None:
        now = now or datetime.now(UTC)
        today = now.astimezone((KRX if market == "KR" else US).zone).date()
        days = await self._load(market, today)
        day = next((d for d in days or [] if d.day == today), None)
        if day is None:
            # The calendar does not list today: a weekend or holiday when it lists the days around it.
            return False if days and any(d.day < today for d in days) and any(d.day > today for d in days) else None
        if day.opens is None or day.closes is None:
            return False                       # a holiday on the calendar
        return day.opens <= now < day.closes

    async def trading_day(self, market: str, day: date) -> bool | None:
        """Whether ``day`` (the market's own date) trades at all: False on a weekend or holiday the
        calendar shows, None when the calendar does not know (#250: off-hours sessions)."""
        days = await self._load(market, day)
        found = next((d for d in days or [] if d.day == day), None)
        if found is None:
            return False if days and any(d.day < day for d in days) and any(d.day > day for d in days) else None
        return found.opens is not None and found.closes is not None


async def is_open(calendar: Calendar | None, ticker: str, now: datetime | None = None) -> bool:
    """The stream's open/closed: the broker calendar when it knows the day, else ``market_open``."""
    market = calendar_market(ticker)
    if calendar is not None and market is not None:
        known = await calendar.is_open(market, now)
        if known is not None:
            return known
    return market_open(ticker, now)


def watch(prices, ticker: str) -> bool:
    """Ask the KIS stream for the ticker; True when its trades are already coming in."""
    stream = getattr(prices, "stream", None)
    if stream is None:
        return False
    stream.want(ticker)
    return stream.latest(ticker) is not None


async def frame(prices, ticker: str, bars: bool = True) -> dict:
    """{"state": "open", "quote", "bars"} — either part may be missing when its source failed.
    ``bars=False`` sends the quote only (the fast KIS rounds between bar refreshes)."""
    out: dict = {"state": "open", "ticker": ticker}
    # The page gets a fixed word; the details (upstream URLs, hosts) stay in the server log.
    try:
        out["quote"] = (await prices.quote(ticker)).to_dict()
    except Exception as exc:  # noqa: BLE001 - the next tick tries again
        log.warning("live quote for %s failed: %s", ticker, exc)
        out["quote_error"] = "unavailable"
    if not bars:
        return out
    try:
        candles = await prices.candles(ticker, "1m", 30)
        out["bars"] = candles["bars"][-BARS:]
        out["source"] = candles["source"]
    except Exception as exc:  # noqa: BLE001
        log.warning("live bars for %s failed: %s", ticker, exc)
        out["bars_error"] = "unavailable"
    return out
