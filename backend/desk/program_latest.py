"""Best-effort sharing of committed KRX program trading snapshots (#689)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as wall_time

from desk import program_trade, shared_cache
from desk.futures_flow import KST
from desk.futures_history import FuturesHistory, interval_min
from desk.ttl_cache import TTLCache

NAMESPACE = "program_trade"
DB_TTL_SEC = 30.0
LOCAL_TTL_SEC = 3.0
REDIS_MAX_TTL_SEC = 3600.0
SEEN_TTL_SEC = 86400.0


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(UTC) if stamp.tzinfo is not None else None
    except (OverflowError, ValueError):
        return None


def _source_time(value: object, checked: datetime, day: date) -> str | None:
    if not isinstance(value, str) or len(value) != 8 or value[2] != ":" or value[5] != ":":
        return None
    try:
        reported = wall_time.fromisoformat(value)
    except ValueError:
        return None
    stamp = datetime.combine(day, reported, KST).astimezone(UTC)
    return stamp.isoformat() if reported >= program_trade.OPEN and stamp <= checked else None


def _observation(row: object, checked: datetime, source_time: str | None) -> dict | None:
    if not isinstance(row, dict):
        return None
    amounts = {kind: row.get(kind) for kind in program_trade.KINDS}
    if all(value is None for value in amounts.values()) or any(
        value is not None and (type(value) not in (int, float) or not abs(value) <= program_trade.CEILING_100M)
        for value in amounts.values()
    ):
        return None
    return {
        "at": checked.isoformat(timespec="seconds"),
        **amounts,
        "source": "kis",
        "source_updated_at": source_time,
        "fetched_at": checked.isoformat(),
    }


def _normalize(value: object, day: date) -> dict | None:
    """Accept only one market's public figures for the requested day from the sealed tier."""
    if not isinstance(value, dict) or value.get("source") != "kis":
        return None
    checked = _timestamp(value.get("fetched_at"))
    if checked is None or abs((checked.date() - day).days) > 1 or checked.astimezone(KST).date() != day:
        return None
    reported = _timestamp(value.get("source_updated_at"))
    if value.get("source_updated_at") is not None and (
        reported is None or reported > checked or reported.astimezone(KST).date() != day
    ):
        return None
    cleaned = _observation(value, checked, reported.isoformat() if reported is not None else None)
    return cleaned if cleaned == value else None


def _rank(value: dict) -> float:
    return datetime.fromisoformat(value["fetched_at"]).timestamp()


class ProgramLatest:
    """Today's latest program totals, with PostgreSQL still the history and cache-miss path."""

    def __init__(
        self, history: FuturesHistory, *, snapshot_min: float = 1.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cache_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._history, self._clock = history, clock
        self._ttl = min(REDIS_MAX_TTL_SEC, max(shared_cache.LATEST_KEEP_SEC, interval_min(snapshot_min) * 60 + 30))
        self._front: TTLCache[tuple[str, str], dict] = TTLCache(4, LOCAL_TTL_SEC, cache_clock)
        self._seen: TTLCache[tuple[str, str], dict] = TTLCache(4, SEEN_TTL_SEC, cache_clock)
        self._fallback: TTLCache[date, dict] = TTLCache(2, DB_TTL_SEC, cache_clock)
        self._fallback_lock = asyncio.Lock()

    def _remember(self, key: tuple[str, str], candidate: dict | None) -> dict | None:
        known = self._seen.get(key)
        if candidate is None:
            return known
        if known is not None:
            known_at, candidate_at = _rank(known), _rank(candidate)
            # The retained DB row has no KIS source clock. Equal figures can gain that clock from Redis.
            source_clock_added = (
                known["source_updated_at"] is None and candidate["source_updated_at"] is not None
                and all(known[kind] == candidate[kind] for kind in program_trade.KINDS)
            )
            if known_at > candidate_at or (known_at == candidate_at and not source_clock_added):
                return known
        self._seen.put(key, candidate)
        return candidate

    async def record(self, view: dict, spot: dict | None, program: dict | None) -> bool:
        checked = self._clock().astimezone(UTC)
        stored = await asyncio.to_thread(self._history.record, view, checked, spot, program)
        if not stored or not program:
            return stored

        day = checked.astimezone(KST).date()

        def rank(value: object) -> float | None:
            cleaned = _normalize(value, day)
            return _rank(cleaned) if cleaned is not None else None

        writes = []
        for market in program_trade.MARKETS:
            row = program.get(market)
            if not isinstance(row, dict):
                continue
            value = _observation(row, checked, _source_time(row.get("at"), checked, day))
            if value is None:
                continue
            key = day.isoformat(), market
            self._front.put(key, self._remember(key, value))
            writes.append(shared_cache.aput_newer(NAMESPACE, key, value, self._ttl, rank=rank))
        if writes:
            self._fallback.pop(day)
            await asyncio.gather(*writes)
        return stored

    async def _read_history(self, day: date) -> dict:
        async with self._fallback_lock:
            rows = self._fallback.get(day)
            if rows is None:
                rows = await asyncio.to_thread(self._history.program, day)
                self._fallback.put(day, rows)
            return rows

    async def read(self, day: date, market: str) -> dict | None:
        if market not in program_trade.MARKETS:
            return None
        key = day.isoformat(), market
        if (front := self._front.get(key)) is not None:
            return front

        hit = await shared_cache.aget(NAMESPACE, key)
        value = _normalize(hit[0], day) if hit is not None else None
        latest_allowed = self._clock() + timedelta(seconds=shared_cache.SKEW_SEC)
        if value is not None and _rank(value) <= latest_allowed.timestamp():
            value = self._remember(key, value)
            self._front.put(key, value, ttl=min(LOCAL_TTL_SEC, hit[2]))
            return value

        row = (await self._read_history(day)).get(market)
        checked = _timestamp(row.get("fetched_at", row.get("at"))) if isinstance(row, dict) else None
        value = _observation(row, checked, None) if checked is not None and checked.astimezone(KST).date() == day else None
        return self._remember(key, value)
