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

#673 today's figures can move while Toss' ``updatedAt`` stays put (the card read "09:01 갱신" for 10-30
minutes). So each market's 1d record is watched between fetches: when its figures change, the time is the
previous fetch's (the change happened after it - never fresher than known), and the source time is the
later of that and ``updatedAt``. Every change is one INFO line (``toss flow``) - the source's real cadence
in the server log. ``fresh`` skips the cache (the minute snapshots, desk.futures_history: so the stored
spot figures are the minute's, and the card's cache is refreshed by them).

Before the bell (PREMARKET_FROM ~ 09:00) today's record already carries the trades before the regular
session (the server's snapshots: non-zero 08:45 figures that differ day to day and move again by 09:01 -
the pre-open off-hours and NXT pre-market trades). It is cached as in the session and marked
``session: "pre"`` so the screens can say 프리장.

#676 with a KIS client (``kis``: desk.futures_flow's, one token) today's 1d record comes from KIS first
during the pre-market and the session (desk.kis_spot_flow, ``source: "kis"`` on the record): Toss' history
stays behind it, Toss' own today record is the fallback (KIS failing, empty or disagreeing), and with Toss
failing the KIS record alone still shows (a short-lived answer carrying Toss' error).
#711: off for now (``kis_spot_flow.ENABLED``) - KIS' screen counts a narrower market than Toss' record.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import UTC, date, datetime, timedelta, timezone

from . import freshness, kis_spot_flow, market_bus, shared_cache
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
STREAM_COUNT = 20             # #690 records in a pushed flow event (the card's default count)
SHARED_NS = "market_flow"   # #689 desk.shared_cache: the last good page per (market, interval), {"value", "ttl"}
PREMARKET_FROM = (8, 0)        # NXT pre-market opens 08:00 (KRX pre-open off-hours trades 08:30-08:40)


def market_open(now: datetime) -> bool:
    """KRX regular session plus the closing auction, weekdays (holidays are treated as open: the
    only effect is a shorter cache)."""
    local = now.astimezone(KST)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return 9 * 60 <= minutes <= 15 * 60 + 40


def premarket(now: datetime) -> bool:
    """Weekdays PREMARKET_FROM ~ 09:00 KST: trades before the regular session already count in today's record."""
    local = now.astimezone(KST)
    return local.weekday() < 5 and PREMARKET_FROM <= (local.hour, local.minute) < (9, 0)


def session(now: datetime) -> str:
    """``pre`` · ``regular`` · ``closed`` for the screens."""
    return "regular" if market_open(now) else "pre" if premarket(now) else "closed"


def _ttl_until_open(now: datetime, ttl: float) -> float:
    """Do not carry an early-morning answer past PREMARKET_FROM on a weekday (#673: from then on the
    pre-market is read as the session is)."""
    local = now.astimezone(KST)
    if local.weekday() >= 5 or (local.hour, local.minute) >= PREMARKET_FROM:
        return ttl
    opens = local.replace(hour=PREMARKET_FROM[0], minute=PREMARKET_FROM[1], second=0, microsecond=0)
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


async def _none():
    return None


def _later(a: str | None, b: str | None) -> str | None:
    """The later of two ISO times (either may be missing)."""
    if not a or not b:
        return a or b
    pa, pb = _parse_time(a), _parse_time(b)
    if pa is None or pb is None:
        return a if pa is not None else b
    return a if pa >= pb else b


def _short_error(exc: Exception) -> str:
    """The class and the first clause only — Toss error bodies stay in the log (as #17 does)."""
    return f"{type(exc).__name__}: {str(exc).split(':')[0]}"


def _valid_flow_record(row: object, interval: str, fetched_day: date) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("date"), str):
        return False
    try:
        day = date.fromisoformat(row["date"])
    except ValueError:
        return False
    if day.isoformat() != row["date"] or row.get("in_progress") is not _same_period(day, fetched_day, interval):
        return False
    if type(row.get("provisional")) is not bool or (row["provisional"] and not row["in_progress"]):
        return False
    if "updated_at" not in row or (row["updated_at"] is not None and _parse_time(row["updated_at"]) is None):
        return False
    return all(
        isinstance(sides, dict) and all(
            isinstance(side, dict) and all(type(side.get(field)) is int for field in ("buy", "sell", "net"))
            for side in sides.values()
        )
        for sides in (row.get("investors"), row.get("institution_breakdown"))
    )


