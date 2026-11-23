"""Read-only US equity option detail (#887), backed by an explicitly supplied source."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from heapq import nsmallest
from typing import Literal, Protocol, TypeVar
from zoneinfo import ZoneInfo

from .single_flight import Flights
from .ttl_cache import TTLCache

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
CACHE_SECONDS = 180
FAILURE_SECONDS = 30
SOURCE_TIMEOUT_SECONDS = 8
MAX_PARALLEL = 3
MAX_EXPIRATIONS = 128
MAX_CONTRACTS = 4096
MAX_STRIKES = 500
TOP_COUNT = 3
# Even at the contract limit, aggregate counts remain exact in a JSON number read by JavaScript.
MAX_FIELD_COUNT = (2**53 - 1) // MAX_CONTRACTS

Side = Literal["call", "put"]
_MISS = object()
K = TypeVar("K", bound=Hashable)
T = TypeVar("T")


@dataclass(frozen=True)
class MetricTime:
    """Source-declared trade date, snapshot time and publication delay, if supplied."""

    trade_date: date | None = None
    as_of: datetime | None = None
    delay_sec: int | None = None


@dataclass(frozen=True)
class Underlying:
    price: float | None = None
    as_of: datetime | None = None
    delay_sec: int | None = None


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    side: Side
    strike: float
    volume: int | str | float | None = None
    oi: int | str | float | None = None
    standard: bool | None = None


@dataclass(frozen=True)
class OptionCatalog:
    source: str
    expirations: tuple[date, ...]


@dataclass(frozen=True)
class OptionChain:
    expiration: date
    contracts: tuple[OptionContract, ...]
    volume: MetricTime = MetricTime()
    oi: MetricTime = MetricTime()
    spot: Underlying = Underlying()


@dataclass(frozen=True)
class _ChainSnapshot:
    volume: dict
    oi: dict
    rows: list[dict]
    excluded: int
    spot: dict
    fetched_at: str | None


class OptionSource(Protocol):
    """An entitled provider of a complete expiry and contract listing.

    The catalog is empty only for a symbol known to have no listed options. A
    partial or failed fetch raises. Missing contract counts remain None. The
    adapter supplies metric-specific dates and timestamps only when the source
    actually publishes them, and establishes standard deliverables.
    """

    async def catalog(self, ticker: str) -> OptionCatalog: ...
    async def chain(self, ticker: str, expiration: date) -> OptionChain: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) and value.utcoffset() is not None else None


def _day(value: date | None) -> str | None:
    return value.isoformat() if type(value) is date else None


def _delay(value: int | None) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _positive(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def _count(value: object) -> int | None:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        return None
    return int(n) if n.is_finite() and 0 <= n <= MAX_FIELD_COUNT and n == n.to_integral_value() else None


def _metric(calls: list[int | None], puts: list[int | None], timing: MetricTime) -> dict:
    c = None if None in calls else sum(calls)
    p = None if None in puts else sum(puts)
    if c is None or p is None:
        status = "partial" if any(v is not None for v in (*calls, *puts)) else "missing"
    else:
        status = "ok" if c else "zero_calls"
    return _metric_payload(c, p, status, timing)


def _metric_payload(calls: int | None, puts: int | None, status: str, timing: MetricTime) -> dict:
    return {"calls": calls, "puts": puts, "put_call_ratio": puts / calls if calls and puts is not None else None,
            "status": status, "trade_date": _day(timing.trade_date), "as_of": _stamp(timing.as_of),
            "delay_sec": _delay(timing.delay_sec)}


FIELDS = ("call_volume", "put_volume", "call_oi", "put_oi")


def _summary(chain: OptionChain) -> tuple[dict, dict, list[dict], int]:
    unique: dict[str, tuple[Side, float, int | None, int | None]] = {}
    for contract in chain.contracts:
        if contract.standard is not True:
            continue
        strike = _positive(contract.strike)
        if (not isinstance(contract.symbol, str) or not contract.symbol.strip()
                or contract.side not in ("call", "put") or strike is None):
            raise ValueError("invalid standard option contract")
        row = (contract.side, strike, _count(contract.volume), _count(contract.oi))
        if unique.setdefault(contract.symbol.strip(), row) != row:
            raise ValueError("conflicting option contract")

    groups: dict[float, dict[str, list[int | None]]] = {}
    totals: dict[str, list[int | None]] = {field: [] for field in FIELDS}
    for side, strike, volume, oi in unique.values():
        group = groups.setdefault(strike, {field: [] for field in FIELDS})
        for name, value in (("volume", volume), ("oi", oi)):
            field = f"{side}_{name}"
            group[field].append(value)
            totals[field].append(value)

    rows = [{"strike": strike, **{field: sum(values) if values and None not in values else None
                                for field, values in groups[strike].items()}} for strike in sorted(groups)]
    volume = _metric(totals["call_volume"], totals["put_volume"], chain.volume) if rows else (
        _metric_payload(None, None, "missing", chain.volume))
    oi = _metric(totals["call_oi"], totals["put_oi"], chain.oi) if rows else (
        _metric_payload(None, None, "missing", chain.oi))
    return volume, oi, rows, len(chain.contracts) - len(unique)


def _top(rows: list[dict], field: str) -> list[dict]:
    ranked = nsmallest(TOP_COUNT, (r for r in rows if r[field]), key=lambda r: (-r[field], r["strike"]))
    return [{"strike": r["strike"], "count": r[field]} for r in ranked]


def _distribution(rows: list[dict], strike_min: float | None, strike_max: float | None, spot: float | None) -> dict:
    matching = [r for r in rows if (strike_min is None or r["strike"] >= strike_min)
                and (strike_max is None or r["strike"] <= strike_max)]
    displayed = matching[:MAX_STRIKES]
    if len(matching) > MAX_STRIKES and spot is not None:
        nearest = sorted(matching, key=lambda r: (abs(r["strike"] - spot), r["strike"]))[:MAX_STRIKES]
        displayed = sorted(nearest, key=lambda r: r["strike"])
    return {"strikes": [dict(row) for row in displayed],
            "top": {m: {"calls": _top(matching, f"call_{m}"), "puts": _top(matching, f"put_{m}")}
                    for m in ("volume", "oi")},
            "range": {"min": strike_min, "max": strike_max, "total_strikes": len(rows),
                      "matching_strikes": len(matching), "limit": MAX_STRIKES,
                      "truncated": len(matching) > len(displayed)}}


class OptionsDetail:
    def __init__(self, provider: OptionSource | None = None, *, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = _now) -> None:
        self._provider = provider
        self._now = now
        self._catalogs: TTLCache[str, OptionCatalog | None] = TTLCache(128, CACHE_SECONDS, clock)
        self._chains: TTLCache[tuple[str, date], _ChainSnapshot | None] = TTLCache(32, CACHE_SECONDS, clock)
        self._gate = asyncio.Semaphore(MAX_PARALLEL)
        self._flights = Flights()

    async def _load(self, key: K, cache: TTLCache[K, T | None], read: Callable[[], Awaitable[T]]) -> T | None:
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached

        async def fetch() -> T | None:
            try:
                async with asyncio.timeout(SOURCE_TIMEOUT_SECONDS), self._gate:
                    value = await read()
            except Exception as exc:  # noqa: BLE001 - a source failure must not masquerade as no listing
                log.info("option detail source failed (%s)", type(exc).__name__)
                cache.put(key, None, ttl=FAILURE_SECONDS)
                return None
            cache.put(key, value)
            return value

        return await self._flights.run(key, fetch)

    async def get(self, ticker: str, expiration: date | None = None, *, strike_min: float | None = None,
                  strike_max: float | None = None) -> dict:
        if ((strike_min is not None and _positive(strike_min) is None)
                or (strike_max is not None and _positive(strike_max) is None)
                or (strike_min is not None and strike_max is not None and strike_min > strike_max)):
            raise ValueError("invalid strike range")

        response = {"ticker": ticker, "status": "unavailable", "reason": "no_provider", "source": None,
                    "expiration": _day(expiration), "expirations": [], "expirations_truncated": False,
                    "fetched_at": None, "spot": {"price": None, "as_of": None, "delay_sec": None},
                    "volume": _metric_payload(None, None, "missing", MetricTime()),
                    "oi": _metric_payload(None, None, "missing", MetricTime()), "excluded_contracts": 0,
                    **_distribution([], strike_min, strike_max, None)}
        provider = self._provider
        if provider is None:
            return response

        async def get_catalog() -> OptionCatalog:
            catalog = await provider.catalog(ticker)
            if not catalog.source.strip() or any(type(d) is not date for d in catalog.expirations):
                raise ValueError("invalid option catalog")
            return catalog

        catalog = await self._load(ticker, self._catalogs, get_catalog)
        if catalog is None:
            return {**response, "reason": "catalog_unavailable"}
        today = self._now().astimezone(ET).date()
        dates = sorted({day for day in catalog.expirations if day >= today})
        response.update(source=catalog.source, expirations=[d.isoformat() for d in dates[:MAX_EXPIRATIONS]],
                        expirations_truncated=len(dates) > MAX_EXPIRATIONS)
        if not catalog.expirations:
            return {**response, "status": "no_options", "reason": "not_listed"}
        if expiration is not None and expiration not in dates:
            return {**response, "status": "invalid_expiration", "reason": "expiration_not_listed"}
        if not dates:
            return {**response, "status": "no_data", "reason": "no_current_expirations"}
        selected = expiration or dates[0]
        response.update(expiration=selected.isoformat())

        async def get_chain() -> _ChainSnapshot:
            chain = await provider.chain(ticker, selected)
            fetched_at = _stamp(self._now())
            if chain.expiration != selected or len(chain.contracts) > MAX_CONTRACTS:
                raise ValueError("invalid option chain")
            volume, oi, rows, excluded = await asyncio.to_thread(_summary, chain)
            spot = {"price": _positive(chain.spot.price), "as_of": _stamp(chain.spot.as_of),
                    "delay_sec": _delay(chain.spot.delay_sec)}
            return _ChainSnapshot(volume, oi, rows, excluded, spot, fetched_at)

        snapshot = await self._load((ticker, selected), self._chains, get_chain)
        if snapshot is None:
            return {**response, "reason": "chain_unavailable"}
        distribution = _distribution(snapshot.rows, strike_min, strike_max, snapshot.spot["price"])
        return {**response, "status": "ok" if distribution["strikes"] else "no_data",
                "reason": None if distribution["strikes"] else ("empty_range" if snapshot.rows else "no_standard_contracts"),
                "fetched_at": snapshot.fetched_at, "spot": dict(snapshot.spot), "volume": dict(snapshot.volume),
                "oi": dict(snapshot.oi), "excluded_contracts": snapshot.excluded, **distribution}
