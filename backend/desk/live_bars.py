"""The analysis chart's last bar and price, live (#653).

The chart's bars come from desk.prices (Toss for 1m / 1d, else Yahoo - Yahoo runs 10-20 minutes late for
KRX and CME futures; KRX minute bars already get their late tail rebuilt from Toss 1m, #379). Past bars
stay as they are; only the last one is carried forward with the price now (``live_price``), on the board's
rules (desk.api /api/estimates, #250 · #283):

- NXT's trade while it runs (the estimator's ``nxt`` state); in the regular session the live quote - KIS's
  websocket trade, else Toss, else Yahoo (desk.prices.quote; a chart that asks puts the ticker on the KIS
  stream) - except a CME future, whose Yahoo quote is late: the Binance estimate;
- pre / post / overnight: only a real off-hours trade (``off_hours``: KRX - NXT, then the KRX's own after-hours
  auction; US - a KIS trade, then a Toss price that has moved off the close), else the Binance estimate;
- closed: the Binance estimate (own contract, a proxy or a leveraged base), marked ``estimated``.
- No trade and no estimate: the last price there is, ``live`` false - it never opens or moves a bar.

The bar (``merge``): the price falls in the last bar's bucket - its close moves, its high / low widen; in a
later bucket - a new bar opens at it. Intraday buckets carry on the last bar's grid (``at`` + k x the step)
within a day; on a new day they start from the market's open (KRX 09:00, the US 09:30 - a weekend or a
daylight-saving switch would shift a 120m / 240m grid). Days, weeks, months and years go by the bar's own
date (desk.market_calendar.bar_day: a Toss US daily bar is stamped at UTC midnight, Yahoo's at New York
midnight) and a new one keeps the last bar's stamp form. An estimate never rewrites a real bar: in the same
bucket it is the price only (``price_only``).

Channels, trendlines, gaps and the trade map keep reading finished bars (#611); the live bar is for the eye.
``LiveBars.get`` answers from a short cache (``TTL_SEC``) and asks the sources at most once per key at a
time, so a page polling every few seconds costs the brokers little; the base bars come from desk.prices'
own cache (desk.prices._CANDLE_TTL). ``delay_sec`` is the price's age, plus the feed's own lag for Yahoo.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .market_calendar import bar_day
from .ttl_cache import TTLCache

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
TTL_SEC = 2.0                 # one answer per ticker and interval this long, whoever asks
CACHE_MAX = 256
BASE_COUNT = 5                # the last few bars are enough to carry the last one on
STEP_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "120m": 7200, "240m": 14400}
OPEN = {"krx": time(9, 0), "us": time(9, 30)}
OFF_HOURS = ("pre", "post", "overnight")
# What a chart series lags by (seconds), by where its bars came from - shown, not used for anything
YAHOO_LAG = {"krx": 1200, "futures": 600, "us": 0, "other": 0}

OffHours = Callable[[str, str, float | None], Awaitable[dict | None]]


def _kind(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")) or t.startswith(("^KS", "^KQ")):
        return "krx"
    if t.endswith("=F"):
        return "futures"
    if t.endswith(("-USD", "=X")) or t.startswith("^"):
        return "other"
    return "us"


def market_tz(ticker: str) -> ZoneInfo:
    kind = _kind(ticker)
    if kind == "krx":
        return KST
    return ZoneInfo("UTC") if ticker.upper().endswith("-USD") else ET


def series_delay(source: str, ticker: str) -> int:
    """About how many seconds a chart series from ``source`` runs behind (the late Yahoo feeds)."""
    return YAHOO_LAG[_kind(ticker)] if source == "yahoo" else 0


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


def place(last_iso: str, now: datetime, interval: str, ticker: str) -> datetime | None:
    """Where ``now`` falls against the last bar (stamped ``last_iso``): None in the same bucket, else the
    start of its bucket. Raises ValueError when ``now`` is before the last bar (a clock that disagrees)."""
    last_at = _at(last_iso)
    kind, tz = _kind(ticker), market_tz(ticker)
    if interval in STEP_SEC:
        if now < last_at:
            raise ValueError("the price is older than the last bar")
        step = STEP_SEC[interval]
        anchor = last_at
        today = now.astimezone(tz).date()
        if kind in OPEN and last_at.astimezone(tz).date() != today:
            anchor = datetime.combine(today, OPEN[kind], tz).astimezone(UTC)   # a new day: from its open
        k = math.floor((now - anchor).total_seconds() / step)
        start = anchor + timedelta(seconds=k * step)
        return None if start == last_at else start
    last_day = bar_day(last_iso, kind == "krx")
    today = now.astimezone(tz).date()
    if today < last_day:
        raise ValueError("the price is older than the last bar")
    if _period(last_day, interval) == _period(today, interval):
        return None
    # the new bar keeps the last one's stamp form (Toss: UTC midnight, Yahoo: local midnight)
    return last_at + timedelta(days=(_period_start(today, interval) - last_day).days)


def merge(bars: list[dict], price: float, now: datetime, interval: str, ticker: str,
          estimated: bool = False, live: bool = True) -> tuple[dict | None, str]:
    """The live last bar and what happened: ``updated`` (the last bar carried on), ``appended`` (a new bar
    opened), ``price_only`` (no trade or estimate behind the price, an estimate in a real bar's bucket, or a
    price older than the last bar)."""
    if not bars:
        return None, "price_only"
    last = bars[-1]
    if not live and not estimated:
        return dict(last), "price_only"
    try:
        start = place(last["at"], now, interval, ticker)
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


def _row(price, source, at, session, estimated=False, live=True) -> dict:
    return {"price": price, "source": source, "at": at, "session": session, "estimated": estimated, "live": live}


async def live_price(ticker: str, prices, estimator, session_of, watch=None, off_hours: OffHours | None = None,
                     now: datetime | None = None) -> dict:
    """{price, source, at, session, estimated, live} - the price a chart's last bar moves with now."""
    est = None
    try:
        est = await estimator.estimate(ticker) if estimator is not None else None
    except Exception as exc:  # noqa: BLE001 - Binance or Yahoo down for this one: the quote may still do
        log.info("live bars: no estimate for %s: %s", ticker, type(exc).__name__)
    session = await session_of(ticker)
    if est is not None and est.market_state == "nxt":
        if session not in ("pre", "post"):
            hour = (now or datetime.now(UTC)).astimezone(KST).hour
            session = "pre" if hour < 9 else "post"
        return _row(est.estimate, "nxt", est.fetched_at, session)
    if est is not None and est.market_state == "open":
        session = "regular"
    if session == "regular" and not (ticker.upper().endswith("=F") and est is not None):
        if watch is not None:
            watch(ticker)                      # the KIS stream takes it up for the next asks
        q = await prices.quote(ticker)
        return _row(q.price, q.source, q.fetched_at, session)
    if session in OFF_HOURS and off_hours is not None:
        got = await off_hours(ticker, session, est.last_close if est is not None else None)
        if got is not None:
            return _row(got["price"], got["source"], got["at"], session)
    if est is not None:
        return _row(est.estimate, "binance-estimate", est.fetched_at, session, estimated=True)
    q = await prices.quote(ticker)            # nothing trades and no contract: the last price there is
    return _row(q.price, q.source, q.fetched_at, session, live=False)


class LiveBars:
    """GET /api/candles/live: the last bar and price of a chart, cached TTL_SEC, one fetch per key at a time."""

    def __init__(self, prices, estimator, session_of, watch=None, off_hours: OffHours | None = None,
                 clock=None, now=None) -> None:
        self.prices, self.estimator, self.session_of = prices, estimator, session_of
        self.watch, self.off_hours = watch, off_hours
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
        now = self._now()
        live = await live_price(ticker, self.prices, self.estimator, self.session_of, self.watch, self.off_hours, now)
        bar, state = merge(base.get("bars") or [], live["price"], now, interval, ticker, live["estimated"],
                           live["live"])
        try:
            age = max(0, round((now - _at(live["at"])).total_seconds()))
            age += series_delay(live["source"], ticker)          # a Yahoo quote is late by its feed's lag
        except (TypeError, ValueError):
            age = None
        return {"ticker": ticker, "interval": interval, "bar": bar, "state": state, "price": live["price"],
                "source": live["source"], "estimated": live["estimated"], "live": live["live"],
                "session": live["session"], "price_at": live["at"], "delay_sec": age,
                "series_source": base.get("source"), "fetched_at": now.isoformat(timespec="seconds")}
