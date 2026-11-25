"""Normalize explicitly supplied KRX full-session trades and published net balances.

The public statistics pages describe the measures, but do not serve as an
automated data source. A caller must supply a source they are authorized to use.
#979: the trade share comes from KIS' daily short-sale trend (``kis_source``) on the desk's own
KIS key; no connected source publishes the net balance, so that series answers ``source_unavailable``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as wall_time

from . import market_calendar
from .futures_flow import _short as kis_reason

_TICKER = re.compile(r"[0-9]{6}\.(KS|KQ)")
_MARKETS = {"KS": "KOSPI", "KQ": "KOSDAQ"}
_FULL_SESSION_AFTER = wall_time(20, 10)
_HISTORY_DAYS = 20
_CACHE_LIMIT = 256
_SUCCESS_TTL = 15 * 60
_RETRY_TTL = 60
# KIS [국내주식-134] 국내주식 공매도 일별추이: short value and the day's whole value on one row (#979)
KIS_SHORT_PATH = "/uapi/domestic-stock/v1/quotations/daily-short-sale"
KIS_SHORT_TR = "FHPST04830000"
# Our share and KIS' own (ssts_tr_pbmn_rlim) must agree within this factor, else the units differ
_KIS_RATIO_FACTOR = 10.0
# Distinct tickers each miss the cache: at most this many KIS calls at once, under the app key's rate
KIS_PARALLEL = 2

log = logging.getLogger(__name__)


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


def _kis_int(value) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def _kis_float(value) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_kis_trades(body: dict) -> list[TradeRow]:
    """KIS ``output2`` rows: ``ssts_tr_pbmn`` over ``acml_tr_pbmn`` (both won) per ``stck_bsop_date``.

    A row whose share disagrees with KIS' own ``ssts_tr_pbmn_rlim`` by an order of magnitude keeps
    its date but no values (``no_data``) - a unit change must not draw a wrong share.
    """
    rows = body.get("output2")
    if not isinstance(rows, list):
        raise TypeError("KIS short sales: no output2 list")
    trades: dict[date, TradeRow] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            day = datetime.strptime(str(row.get("stck_bsop_date") or ""), "%Y%m%d").date()
        except ValueError:
            continue
        short, total = _kis_int(row.get("ssts_tr_pbmn")), _kis_int(row.get("acml_tr_pbmn"))
        quoted = _kis_float(row.get("ssts_tr_pbmn_rlim"))
        if short and total:
            # Without KIS' own share to agree with, a positive share is unverified (fails closed)
            ours = short / total * 100
            if quoted is None or (quoted <= 0 and ours >= 0.005) or (
                    quoted > 0 and not 1 / _KIS_RATIO_FACTOR <= ours / quoted <= _KIS_RATIO_FACTOR):
                log.warning("KIS short sales %s: share %.4f%% vs KIS %s%%, dropped", day, ours, quoted)
                short = total = None
        kept = trades.get(day)
        if kept is None or kept.short_value_krw is None:
            trades[day] = TradeRow(day, short, total)
    return list(trades.values())


def kis_source(rest) -> Source:
    """``rest`` is desk.futures_flow.KisRest (keys never leave it; its errors carry codes only).

    The KRX market code ``J``; one call covers the 20-session window (about 28 days).
    """
    gate = asyncio.Semaphore(KIS_PARALLEL)

    async def source(ticker: str, start: date, end: date) -> Publication:
        async with gate:
            body = await rest.get(KIS_SHORT_PATH, KIS_SHORT_TR, {
                "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker[:6],
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"), "FID_INPUT_DATE_2": end.strftime("%Y%m%d")})
        trades = parse_kis_trades(body)
        if trades and min(row.as_of for row in trades) > start:
            # KIS caps the rows per call (no paging here): older history points then show no data
            log.info("KIS short sales %s: rows from %s, window from %s", ticker, min(row.as_of for row in trades), start)
        return Publication(trades, [])
    return source


def connect(rest) -> KrxShortSales:
    """#979 the desk's service: trade shares from KIS when its client exists; no net-balance source."""
    return KrxShortSales(kis_source(rest) if rest is not None else None, balances=False, name="kis")


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

    def __init__(self, source: Source | None = None, *, clock: Callable[[], float] = time.monotonic,
                 balances: bool = True, name: str | None = None):
        self._source = source
        self._balances = balances      # False: the source publishes no net balance (#979, KIS)
        self.name = name if source is not None else None
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
        balance_status = "source_unavailable" if not self._balances else "source_error" if data.balance_error else (
            balance_points[-1]["status"] if balance_points else "no_data"
        )
        return {
            "ticker": ticker, "market": market, "status": trade_status, "source": self.name,
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
        except Exception as exc:  # noqa: BLE001 - source implementations can fail; preserve the last good publication
            # KisError text carries codes only; anything else (an httpx header error may quote the key) by type
            log.warning("kr short sales for %s failed: %s", ticker, kis_reason(exc))
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

    def _empty(self, ticker: str, market: str | None, status: str) -> dict:
        return {
            "ticker": ticker, "market": market, "status": status, "source": self.name,
            "trade": {**_series(status, []), "history": []}, "balance": _series(status, []),
        }
