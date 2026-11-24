"""Binance USDⓈ-M confirmed funding settlements and UTC aggregates."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from . import shared_cache
from .single_flight import Flights

_BASE = "https://fapi.binance.com"
_CACHE = "binance-funding-v1"
_TIMEOUT = 5.0
_FRESH = 60
_METADATA_FRESH = 600
_STALE = 6 * 3600
_FAILURE_BACKOFF = 30
_PAGE_SIZE = 1000
_MAX_PAGES = 2
_METADATA_BYTES = 4 * 1024 * 1024
_HISTORY_BYTES = 1024 * 1024
_HOUR_MS = 3_600_000
_DAY_MS = 24 * _HOUR_MS
_JITTER_MS = 60_000
_DELAY_MS = 15 * 60_000
_HISTORY_DAYS = 37
_DEFAULT_HOURS = 8
_INTERVALS = (1, 2, 3, 4, 6, 8, 12, 24)
_RATE_TYPES = {"Regular": "regular", "Special": "special"}

# The labels come from the curated list, not from a supplier's arbitrary asset metadata.
_UNDERLYINGS = {
    "BTCUSDT": ("비트코인", "crypto", "BTC"),
    "ETHUSDT": ("이더리움", "crypto", "ETH"),
    "XRPUSDT": ("엑스알피", "crypto", "XRP"),
    "SOLUSDT": ("솔라나", "crypto", "SOL"),
    "SAMSUNGUSDT": ("삼성전자", "stock", "005930"),
    "SKHYNIXUSDT": ("SK하이닉스", "stock", "000660"),
    "NVDAUSDT": ("엔비디아", "stock", "NVDA"),
    "AMDUSDT": ("AMD", "stock", "AMD"),
    "MUUSDT": ("마이크론", "stock", "MU"),
    "SOXXUSDT": ("iShares Semiconductor ETF", "etf", "SOXX"),
}


@dataclass(frozen=True)
class _Contract:
    state: str
    contract_status: str | None = None
    interval: int | None = None
    onboard_ms: int | None = None


@dataclass(frozen=True)
class _Settlement:
    at: int
    rate: Decimal
    mark: float | None
    rate_type: str

    @property
    def key(self) -> tuple[int, str]:
        return self.at, self.rate_type

    def wire(self) -> dict:
        return {
            "settled_at": _iso(self.at),
            "rate": float(self.rate),
            "mark_price": self.mark,
            "rate_type": self.rate_type,
        }

    def cache(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "fundingTime": self.at,
            "fundingRate": str(self.rate),
            "markPrice": self.mark,
            "rateType": self.rate_type.title(),
        }


@dataclass(frozen=True)
class _Snapshot:
    fetched_ms: int
    cutoff_ms: int
    contract: _Contract
    rows: tuple[_Settlement, ...] = ()
    damaged_days: frozenset[int] = frozenset()

    def cache(self, symbol: str) -> dict:
        return {
            "fetched_ms": self.fetched_ms,
            "cutoff_ms": self.cutoff_ms,
            "contract": asdict(self.contract),
            "rows": [row.cache(symbol) for row in self.rows],
            "damaged_days": sorted(self.damaged_days),
        }


class _SourceError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _date(day: int) -> str:
    return datetime.fromtimestamp(day * 86400, UTC).date().isoformat()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _interval(value: object) -> int | None:
    return value if type(value) is int and value in _INTERVALS else None


def _decimal(value: object) -> Decimal | None:
    if type(value) not in (str, int, float) or len(str(value)) > 64:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if not result.is_finite() or not -100 <= result.adjusted() <= 100:
        return None
    return result


def _parse_row(row: dict) -> _Settlement | None:
    raw_type = row.get("rateType", "Regular")
    rate_type = _RATE_TYPES.get(raw_type) if isinstance(raw_type, str) else None
    rate = _decimal(row.get("fundingRate"))
    if rate is None or rate_type is None:
        return None
    mark = _decimal(row.get("markPrice"))
    return _Settlement(row["fundingTime"], rate, float(mark) if mark is not None and mark > 0 else None, rate_type)


def _snapshot_rank(value: object) -> int | None:
    cutoff = value.get("cutoff_ms") if isinstance(value, dict) else None
    return cutoff if type(cutoff) is int and cutoff > 0 else None


def _from_cache(value: object, symbol: str, now_ms: int) -> _Snapshot | None:
    cutoff = _snapshot_rank(value)
    fetched = value.get("fetched_ms") if isinstance(value, dict) else None
    if cutoff is None or type(fetched) is not int or not cutoff <= fetched <= now_ms or now_ms - fetched >= _STALE * 1000:
        return None
    raw_contract = value.get("contract")
    rows = value.get("rows")
    damaged = value.get("damaged_days")
    start_day = cutoff // _DAY_MS - _HISTORY_DAYS
    if (
        not isinstance(raw_contract, dict)
        or not isinstance(rows, list)
        or len(rows) > _PAGE_SIZE * _MAX_PAGES
        or not isinstance(damaged, list)
        or len(damaged) > _HISTORY_DAYS + 2
        or any(type(day) is not int or not start_day <= day <= cutoff // _DAY_MS for day in damaged)
    ):
        return None
    state = raw_contract.get("state")
    status = raw_contract.get("contract_status")
    interval = raw_contract.get("interval")
    onboard = raw_contract.get("onboard_ms")
    if (
        state not in ("active", "inactive", "unsupported")
        or (status is not None and (not isinstance(status, str) or len(status) > 32 or not status.isascii()))
        or (interval is not None and _interval(interval) is None)
        or (onboard is not None and (type(onboard) is not int or not 0 <= onboard <= fetched))
        or (state == "active" and status != "TRADING")
        or (state != "active" and (rows or damaged))
    ):
        return None
    parsed = []
    for row in rows:
        if not isinstance(row, dict) or row.get("symbol") != symbol:
            return None
        at = row.get("fundingTime")
        if type(at) is not int or not start_day * _DAY_MS <= at <= cutoff:
            return None
        settlement = _parse_row(row)
        if settlement is None or (parsed and parsed[-1].key >= settlement.key):
            return None
        parsed.append(settlement)
    return _Snapshot(fetched, cutoff, _Contract(state, status, interval, onboard), tuple(parsed), frozenset(damaged))


def _regular_day(day: int, rows: list[_Settlement], contract: _Contract) -> bool:
    if contract.onboard_ms is not None and day * _DAY_MS < contract.onboard_ms:
        return False
    # More frequent historical days can establish their own complete grid. A slower or irregular grid
    # can also be the result of missing rows, and the provider does not date its interval changes.
    slots = [row.at % _DAY_MS for row in rows if row.rate_type == "regular"]
    interval = 24 // len(slots) if slots and 24 % len(slots) == 0 else None
    return (
        contract.interval is not None
        and _interval(interval) is not None
        and interval <= contract.interval
        and all(0 <= at - slot * interval * _HOUR_MS <= _JITTER_MS for slot, at in enumerate(slots))
    )


def _rollup(snapshot: _Snapshot, today: int, days: int, now_ms: int) -> tuple:
    by_day: dict[int, list[_Settlement]] = {}
    visible = []
    latest = None
    for row in snapshot.rows:
        if row.at > now_ms:
            break
        day = row.at // _DAY_MS
        by_day.setdefault(day, []).append(row)
        # The settlement list has a stable chronological order; prefer the Regular row on ties.
        if latest is None or row.at > latest.at:
            latest = row
        if day >= today - days:
            visible.append(row.wire())

    daily = []
    running: deque[Decimal | None] = deque(maxlen=7)
    streak = 0
    for day in range(today - _HISTORY_DAYS, today):
        rows = by_day.get(day, [])
        damaged = day in snapshot.damaged_days
        if not damaged and _regular_day(day, rows, snapshot.contract):
            total = sum((row.rate for row in rows), Decimal(0))
            status = "complete"
        else:
            total = None
            status = "partial" if damaged or rows else "missing"
        running.append(total)
        streak = streak + 1 if total is not None and total < 0 else 0
        if day >= today - days:
            mean = sum(running) / 7 if len(running) == 7 and all(x is not None for x in running) else None
            daily.append({
                "date": _date(day),
                "total_rate": float(total) if total is not None else None,
                "settlement_count": len(rows),
                "status": status,
                "average_7d": float(mean) if mean is not None else None,
            })

    partial = by_day.get(today, [])
    # A trusted subtotal is useful even though the ongoing day can never be complete.
    today_total = (
        float(sum(row.rate for row in partial)) if partial and today not in snapshot.damaged_days else None
    )
    current = {"date": _date(today), "total_rate": today_total, "settlement_count": len(partial), "status": "partial"}
    return daily, current, visible, latest, streak


def _retry_wait(response: httpx.Response) -> int:
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.isascii() and retry_after.isdigit() and len(retry_after) <= 10:
        return min(3600, max(1, int(retry_after)))
    return 120 if response.status_code == 418 else 60


class FundingHistory:
    """Bounded anonymous market-data reads with per-symbol and metadata single flights."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = enabled
        self._transport = transport
        self._clock = clock
        self._monotonic = monotonic
        self._requests = asyncio.Semaphore(3)
        self._flights = Flights()
        self._cache: dict[str, _Snapshot] = {}
        self._failures: dict[str, tuple[float, str]] = {}
        self._metadata: tuple[int, dict[str, _Contract]] | None = None
        self._cooldown_until = 0.0

    def _now_ms(self) -> int:
        return int(self._clock().timestamp() * 1000)

    async def _json(self, path: str, **params: int | str) -> object:
        async with self._requests:
            if self._monotonic() < self._cooldown_until:
                raise _SourceError("rate_limited")
            try:
                async with (
                    asyncio.timeout(_TIMEOUT),
                    httpx.AsyncClient(
                        base_url=_BASE,
                        transport=self._transport,
                        timeout=_TIMEOUT,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client,
                    client.stream("GET", path, params=params) as response,
                ):
                    if response.status_code in (418, 429):
                        self._cooldown_until = max(
                            self._cooldown_until, self._monotonic() + _retry_wait(response),
                        )
                        raise _SourceError("rate_limited")
                    if response.status_code != 200:
                        raise _SourceError("upstream_error")
                    body = bytearray()
                    limit = _HISTORY_BYTES if path == "/fapi/v1/fundingRate" else _METADATA_BYTES
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(body) + len(chunk) > limit:
                            raise _SourceError("invalid_response")
                        body.extend(chunk)
            except (httpx.TimeoutException, TimeoutError) as exc:
                raise _SourceError("timeout") from exc
            except httpx.RequestError as exc:
                raise _SourceError("upstream_error") from exc
            try:
                return json.loads(body)
            except (ValueError, RecursionError) as exc:
                raise _SourceError("invalid_response") from exc

    async def _read_contracts(self) -> dict[str, _Contract]:
        exchange = await self._json("/fapi/v1/exchangeInfo")
        if not isinstance(exchange, dict) or not isinstance(exchange.get("symbols"), list):
            raise _SourceError("invalid_response")
        funding = await self._json("/fapi/v1/fundingInfo")
        if not isinstance(funding, list):
            raise _SourceError("invalid_response")

        assets = {
            row["symbol"]: row
            for row in exchange["symbols"]
            if isinstance(row, dict) and isinstance(row.get("symbol"), str) and row["symbol"] in _UNDERLYINGS
        }
        periods = {
            row["symbol"]: _interval(row.get("fundingIntervalHours"))
            for row in funding
            if isinstance(row, dict) and isinstance(row.get("symbol"), str) and row["symbol"] in _UNDERLYINGS
        }
        now_ms = self._now_ms()
        result = {}
        for symbol, (_, kind, _) in _UNDERLYINGS.items():
            row = assets.get(symbol, {})
            expected_type = "PERPETUAL" if kind == "crypto" else "TRADIFI_PERPETUAL"
            if (
                row.get("baseAsset") != symbol.removesuffix("USDT")
                or row.get("quoteAsset") != "USDT"
                or row.get("marginAsset") != "USDT"
                or row.get("contractType") != expected_type
            ):
                result[symbol] = _Contract("unsupported")
                continue
            status = row.get("status")
            if not isinstance(status, str) or not status or len(status) > 32 or not status.isascii():
                raise _SourceError("invalid_response")
            onboard = row.get("onboardDate")
            result[symbol] = _Contract(
                "active" if status == "TRADING" else "inactive",
                status,
                periods.get(symbol, _DEFAULT_HOURS),
                onboard if type(onboard) is int and 0 <= onboard <= now_ms else None,
            )
        self._metadata = now_ms, result
        return result

    async def _contract(self, symbol: str) -> _Contract:
        if self._metadata is not None:
            at, contracts = self._metadata
            if 0 <= self._now_ms() - at < _METADATA_FRESH * 1000:
                return contracts[symbol]
        contracts = await self._flights.run("metadata", self._refresh_contracts)
        return contracts[symbol]

    async def _refresh_contracts(self) -> dict[str, _Contract]:
        reason = self._failure("metadata")
        if reason is not None:
            raise _SourceError(reason)
        try:
            return await self._read_contracts()
        except _SourceError as exc:
            self._failures["metadata"] = self._monotonic() + _FAILURE_BACKOFF, exc.reason
            raise

    async def _settlements(
        self, symbol: str, start_ms: int, end_ms: int,
    ) -> tuple[tuple[_Settlement, ...], frozenset[int]]:
        settlements: dict[tuple[int, str], _Settlement] = {}
        conflicts: set[tuple[int, str]] = set()
        damaged: set[int] = set()
        cursor = start_ms
        for _ in range(_MAX_PAGES):
            page = await self._json(
                "/fapi/v1/fundingRate", symbol=symbol, startTime=cursor, endTime=end_ms, limit=_PAGE_SIZE,
            )
            if not isinstance(page, list) or len(page) > _PAGE_SIZE:
                raise _SourceError("invalid_response")
            last = cursor
            for row in page:
                if not isinstance(row, dict) or row.get("symbol") != symbol:
                    continue
                at = row.get("fundingTime")
                if type(at) is not int:
                    raise _SourceError("invalid_response")
                if not cursor <= at <= end_ms:
                    continue
                last = max(last, at)
                item = _parse_row(row)
                if item is None:
                    damaged.add(at // _DAY_MS)
                    continue
                previous = settlements.get(item.key)
                if previous is not None and (previous.rate != item.rate or (
                    previous.mark is not None and item.mark is not None and previous.mark != item.mark
                )):
                    conflicts.add(item.key)
                    damaged.add(at // _DAY_MS)
                elif previous is None or previous.mark is None:
                    settlements[item.key] = item
            if len(page) < _PAGE_SIZE:
                if page and not settlements and not damaged:
                    raise _SourceError("invalid_response")
                rows = tuple(item for key, item in sorted(settlements.items()) if key not in conflicts)
                if damaged and not rows:
                    raise _SourceError("invalid_response")
                return rows, frozenset(damaged)
            # Overlap the exact limit boundary for Regular and Special rows at the same timestamp.
            if last == cursor:
                break
            cursor = last
        raise _SourceError("incomplete_history")

    def _failure(self, key: str) -> str | None:
        now = self._monotonic()
        if now < self._cooldown_until:
            return "rate_limited"
        failure = self._failures.get(key)
        if failure is not None and now < failure[0]:
            return failure[1]
        return None

    async def _refresh(self, symbol: str) -> tuple[_Snapshot | None, str | None]:
        local = self._cache.get(symbol)
        shared = await shared_cache.aget(_CACHE, symbol)
        if shared is not None:
            cached = _from_cache(shared[0], symbol, self._now_ms())
            if cached is not None and (local is None or cached.cutoff_ms > local.cutoff_ms):
                local = self._cache[symbol] = cached
        now_ms = self._now_ms()
        if local is not None and not 0 <= now_ms - local.fetched_ms < _STALE * 1000:
            local = None
        if local is not None and now_ms - local.fetched_ms < _FRESH * 1000:
            return local, None
        last_good = local if local is not None and local.rows else None
        reason = self._failure(symbol)
        if reason is not None:
            return last_good, reason
        try:
            contract = await self._contract(symbol)
            cutoff = self._now_ms()
            if contract.state == "active":
                start = (cutoff // _DAY_MS - _HISTORY_DAYS) * _DAY_MS
                rows, damaged = await self._settlements(symbol, start, cutoff)
                if last_good is not None and not rows:
                    raise _SourceError("empty_refresh")
                snapshot = _Snapshot(self._now_ms(), cutoff, contract, rows, damaged)
            else:
                snapshot = _Snapshot(cutoff, cutoff, contract)
        except _SourceError as exc:
            self._failures[symbol] = self._monotonic() + _FAILURE_BACKOFF, exc.reason
            return last_good, exc.reason
        self._cache[symbol] = snapshot
        self._failures.pop(symbol, None)
        await shared_cache.aput_newer(_CACHE, symbol, snapshot.cache(symbol), _STALE, rank=_snapshot_rank)
        return snapshot, None

    async def read(self, symbol: str, days: int) -> dict:
        symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
        if symbol not in _UNDERLYINGS or type(days) is not int or not 1 <= days <= 30:
            raise ValueError("invalid funding symbol or days")

        snapshot = self._cache.get(symbol)
        reason = None
        if not self._enabled:
            snapshot, reason = None, "fake_runs"
        elif snapshot is None or not 0 <= self._now_ms() - snapshot.fetched_ms < _FRESH * 1000:
            snapshot, reason = await self._flights.run(symbol, lambda: self._refresh(symbol))

        now_ms = self._now_ms()
        today = now_ms // _DAY_MS
        name, kind, ticker = _UNDERLYINGS[symbol]
        result = {
            "symbol": symbol,
            "days": days,
            "underlying": {"name": name, "kind": kind, "ticker": ticker},
            "source": "binance",
            "contract_status": None,
            "fetched_at": None,
            "status": "source_error",
            "reason": reason,
            "funding_interval_hours": None,
            "latest_settlement": None,
            "settlements": [],
            "daily": [],
            "current_day": {"date": _date(today), "total_rate": None, "settlement_count": 0, "status": "partial"},
            "negative_streak_days": 0,
            "negative_streak_truncated": False,
        }
        if snapshot is None:
            return result

        contract = snapshot.contract
        result.update(contract_status=contract.contract_status, fetched_at=_iso(snapshot.fetched_ms),
                      funding_interval_hours=contract.interval)
        if contract.state != "active":
            result.update(status=contract.state, reason=reason or (
                "contract_not_trading" if contract.state == "inactive" else "contract_not_supported"
            ))
            return result

        daily, current, visible, latest, streak = _rollup(snapshot, today, days, now_ms)
        last_regular = next((row for row in reversed(snapshot.rows) if row.rate_type == "regular" and row.at <= now_ms), None)
        if reason is None and contract.interval is not None:
            if last_regular is not None and now_ms - last_regular.at > contract.interval * _HOUR_MS + _DELAY_MS:
                reason = "settlement_delayed"
            elif latest is not None and last_regular is None:
                reason = "regular_settlement_missing"
        result.update(
            status="stale" if reason is not None else "available" if latest is not None else "no_data",
            reason=reason or ("no_settlements" if latest is None else None),
            latest_settlement=latest.wire() if latest is not None else None,
            settlements=visible,
            daily=daily,
            current_day=current,
            negative_streak_days=streak,
            negative_streak_truncated=streak == _HISTORY_DAYS,
        )
        return result
