"""Normalize explicitly supplied KRX full-session trades and published net balances.

The public statistics pages describe the measures, but do not serve as an
automated data source. A caller must supply a source they are authorized to use.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as wall_time

from . import market_calendar

_TICKER = re.compile(r"[0-9]{6}\.(KS|KQ)")
_MARKETS = {"KS": "KOSPI", "KQ": "KOSDAQ"}
_FULL_SESSION_AFTER = wall_time(20, 10)
_HISTORY_DAYS = 20
_CACHE_LIMIT = 256
_SUCCESS_TTL = 15 * 60
_RETRY_TTL = 60


@dataclass(frozen=True)
class TradeRow:
    """Whole-session KRX statistics, including NXT where included in the publication."""

    as_of: date
    short_value_krw: int | None
    total_value_krw: int | None
    published: bool = True


@dataclass(frozen=True)
class BalanceRow:
    """The exchange's ratio and its own reporting date."""

    as_of: date
    ratio_pct: float | None
    published: bool = True


@dataclass(frozen=True)
class Publication:
    """Normalized source output. None indicates an error for that series; an empty list means no rows."""

    trades: Sequence[TradeRow] | None
    balances: Sequence[BalanceRow] | None


class UnsupportedTicker(ValueError):
    """The source explicitly reported that it does not cover this ticker."""


Source = Callable[[str, date, date], Awaitable[Publication]]


def is_kr_ticker(ticker: str) -> bool:
    return _TICKER.fullmatch(ticker.strip().upper()) is not None


def _sessions(last: date) -> list[date]:
    sessions = [last]
    for _ in range(_HISTORY_DAYS - 1):
        sessions.append(market_calendar.previous_open("kr", sessions[-1]))
    return sessions[::-1]


def _point(day: date, ratio: float | None = None, status: str = "no_data") -> dict:
    return {"as_of": day.isoformat(), "ratio_pct": ratio, "status": status}


def _trade_point(row: TradeRow) -> dict:
    if not row.published:
        return _point(row.as_of, status="not_published")
    short, total = row.short_value_krw, row.total_value_krw
    if type(total) is int and total == 0:
        return _point(row.as_of, status="zero_denominator")
    if type(short) is not int or type(total) is not int or not 0 <= short <= total:
        return _point(row.as_of)
    return _point(row.as_of, short / total * 100, "available")


def _balance_point(row: BalanceRow) -> dict:
    if not row.published:
        return _point(row.as_of, status="not_published")
    ratio = row.ratio_pct
    if ratio is None or not math.isfinite(ratio) or ratio < 0:
        return _point(row.as_of)
    return _point(row.as_of, ratio, "available")


def _series(status: str, points: list[dict]) -> dict:
    last = next((point for point in reversed(points) if point["status"] == "available"), None)
    return {
        "status": status,
        "latest": last,
        "stale": last is not None and status in {"no_data", "source_error", "zero_denominator"},
    }


@dataclass(frozen=True)
class _Cached:
    session: date
    expires_at: float
    trades: dict[date, TradeRow]
    balances: dict[date, BalanceRow]
    trade_error: bool = False
    balance_error: bool = False
    unsupported: bool = False


class KrxShortSales:
    """Group reads by the most recent completed publication session and keep the last good data on error."""

    def __init__(self, source: Source | None = None, *, clock: Callable[[], float] = time.monotonic):
        self._source = source
        self._clock = clock
        self._cache: OrderedDict[str, _Cached] = OrderedDict()
        # Keep the number of locks bounded even after many different ticker requests.
        self._locks = tuple(asyncio.Lock() for _ in range(32))

    async def get(self, ticker: str, now: datetime | None = None) -> dict:
        ticker = ticker.strip().upper()
        match = _TICKER.fullmatch(ticker)
        if match is None:
            return self._empty(ticker, None, "unsupported")
        market = _MARKETS[match[1]]
        if self._source is None:
            return self._empty(ticker, market, "source_unavailable")

        local_now = (now or datetime.now(market_calendar.KST)).astimezone(market_calendar.KST)
        day = local_now.date()
        open_today = market_calendar.is_open("kr", day)
        last_session = day if open_today else market_calendar.previous_open("kr", day)
        pending_today = open_today and local_now.time() < _FULL_SESSION_AFTER
        published_session = market_calendar.previous_open("kr", day) if pending_today else last_session
        days = _sessions(last_session)
        lock = self._locks[hash(ticker) % len(self._locks)]
        async with lock:
            data = await self._get(ticker, published_session, self._source)
        if data.unsupported:
            return self._empty(ticker, market, "unsupported")

        history = []
        for session in days:
            if session > published_session:
                history.append(_point(session, status="not_published"))
            elif (row := data.trades.get(session)) is not None:
                history.append(_trade_point(row))
            else:
                history.append(_point(session, status="source_error" if data.trade_error else "no_data"))
        trade_status = "source_error" if data.trade_error else history[-1]["status"]
        trade = _series(trade_status, history)
        # The source window may include an older valid point just outside the
        # displayed history while today's publication is pending.
        if trade["latest"] is None:
            available = [_trade_point(row) for _, row in sorted(data.trades.items())]
            trade = _series(trade_status, available)
        balance_points = [_balance_point(row) for _, row in sorted(data.balances.items())]
        balance_status = "source_error" if data.balance_error else (
            balance_points[-1]["status"] if balance_points else "no_data"
        )
        return {
            "ticker": ticker, "market": market, "status": trade_status,
            "trade": {**trade, "history": history}, "balance": _series(balance_status, balance_points),
        }

    async def _get(self, ticker: str, session: date, source: Source) -> _Cached:
        old = self._cache.get(ticker)
        if old and old.session == session and old.expires_at > self._clock():
            self._cache.move_to_end(ticker)
            return old
        days = set(_sessions(session))
        try:
            result = await source(ticker, min(days), session)
        except UnsupportedTicker:
            data = _Cached(session, self._clock() + _SUCCESS_TTL, {}, {}, unsupported=True)
        except Exception:  # noqa: BLE001 - source implementations can fail; preserve the last good publication
            data = self._updated(session, days, Publication(None, None), old)
        else:
            data = self._updated(session, days, result, old)
        self._cache[ticker] = data
        self._cache.move_to_end(ticker)
        if len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return data

    def _updated(
        self, session: date, days: set[date], result: Publication, old: _Cached | None,
    ) -> _Cached:
        trade_error, balance_error = result.trades is None, result.balances is None
        trades = (old.trades if old else {}) if result.trades is None else {
            row.as_of: row for row in result.trades if row.as_of in days
        }
        balances = (old.balances if old else {}) if result.balances is None else {
            row.as_of: row for row in result.balances if row.as_of in days
        }
        latest_trade = trades.get(session)
        retry = trade_error or balance_error or latest_trade is None or not latest_trade.published
        ttl = _RETRY_TTL if retry else _SUCCESS_TTL
        return _Cached(session, self._clock() + ttl, trades, balances, trade_error, balance_error)

    @staticmethod
    def _empty(ticker: str, market: str | None, status: str) -> dict:
        return {
            "ticker": ticker, "market": market, "status": status,
            "trade": {**_series(status, []), "history": []}, "balance": _series(status, []),
        }


KRX_SHORT_SALES = KrxShortSales()
