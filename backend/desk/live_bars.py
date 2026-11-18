"""The analysis chart's last bar and price, live (#653).

The chart's bars come from desk.prices (Toss for 1m / 1d, else Yahoo - Yahoo runs 10-20 minutes late for
KRX and CME futures; KRX minute bars already get their late tail rebuilt from Toss 1m, #379). Past bars
stay as they are; only the last one is carried forward with the price now:

- the price (``live_price``): NXT's last trade while it runs (KRX stocks 08:00-08:50, 15:40-20:00); in a
  session (regular, pre, post, overnight) the live quote - KIS's websocket trade, else Toss, else Yahoo
  (desk.prices.quote; a chart that asks puts the ticker on the KIS stream); a CME future, whose Yahoo quote
  is late, and any market closed: the Binance estimate (#250's rules: own contract, a proxy or a leveraged
  base), marked ``estimated``.
- the bar (``merge``): the price falls in the last bar's bucket - its close moves, its high / low widen; in
  a later bucket - a new bar opens at it. Buckets follow the last bar's own grid (``at`` + k x the step),
  so KRX's 09:00 and the US's 09:30 grids carry on; days, weeks, months and years by the market's local
  date. An estimate never rewrites a real bar: in the same bucket it is the price only.

Channels, trendlines, gaps and the trade map keep reading finished bars (#611); the live bar is for the
eye. ``LiveBars.get`` answers from a short cache (``TTL_SEC``) and asks the sources at most once per key at
a time, so a page polling every few seconds costs the brokers little; the base bars come from desk.prices'
own cache (desk.prices._CANDLE_TTL).
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .ttl_cache import TTLCache

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
TTL_SEC = 2.0                 # one answer per ticker and interval this long, whoever asks
CACHE_MAX = 256
BASE_COUNT = 5                # the last few bars are enough to carry the last one on
STEP_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "120m": 7200, "240m": 14400}
LIVE_SESSIONS = ("regular", "pre", "post", "overnight")
# What a chart series lags by (seconds), by where its bars came from - shown, not used for anything
YAHOO_LAG = {"krx": 1200, "futures": 600, "other": 0}


def market_tz(ticker: str) -> ZoneInfo:
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")) or t.startswith(("^KS", "^KQ")):
        return KST
    if t.endswith("-USD"):
        return ZoneInfo("UTC")
    return ET


def _kind(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")) or t.startswith(("^KS", "^KQ")):
        return "krx"
    if t.endswith("=F"):
        return "futures"
    return "other"


def series_delay(source: str, ticker: str) -> int:
    """About how many seconds a chart series from ``source`` runs behind (the late Yahoo feeds)."""
    if source == "yahoo":
        return YAHOO_LAG[_kind(ticker)]
    return 0


def _at(iso: str) -> datetime:
    at = datetime.fromisoformat(iso)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _period(d: date, interval: str) -> tuple:
    if interval == "1w":
        return d.isocalendar()[:2]
    if interval == "1M":
        return (d.year, d.month)
    if interval == "1y":
        return (d.year,)
    return (d,)


def _period_start(d: date, interval: str) -> date:
    if interval == "1w":
        return d - timedelta(days=d.weekday())
    if interval == "1M":
        return d.replace(day=1)
    if interval == "1y":
        return d.replace(month=1, day=1)
    return d


def place(last_at: datetime, now: datetime, interval: str, tz: ZoneInfo) -> datetime | None:
    """Where ``now`` falls against the last bar: None in the same bucket, else the start of its bucket.
    Raises ValueError when ``now`` is before the last bar (a clock that disagrees)."""
    if now < last_at:
        raise ValueError("the price is older than the last bar")
    if interval in STEP_SEC:
        k = math.floor((now - last_at).total_seconds() / STEP_SEC[interval])
        return None if k == 0 else last_at + timedelta(seconds=k * STEP_SEC[interval])
    last_day, today = last_at.astimezone(tz).date(), now.astimezone(tz).date()
    if _period(last_day, interval) == _period(today, interval):
        return None
    return datetime.combine(_period_start(today, interval), time(0), tz).astimezone(UTC)


def merge(bars: list[dict], price: float, now: datetime, interval: str, tz: ZoneInfo,
          estimated: bool = False) -> tuple[dict | None, str]:
    """The live last bar and what happened: ``updated`` (the last bar carried on), ``appended`` (a new bar
    opened), ``price_only`` (an estimate in a real bar's bucket, or a price older than the last bar)."""
    if not bars:
        return None, "price_only"
    last = bars[-1]
    try:
        start = place(_at(last["at"]), now, interval, tz)
    except ValueError:
        return dict(last), "price_only"
    if start is None:
        if estimated and not last.get("estimated"):
            return dict(last), "price_only"
        bar = {**last, "close": price, "high": max(last["high"], price), "low": min(last["low"], price)}
        if estimated:
            bar["estimated"] = True
        return bar, "updated"
    bar = {"at": start.isoformat(timespec="seconds"), "open": price, "high": price, "low": price, "close": price,
           "volume": 0.0}
    if estimated:
        bar["estimated"] = True
    return bar, "appended"


async def live_price(ticker: str, prices, estimator, session_of, watch=None) -> dict:
    """{price, source, at, estimated, session} - the price a chart's last bar moves with now."""
    est = None
    try:
        est = await estimator.estimate(ticker) if estimator is not None else None
    except Exception as exc:  # noqa: BLE001 - Binance or Yahoo down for this one: the quote may still do
        log.info("live bars: no estimate for %s: %s", ticker, type(exc).__name__)
    if est is not None and est.market_state == "nxt":
        return {"price": est.estimate, "source": "nxt", "at": est.fetched_at, "estimated": False, "session": "post"}
    session = await session_of(ticker)
    if est is not None and est.market_state == "open":
        session = "regular"
    future = ticker.upper().endswith("=F")
    if session in LIVE_SESSIONS and not (future and est is not None):
        if watch is not None:
            watch(ticker)                      # the KIS stream takes it up for the next asks
        q = await prices.quote(ticker)
        return {"price": q.price, "source": q.source, "at": q.fetched_at, "estimated": False, "session": session}
    if est is not None:
        return {"price": est.estimate, "source": "binance-estimate", "at": est.fetched_at, "estimated": True,
                "session": session}
    q = await prices.quote(ticker)            # closed and no contract: the last price there is
    return {"price": q.price, "source": q.source, "at": q.fetched_at, "estimated": False, "session": session}


class LiveBars:
    """GET /api/candles/live: the last bar and price of a chart, cached TTL_SEC, one fetch per key at a time."""

    def __init__(self, prices, estimator, session_of, watch=None, clock=None, now=None) -> None:
        self.prices, self.estimator, self.session_of, self.watch = prices, estimator, session_of, watch
        self._now = now or (lambda: datetime.now(UTC))
        self._cache: TTLCache[tuple[str, str], dict] = (TTLCache(CACHE_MAX, TTL_SEC, clock=clock) if clock
                                                         else TTLCache(CACHE_MAX, TTL_SEC))
        self._inflight: dict[tuple[str, str], asyncio.Future] = {}

    async def get(self, ticker: str, interval: str) -> dict:
        key = (ticker, interval)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        pending = self._inflight.get(key)
        if pending is not None and pending.get_loop() is asyncio.get_running_loop():
            return await asyncio.shield(pending)
        task = asyncio.ensure_future(self._build(ticker, interval))
        self._inflight[key] = task

        def done(t: asyncio.Future) -> None:
            if self._inflight.get(key) is t:
                self._inflight.pop(key, None)
            if not t.cancelled():
                t.exception()

        task.add_done_callback(done)
        out = await asyncio.shield(task)
        self._cache.put(key, out)
        return out

    async def _build(self, ticker: str, interval: str) -> dict:
        base = await self.prices.candles(ticker, interval, BASE_COUNT)
        live = await live_price(ticker, self.prices, self.estimator, self.session_of, self.watch)
        now = self._now()
        bar, state = merge(base.get("bars") or [], live["price"], now, interval, market_tz(ticker), live["estimated"])
        try:
            age = max(0, round((now - _at(live["at"])).total_seconds()))
        except (TypeError, ValueError):
            age = None
        return {"ticker": ticker, "interval": interval, "bar": bar, "state": state, "price": live["price"],
                "source": live["source"], "estimated": live["estimated"], "session": live["session"],
                "price_at": live["at"], "delay_sec": age, "series_source": base.get("source"),
                "fetched_at": now.isoformat(timespec="seconds")}
