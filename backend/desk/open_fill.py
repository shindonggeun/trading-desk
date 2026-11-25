"""#874 the favorites' spark bars, read just before their market opens: the first look at the open finds them.

The dashboard's first look at a market's open (09:00 Seoul, 09:30 New York) read every bar cold. The prewarm
passes (desk.prewarm, every INTERVAL_SEC) cannot hold bars for it: they live 30 s (desk.prices CANDLE_TTL_SEC)
and a batch takes them up to 90 s past that (desk.api BATCH_STALE_SEC), while a pass's read may be up to a whole
interval old at the open - and the levels and charts never take old bars. So a pass only plans: each market of
the favorites that opens later today (by its calendar: no plan on a weekend or a holiday) gets one plan, which
waits until LEAD_SEC before that open, reads the favorites again and reads their spark bars (FavoritesPanel.tsx:
5m 78). At the open they are seconds old: fresh, and read again behind the open's first asks once past
REFRESH_AHEAD of their TTL. The day's first pass plans the day's opens, so a slow pass never misses one; the
plan waits beside the passes (desk.prewarm ``aside``), never inside one, and a pass's time stays its jobs' own.

Not filled here: the market board's bars (its tiles are each browser's own list, not known to the server); the
favorites' daily closes (the prewarm's board job keeps them, 300 s ahead); quotes (10 s: gone before the open).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from . import market_calendar, pools
from .estimate import session_of
from .prices import CRYPTO_SUFFIXES

log = logging.getLogger(__name__)

# Read this long before the open: the reads (PARALLEL at a time, a source's 0.2-1 s each - 24 favorites in 1-6 s)
# end before it, and at the open the bars are at most this old - fresh for the open's first 18 s of their 30 s
LEAD_SEC = 12.0
PARALLEL = 4                 # reads at once - REFRESH_PARALLEL's share of the source threads, not a burst's
SPARK = ("5m", 78)           # FavoritesPanel.tsx: batch candles 5m 78
TICKERS_MAX = 30             # FavoritesPanel.tsx MAX_QUOTES


def next_open(ticker: str, now: datetime) -> datetime | None:
    """Today's open of the ticker's market while still ahead - None on a day its calendar keeps it closed, and for
    markets that never open into a burst: CME futures (~23 h), crypto, FX."""
    t = ticker.upper()
    if t.endswith((*CRYPTO_SUFFIXES, "=F", "=X", ".NYB")):
        return None
    session = session_of(t)
    local = now.astimezone(session.zone)
    day = local.date()
    if day.weekday() >= 5 or (session.market and not market_calendar.is_open(session.market, day)):
        return None
    opens, _closes = session.hours(day)
    at = datetime.combine(day, opens, session.zone)
    return at if at > now else None


class OpenFill:
    def __init__(self, candles: Callable[..., Awaitable[dict]], tickers: Callable[[int], list[str]],
                 now: Callable[[], datetime] = lambda: datetime.now(UTC), sleep=asyncio.sleep) -> None:
        self._candles = candles          # desk.prices.PriceSource.candles
        self._tickers = tickers          # the favorites, at most so many (a DB read: desk.favorites)
        self._now = now
        self._sleep = sleep
        self._planned: set[datetime] = set()     # opens a plan has taken (one read each, however many passes)

    async def plan(self) -> int:
        """One pass's plan, beside it: each of today's opens no earlier plan took - wait until LEAD_SEC before
        it, read the favorites then and their spark bars. How many were read."""
        now = self._now()
        # the favorites (a DB read) and their calendars (a year's holidays are built on first use): off the loop
        opens = await pools.run(pools.PREWARM, self._opens, now)
        due = sorted(at for at in opens if at not in self._planned)
        self._planned = {at for at in self._planned if at > now} | set(due)
        done = 0
        for i, at in enumerate(due):
            try:
                wait = (at - timedelta(seconds=LEAD_SEC) - self._now()).total_seconds()
                if wait > 0:
                    await self._sleep(wait)
                tickers = await pools.run(pools.PREWARM, self._opening, at, self._now())   # the favorites by then
            except Exception:
                # #874 review: this open and the later ones were never read - the next pass plans them again
                self._planned.difference_update(due[i:])
                raise
            done += await self._fill(at, tickers)
        return done

    def _opens(self, now: datetime) -> set[datetime]:
        return {at for t in self._tickers(TICKERS_MAX) if (at := next_open(t, now)) is not None}

    def _opening(self, at: datetime, now: datetime) -> list[str]:
        return [t for t in self._tickers(TICKERS_MAX) if next_open(t, now) == at]

    async def _fill(self, at: datetime, tickers: list[str]) -> int:
        gate = asyncio.Semaphore(PARALLEL)

        async def one(ticker: str) -> bool:
            async with gate:
                try:
                    await self._candles(ticker, *SPARK)
                except Exception as exc:  # noqa: BLE001 - that ticker only: the open's first look reads it
                    log.info("open fill %s failed: %s", ticker, type(exc).__name__)
                    return False
                return True

        got = sum(await asyncio.gather(*(one(t) for t in tickers)))
        log.info("open fill: %d of %d favorites' spark bars before the %s open", got, len(tickers),
                 at.isoformat(timespec="minutes"))
        return got
