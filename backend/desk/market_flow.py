"""KOSPI / KOSDAQ trading value by investor class (individual, foreign, institution, other
corporations), from the broker's market data (Toss Market Indicators group, read-only).

Toss gives buy and sell per class; this adds net = buy - sell and marks the rows that may still
change. It caches per (market, interval): Toss is always asked for the full 100 records and each
``count`` is cut from that, so no request parameter multiplies the upstream calls.

- ``in_progress``: the record's period includes today (same day / ISO week / month / year), so
  later trading days still add to it.
- ``provisional``: Toss last updated the record today before the evening confirmation
  (CONFIRMED_AFTER). A record last updated on an earlier day is final for the days it covers,
  which keeps a confirmed week from turning provisional again over the weekend. The 18:00 mark
  comes from the spec's sample (updatedAt 18:10); check it against a live updatedAt (#76).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta, timezone

from .broker.base import INSTITUTION_KINDS, INVESTORS, Broker, FlowRecord

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
MARKETS = ("KOSPI", "KOSDAQ")
INTERVALS = ("1d", "1w", "1mo", "1y")
MAX_COUNT = 100
# Toss confirms the day's figures in the evening (spec: "확정치는 해당 일자 저녁"; sample updatedAt 18:10).
CONFIRMED_AFTER = (18, 0)
# Fresh enough for an intraday glance; one call per key per window (MARKET_INDICATOR rate limit group).
CACHE_OPEN_SEC = 60.0
CACHE_CLOSED_SEC = 600.0
CACHE_FAIL_SEC = 30.0


def market_open(now: datetime) -> bool:
    """KRX regular session plus the closing auction, weekdays (holidays are treated as open: the
    only effect is a shorter cache)."""
    local = now.astimezone(KST)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return 9 * 60 <= minutes <= 15 * 60 + 40


def _ttl_until_open(now: datetime, ttl: float) -> float:
    """Do not carry a pre-open answer past 09:00 KST on a weekday."""
    local = now.astimezone(KST)
    if local.weekday() >= 5 or local.hour >= 9:
        return ttl
    opens = local.replace(hour=9, minute=0, second=0, microsecond=0)
    return max(1.0, min(ttl, (opens - local).total_seconds()))


def _same_period(day: date, today: date, interval: str) -> bool:
    if interval == "1d":
        return day == today
    if interval == "1w":
        return day.isocalendar()[:2] == today.isocalendar()[:2]
    if interval == "1mo":
        return (day.year, day.month) == (today.year, today.month)
    return day.year == today.year


def _parse_time(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def _side(pair: tuple[int, int]) -> dict:
    buy, sell = pair
    return {"buy": buy, "sell": sell, "net": buy - sell}


def normalize(record: FlowRecord, interval: str, now: datetime) -> dict:
    """One record for the API. ``in_progress``: the period includes today, so later days will
    still add to it. ``provisional``: today's figures are not confirmed yet."""
    local_now = now.astimezone(KST)
    today = local_now.date()
    try:
        day = date.fromisoformat(record.date)
    except ValueError:
        day = None
    updated = _parse_time(record.updated_at)
    in_progress = day is not None and _same_period(day, today, interval)
    if updated is None:
        provisional = in_progress
    else:
        local = updated.astimezone(KST)
        provisional = in_progress and local.date() == today and (local.hour, local.minute) < CONFIRMED_AFTER
    return {
        "date": record.date,
        "updated_at": updated.astimezone(KST).isoformat(timespec="seconds") if updated else None,
        "in_progress": in_progress,
        "provisional": provisional,
        "investors": {name: _side(record.investors[name]) for name in INVESTORS if name in record.investors},
        "institution_breakdown": {kind: _side(record.institution_breakdown[kind])
                                  for kind in INSTITUTION_KINDS if kind in record.institution_breakdown},
    }


def _short_error(exc: Exception) -> str:
    """The class and the first clause only — Toss error bodies stay in the log (as #17 does)."""
    return f"{type(exc).__name__}: {str(exc).split(':')[0]}"


class MarketFlow:
    """Cached market-wide investor flow for the dashboard."""

    def __init__(self, broker: Broker, clock=time.monotonic, now=lambda: datetime.now(UTC)) -> None:
        self.broker = broker
        self._clock = clock
        self._now = now
        self._cache: dict[tuple, tuple[float, float, dict]] = {}   # key -> (stamp, ttl, value)
        self._locks: dict[tuple, asyncio.Lock] = {}

    async def get(self, market: str, interval: str, count: int) -> dict:
        if market not in MARKETS:
            raise ValueError(f"market must be one of {', '.join(MARKETS)}")
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
        count = min(max(count, 1), MAX_COUNT)
        key = (market, interval)                       # at most 8 keys: count is cut from the full page
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:   # concurrent refreshes of one key share a single broker call
            cached = self._cache.get(key)
            if not cached or self._clock() - cached[0] >= cached[1]:
                value, ok = await self._fetch(market, interval)
                now = self._now()
                if not ok:
                    ttl = CACHE_FAIL_SEC
                elif market_open(now):
                    ttl = CACHE_OPEN_SEC
                else:
                    ttl = _ttl_until_open(now, CACHE_CLOSED_SEC)
                cached = (self._clock(), ttl, value)
                self._cache[key] = cached
        value = cached[2]
        return {**value, "records": value["records"][:count]}

    async def _fetch(self, market: str, interval: str) -> tuple[dict, bool]:
        now = self._now()
        base = {"market": market, "interval": interval, "source": self.broker.name,
                "fetched_at": now.astimezone(KST).isoformat(timespec="seconds")}
        flow, index = await asyncio.gather(self.broker.investor_flow(market, interval, MAX_COUNT),
                                           self.broker.index_prices([market]), return_exceptions=True)
        if isinstance(flow, NotImplementedError):
            return {**base, "supported": False, "index": None, "records": [], "error": None}, True
        if isinstance(flow, BaseException):
            log.warning("market flow %s %s failed: %s", market, interval, flow)   # full text stays in the log
            return {**base, "supported": True, "index": None, "records": [],
                    "error": _short_error(flow) if isinstance(flow, Exception) else "failed"}, False
        records, _next_until = flow   # paging past 100 records is not offered (yet)
        level = None
        if isinstance(index, BaseException):
            if not isinstance(index, NotImplementedError):
                log.info("index price %s failed: %s", market, index)   # the flow still shows without it
        else:
            level = next(({"price": p.price, "at": p.at} for p in index if p.symbol == market), None)
        return {**base, "supported": True, "index": level,
                "records": [normalize(r, interval, now) for r in records], "error": None}, True