def _shared_flow(hit, market: str, interval: str, now: datetime) -> tuple[dict, float, float] | None:
    """Adopt a public page only while its observation and trading date are still usable."""
    latest = shared_cache.latest(hit)
    if latest is None:
        return None
    value, ttl, left = latest
    if (value.get("market") != market or value.get("interval") != interval
            or not isinstance(value.get("source"), str) or type(value.get("supported")) is not bool
            or "error" not in value or value["error"] is not None or not isinstance(value.get("records"), list)):
        return None
    index = value.get("index")
    if "index" not in value or (index is not None and (
        not isinstance(index, dict) or type(index.get("price")) not in (int, float)
        or (type(index["price"]) is float and not math.isfinite(index["price"]))
        or "at" not in index or (index["at"] is not None and _parse_time(index["at"]) is None)
    )):
        return None
    fetched = _parse_time(value.get("fetched_at"))
    changed = value.get("changed_at")
    if fetched is None or (changed is not None and _parse_time(changed) is None) or ttl > CACHE_CLOSED_SEC:
        return None

    age = (now - fetched).total_seconds()
    if age < -shared_cache.SKEW_SEC:
        return None
    left = min(left, max(ttl, shared_cache.LATEST_KEEP_SEC) + shared_cache.SKEW_SEC - age)
    local, seen = now.astimezone(KST), fetched.astimezone(KST)
    if interval == "1d":
        if seen.date() != local.date():
            return None
        deadline = seen.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        pre = seen.replace(hour=PREMARKET_FROM[0], minute=PREMARKET_FROM[1], second=0, microsecond=0)
        if seen.weekday() < 5 and seen < pre:
            deadline = pre
        left = min(left, (deadline - local).total_seconds())
    if left <= 0 or not all(_valid_flow_record(row, interval, seen.date()) for row in value["records"]):
        return None
    return value, ttl, left


class MarketFlow:
    """Cached market-wide investor flow for the dashboard."""

    def __init__(self, broker: Broker, clock=time.monotonic, now=lambda: datetime.now(UTC), kis=None) -> None:
        self.broker = broker
        self.kis = kis                                   # #676 desk.futures_flow.KisRest (shared token) or None
        self._kis_noted: dict[str, str] = {}             # market:why -> the day a KIS fallback was last logged
        self._kis_verified: dict[str, str] = {}          # market -> the day KIS last passed a real check
        self._last_kis: dict[str, tuple[dict, datetime]] = {}   # market -> (last KIS record shown, when)
        self._clock = clock
        self._now = now
        self._cache: dict[tuple, tuple[float, float, dict]] = {}   # key -> (stamp, ttl, value)
        self._locks: dict[tuple, asyncio.Lock] = {}
        # market:source -> (date, updatedAt, figures, fetched at, figures first seen changed at or None)
        self._seen: dict[str, tuple[str, str | None, tuple, datetime, datetime | None]] = {}

    async def get(self, market: str, interval: str, count: int, fresh: bool = False) -> dict:
        if market not in MARKETS:
            raise ValueError(f"market must be one of {', '.join(MARKETS)}")
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
        count = min(max(count, 1), MAX_COUNT)
        key = (market, interval)                       # at most 8 keys: count is cut from the full page
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:   # concurrent refreshes of one key share a single broker call
            cached = self._cache.get(key)
            if not fresh and (not cached or self._clock() - cached[0] >= cached[1]):
                # #689 another process (the collector's snapshot job reads fresh) may have it already
                hit = _shared_flow(await shared_cache.aget(SHARED_NS, key), market, interval, self._now())
                if hit is not None:
                    shared, ttl, left = hit
                    cached = self._cache[key] = (self._clock() - max(0.0, ttl - left), ttl, shared)
            if fresh or not cached or self._clock() - cached[0] >= cached[1]:
                value, ok = await self._fetch(market, interval)
                now = self._now()
                if not ok:
                    ttl = CACHE_FAIL_SEC
                elif market_open(now) or premarket(now):
                    ttl = CACHE_OPEN_SEC
                else:
                    ttl = _ttl_until_open(now, CACHE_CLOSED_SEC)
                got = (self._clock(), ttl, value)
                # a failed fresh read (the snapshot's) answers its caller but leaves a good cache to the card
                if ok or not fresh or not cached or cached[2].get("error") or not cached[2].get("records"):
                    self._cache[key] = got
                if ok:   # a failure answers this caller only - the other processes keep the last good one
                    await shared_cache.aput(SHARED_NS, key, {"value": value, "ttl": ttl}, max(ttl, shared_cache.LATEST_KEEP_SEC))
                    # #690 and to the open market streams, as the card would answer it
                    pushed = self._answer(value, ttl, interval, STREAM_COUNT)
                    first = (value.get("records") or [None])[0] or {}
                    market_bus.publish("flow", f"{market}:{interval}", pushed,
                                       "kis_spot_investors" if first.get("source") == "kis" else "toss_investors",
                                       pushed["freshness"]["source_updated_at"])
                cached = got
        return self._answer(cached[2], cached[1], interval, count)

    def _answer(self, value: dict, ttl: float, interval: str, count: int) -> dict:
        now = self._now()
        latest = (value.get("records") or [None])[0] or {}
        # #645 how old the figures are: Toss stamps today's record with its own update time;
        # #673 or later, when the figures were seen to change after it
        today = interval == "1d" and latest.get("date") == now.astimezone(KST).date().isoformat()
        stamp = freshness.stamp(value.get("fetched_at"), now, ttl, live=session(now) != "closed",
                                source_at=_later(latest.get("updated_at"), value.get("changed_at")) if today else None,
                                source="kis_spot_investors" if latest.get("source") == "kis" else "toss_investors")
        return {**value, "records": value["records"][:count], "freshness": stamp, "session": session(now)}

    def _watch(self, market: str, record: dict, now: datetime) -> str | None:
        """#673 when today's figures were last seen to change (the fetch before the change, KST ISO), and
        an INFO line on each change of the figures or of ``updatedAt``."""
        figures = tuple(sorted((k, v["net"]) for k, v in (record.get("investors") or {}).items()))
        day, updated = record.get("date"), record.get("updated_at")
        source = record.get("source") or "toss"
        market_key = f"{market}:{source}"                # a source switch is not a change of the figures
        before = self._seen.get(market_key)
        changed_at = before[4] if before and before[0] == day else None
        if before and before[0] == day and (before[1], before[2]) != (updated, figures):
            moved = before[2] != figures
            if moved:
                gap = f"+{(before[3] - before[4]).total_seconds():.0f}s" if before[4] else "first"
                changed_at = before[3]
            else:
                gap = "-"
            log.info("%s flow %s: updatedAt %s figures %s (since last change %s)", source, market,
                     updated[11:19] if updated else "-", "changed" if moved else "same", gap)
        self._seen[market_key] = (day, updated, figures, now, changed_at)
        return changed_at.astimezone(KST).isoformat(timespec="seconds") if changed_at else None

    def _kis_record(self, market: str, answer, today: str, now: datetime) -> dict | None:
        """#676 today's KIS record, or None (failed or empty: noted once a day, Toss then serves)."""
        if isinstance(answer, BaseException):
            why = "timed out" if isinstance(answer, TimeoutError) else (
                _short_error(answer) if isinstance(answer, Exception) else "failed")
            self._note(market, today, f"failed: {why}")
            return None
        got = kis_spot_flow.record((answer or {}).get("output"), today)
        if got is None and session(now) == "regular":      # before the bell nothing traded yet is normal
            self._note(market, today, "no figures in the answer")
        return got

    def _held(self, market: str, today: str, now: datetime) -> dict | None:
        """The last KIS record shown, for HOLD_SEC after it (``held``), when KIS fails in between."""
        last = self._last_kis.get(market)
        if last and last[0]["date"] == today and (now - last[1]).total_seconds() <= kis_spot_flow.HOLD_SEC:
            return {**last[0], "held": True}
        return None

    def _changed_at(self, market: str, source: str) -> str | None:
        """When the figures of ``source`` were last seen to change (no new observation)."""
        seen = self._seen.get(f"{market}:{source}")
        return seen[4].astimezone(KST).isoformat(timespec="seconds") if seen and seen[4] else None

    def _note(self, market: str, today: str, why: str) -> None:
        """One INFO line a market a day for each kind of KIS fallback (each failure would be one a minute)."""
        key = f"{market}:{why.split()[0]}"               # a failure and a disagreement are noted apart
        if self._kis_noted.get(key) != today:
            self._kis_noted[key] = today
            log.info("kis spot flow %s: %s - Toss serves today's record", market, why)

    async def _fetch(self, market: str, interval: str) -> tuple[dict, bool]:
        now = self._now()
        base = {"market": market, "interval": interval, "source": self.broker.name,
                "fetched_at": now.astimezone(KST).isoformat(timespec="seconds")}
        today = now.astimezone(KST).date().isoformat()
        use_kis = (kis_spot_flow.ENABLED and interval == "1d" and self.kis is not None    # #711 off: scope
                   and self.broker.name != "mock"                                        # no real figures
                   and market in kis_spot_flow.CODES and kis_spot_flow.hours(now))          # over the sample
        flow, index, kis = await asyncio.gather(
            self.broker.investor_flow(market, interval, MAX_COUNT), self.broker.index_prices([market]),
            asyncio.wait_for(kis_spot_flow.fetch(self.kis, market), kis_spot_flow.TIMEOUT_SEC) if use_kis else _none(),
            return_exceptions=True)
        if isinstance(flow, NotImplementedError):
            return {**base, "supported": False, "index": None, "records": [], "error": None}, True
        kis_row = self._kis_record(market, kis, today, now) if use_kis else None
        if isinstance(flow, BaseException):
            log.warning("market flow %s %s failed: %s", market, interval, flow)   # full text stays in the log
            error = _short_error(flow) if isinstance(flow, Exception) else "failed"
            # #676 Toss down: today's KIS figures still show (only once KIS passed a real check today -
            # nothing to check them against now), retried soon
            if kis_row is not None and self._kis_verified.get(market) == today:
                self._last_kis[market] = (kis_row, now)
                return {**base, "supported": True, "index": None, "records": [kis_row], "error": error,
                        "changed_at": self._watch(market, kis_row, now)}, False
            return {**base, "supported": True, "index": None, "records": [], "error": error}, False
        records, _next_until = flow   # paging past 100 records is not offered (yet)
        level = None
        if isinstance(index, BaseException):
            if not isinstance(index, NotImplementedError):
                log.info("index price %s failed: %s", market, index)   # the flow still shows without it
        else:
            level = next(({"price": p.price, "at": p.at} for p in index if p.symbol == market), None)
        rows = [normalize(r, interval, now) for r in records]
        toss_today = rows[0] if interval == "1d" and rows and rows[0]["date"] == today else None
        # Toss' today record is watched even under KIS (its ``toss flow`` cadence lines, #673)
        changed_at = self._watch(market, toss_today, now) if toss_today else None
        held = None
        if kis_row is not None:
            toss_before = next((r for r in rows if r["date"] != today), None)
            why = kis_spot_flow.check(kis_row, toss_today, toss_before, premarket(now))
            if why:                          # a wrong code, unit or day: Toss, and no held KIS either
                self._note(market, today, f"not shown: {why}")
                self._last_kis.pop(market, None)
                kis_row = None
            elif kis_spot_flow.verified(kis_row, toss_today) and self._kis_verified.get(market) != today:
                self._kis_verified[market] = today
                log.info("kis spot flow %s: individual buy KIS %s vs Toss %s - shown", market,
                         kis_row["investors"]["individual"]["buy"], toss_today["investors"]["individual"]["buy"])
        elif use_kis:
            held = self._held(market, today, now)        # a brief KIS failure: not 10-30 minutes back to Toss
        if kis_row is not None:
            self._last_kis[market] = (kis_row, now)
            rows = [kis_row, *(rows[1:] if toss_today else rows)]
            changed_at = self._watch(market, kis_row, now)
        elif held is not None:
            rows = [held, *(rows[1:] if toss_today else rows)]
            changed_at = self._changed_at(market, "kis")
        return {**base, "supported": True, "index": level, "records": rows, "error": None,
                "changed_at": changed_at}, True
