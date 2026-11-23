"""독립적인 미국 시장 관측과 공개 발언, SEC 공시를 묶는 읽기 전용 서비스."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Literal

from . import day_call, market_calendar
from .single_flight import Flights

log = logging.getLogger(__name__)
INDEX = "^GSPC"
VIX = "^VIX"
HISTORY = 80
VIX_HISTORY = 7  # 장중 봉 한 개를 버려도 마감한 6거래일을 읽는다.
DRAWDOWN_WINDOW = 60
DRAWDOWN_LIMIT = -5.0
SOURCE_TIMEOUT = 2.0

Status = Literal["available", "stale", "missing", "error", "disabled"]
Direction = Literal["positive", "negative", "neutral"]
State = Literal["downtrend", "rebound_candidate", "recovery_watch", "undetermined"]


@dataclass(frozen=True)
class Observation:
    """추후 실제 시장 폭·신용 스프레드 원천이 제공할 관측과 개선 방향."""

    value: float
    source: str
    observed_at: datetime
    direction: Direction
    fetched_at: datetime | None = None


Candles = Callable[[str, str, int], Awaitable[dict]]
Measure = Callable[[], Awaitable[Observation | None]]


@dataclass(frozen=True)
class _Daily:
    closes: list[float] = field(default_factory=list)
    source: str | None = None
    fetched_at: datetime | None = None
    observed_at: datetime | None = None
    status: Status = "missing"
    reason: str | None = None


@dataclass(frozen=True)
class _Evidence:
    id: str
    unit: str
    status: Status
    reason: str | None = None
    source: str | None = None
    observed_at: str | None = None
    fetched_at: str | None = None
    age_days: int | None = None
    value: float | None = None
    direction: Direction | None = None
    details: dict = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC)
    return None


def _iso(stamp: datetime | None) -> str | None:
    return stamp.isoformat(timespec="seconds") if stamp is not None else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) else None


def _last_closed_day(symbol: str, now: datetime) -> date:
    day = market_calendar.local_today("us", now)
    zone, close = day_call.CLOSES[symbol]
    # day_call과 같은 보수적인 마감 기준을 써서 단축장 봉도 정규 시각까지 기다린다.
    if market_calendar.is_open("us", day) and now >= datetime.combine(day, close, zone):
        return day
    return market_calendar.previous_open("us", day)


def _daily(symbol: str, snapshot: dict, now: datetime) -> _Daily:
    raw_source = snapshot.get("source")
    source = raw_source.strip() if isinstance(raw_source, str) and raw_source.strip() else None
    fetched = _timestamp(snapshot.get("fetched_at"))
    # 장중 내려받은 캐시를 마감 봉으로 승격시키지 않는다.
    cutoff = min(now, fetched) if fetched is not None else now
    closed = {}
    for row in snapshot.get("bars") or ():
        if not isinstance(row, dict):
            continue
        opened = _timestamp(row.get("at"))
        price = _number(row.get("close"))
        if opened is None or price is None or price <= 0:
            continue
        day = market_calendar.local_today("us", opened)
        if not market_calendar.is_open("us", day):
            continue
        observed = day_call.closed_at(symbol, SimpleNamespace(at=row["at"]))
        if opened <= observed <= cutoff:
            closed[observed] = price
    if not closed:
        return _Daily(source=source, fetched_at=fetched, reason="no_closed_bars")
    sessions = sorted(closed)
    latest = sessions[-1].astimezone(UTC)
    latest_day = market_calendar.local_today("us", latest)
    stale = latest_day < _last_closed_day(symbol, now)
    # 중간에 빠진 거래일을 건너뛰면 5일 수익률과 20·50일 평균의 관측 기간이 바뀐다.
    contiguous = []
    day = latest_day
    for stamp in reversed(sessions):
        if stamp.date() != day:
            break
        contiguous.append(closed[stamp])
        day = market_calendar.previous_open("us", day)
    return _Daily(
        contiguous[::-1], source, fetched, latest,
        "stale" if stale else "available", "latest_session_missing" if stale else None,
    )


def _age(now: datetime, stamp: datetime) -> int:
    return (market_calendar.local_today("us", now) - market_calendar.local_today("us", stamp)).days


def _daily_evidence(id_: str, unit: str, daily: _Daily, now: datetime, value: float | None = None,
                    direction: Direction | None = None, **details: float) -> _Evidence:
    status, reason = daily.status, daily.reason
    if status == "available" and value is None:
        status, reason = "missing", "insufficient_history"
    at = daily.observed_at
    return _Evidence(id_, unit, status, reason, daily.source, _iso(at), _iso(daily.fetched_at),
                     _age(now, at) if at else None, round(value, 2) if value is not None else None,
                     direction, {key: round(amount, 2) for key, amount in details.items()})


def _pct(value: float, base: float) -> float:
    return (value - base) / base * 100


def _index_evidence(index: _Daily, now: datetime) -> tuple[_Evidence, _Evidence, _Evidence, bool]:
    values = index.closes
    trend = _daily_evidence("index_trend", "%", index, now)
    drawdown = _daily_evidence("index_drawdown", "%", index, now)
    rebound = _daily_evidence("index_rebound", "%", index, now)
    recent_drop = False
    if len(values) >= 50:
        close = values[-1]
        sma20, sma50 = sum(values[-20:]) / 20, sum(values[-50:]) / 50
        prev_sma20 = sum(values[-25:-5]) / 20
        direction: Direction = "neutral"
        if close > sma20 > sma50 and sma20 > prev_sma20:
            direction = "positive"
        elif close < sma50 and sma20 < sma50 and sma20 < prev_sma20:
            direction = "negative"
        trend = _daily_evidence("index_trend", "%", index, now, _pct(close, sma50), direction,
                                close=close, sma20=sma20, sma50=sma50)
    if len(values) >= DRAWDOWN_WINDOW:
        peak = max(values[-DRAWDOWN_WINDOW:])
        depth = _pct(values[-1], peak)
        drawdown = _daily_evidence("index_drawdown", "%", index, now, depth,
                                   "negative" if depth <= DRAWDOWN_LIMIT else "neutral", peak=peak)
        # 최근 20개 종가 각각을 당시까지의 고점과 비교한다. 뒤에 나온 고점은 과거 낙폭에 쓰지 않는다.
        recent_drop = any(
            _pct(values[end - 1], max(values[end - DRAWDOWN_WINDOW:end])) <= DRAWDOWN_LIMIT
            for end in range(max(DRAWDOWN_WINDOW, len(values) - 19), len(values) + 1)
        )
    if len(values) >= 6:
        ret5 = _pct(values[-1], values[-6])
        rebound = _daily_evidence("index_rebound", "%", index, now, ret5,
                                  "positive" if ret5 >= 2 else "negative" if ret5 <= -2 else "neutral")
    return trend, drawdown, rebound, recent_drop


def _volatility(vix: _Daily, now: datetime) -> _Evidence:
    if len(vix.closes) < 6:
        return _daily_evidence("volatility", "index_points", vix, now)
    last = vix.closes[-1]
    change5 = last - vix.closes[-6]
    return _daily_evidence("volatility", "index_points", vix, now, last,
                           "negative" if change5 >= 3 else "positive" if change5 <= -2 else "neutral",
                           change5=change5)


def _regime(index: _Daily, vix: _Daily, breadth: _Evidence, credit: _Evidence, now: datetime) -> dict:
    trend, drawdown, rebound, recent_drop = _index_evidence(index, now)
    volatility = _volatility(vix, now)
    evidence = [trend, drawdown, rebound, volatility, breadth, credit]
    positives = [e.id for e in evidence if e.status == "available" and e.direction == "positive"]
    negatives = [e.id for e in evidence if e.status == "available" and e.direction == "negative"]
    confirmations = {breadth.id, credit.id}
    cross_checks = confirmations | {volatility.id}
    state: State = "undetermined"
    checks = ["최신 마감 지수와 낙폭을 확인", "시장 폭·신용 스프레드·변동성의 방향을 대조"]
    if trend.status == drawdown.status == "available":
        if (recent_drop and trend.direction == "positive" and rebound.direction != "negative"
                and confirmations.intersection(positives) and not cross_checks.intersection(negatives)):
            state = "recovery_watch"
            checks = ["지수가 20일선과 50일선 위에서 흐름을 유지하는지 확인",
                      "시장 폭 확대 또는 신용 스프레드 개선이 이어지는지 확인"]
        elif drawdown.direction == "negative" and trend.direction in ("negative", "neutral"):
            if (rebound.direction == "positive" and cross_checks.intersection(positives)
                    and not cross_checks.intersection(negatives)):
                state = "rebound_candidate"
                checks = ["5거래일 반등이 이어지는지 확인", "20일·50일 구조와 시장 폭·신용을 별도로 확인"]
            elif (trend.direction == "negative" and rebound.direction != "positive"
                  and not confirmations.intersection(positives)):
                state = "downtrend"
                checks = ["낙폭이 더 커지거나 5거래일 반등이 나타나는지 확인", "변동성·시장 폭·신용 악화를 확인"]
    supporting, opposing = (negatives, positives) if state == "downtrend" else (positives, negatives)
    return {"state": state, "supporting": supporting, "opposing": opposing,
            "next_checks": checks, "evidence": [asdict(e) for e in evidence]}


class MarketOutlook:
    def __init__(self, *, candles: Candles | None = None, filing: Callable[[], dict] | None = None,
                 views: Callable[[date], list[dict]] | None = None, breadth: Measure | None = None,
                 credit: Measure | None = None, clock: Callable[[], datetime] = _utcnow,
                 timeout: float = SOURCE_TIMEOUT) -> None:
        self._candles = candles
        self._filing = filing
        self._views = views
        self._breadth = breadth
        self._credit = credit
        self._clock = clock
        self._timeout = timeout
        self._flights = Flights()

    async def _read_daily(self, symbol: str, now: datetime) -> _Daily:
        if self._candles is None:
            return _Daily(status="disabled", reason="source_unconfigured")
        try:
            async with asyncio.timeout(self._timeout):
                snapshot = await self._candles(symbol, "1d", VIX_HISTORY if symbol == VIX else HISTORY)
            return _daily(symbol, snapshot, now)
        except TimeoutError:
            return _Daily(status="error", reason="timeout")
        except Exception as exc:  # noqa: BLE001 - one failed source must leave the other sections readable
            log.info("market outlook %s failed (%s)", symbol, type(exc).__name__)
            return _Daily(status="error", reason="source_error")

    async def _measure(self, id_: str, unit: str, read: Measure | None, now: datetime) -> _Evidence:
        if read is None:
            return _Evidence(id_, unit, "disabled", "source_unconfigured")
        try:
            async with asyncio.timeout(self._timeout):
                item = await read()
        except TimeoutError:
            return _Evidence(id_, unit, "error", "timeout")
        except Exception as exc:  # noqa: BLE001 - one failed source must leave the other sections readable
            log.info("market outlook %s failed (%s)", id_, type(exc).__name__)
            return _Evidence(id_, unit, "error", "source_error")
        if item is None:
            return _Evidence(id_, unit, "missing", "no_observation")
        if not isinstance(item, Observation):
            return _Evidence(id_, unit, "missing", "invalid_observation")
        at = _timestamp(item.observed_at)
        val = _number(item.value)
        if (at is None or at > now or val is None or not isinstance(item.source, str) or not item.source.strip()
                or item.direction not in ("positive", "negative", "neutral")):
            return _Evidence(id_, unit, "missing", "invalid_observation")
        # 보조 지표는 공개 지연을 감안해 직전 거래일 자료까지 판정에 쓴다.
        recent = _last_closed_day(INDEX, now)
        stale = market_calendar.local_today("us", at) < market_calendar.previous_open("us", recent)
        return _Evidence(id_, unit, "stale" if stale else "available", "publication_lag" if stale else None,
                         item.source, _iso(at), _iso(_timestamp(item.fetched_at)), _age(now, at), val, item.direction)

    async def _read_filing(self) -> dict:
        if self._filing is None:
            return {"status": "disabled", "reason": "source_unconfigured", "items": []}
        try:
            async with asyncio.timeout(self._timeout):
                snapshot = await self._flights.run("filing", lambda: asyncio.to_thread(self._filing))
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("items"), list):
                raise TypeError("invalid filings snapshot")
            return snapshot
        except TimeoutError:
            return {"status": "error", "reason": "timeout", "items": []}
        except Exception as exc:  # noqa: BLE001 - one failed source must leave the other sections readable
            log.info("market outlook filings failed (%s)", type(exc).__name__)
            return {"status": "error", "reason": "source_error", "items": []}

    def _read_views(self, now: datetime) -> tuple[list[dict], dict]:
        if self._views is None:
            return [], {"status": "disabled", "reason": "source_unconfigured"}
        try:
            items = self._views(market_calendar.local_today("us", now))
            if not isinstance(items, list):
                raise TypeError("invalid views snapshot")
        except Exception as exc:  # noqa: BLE001 - one failed source must leave the other sections readable
            log.info("market outlook views failed (%s)", type(exc).__name__)
            return [], {"status": "error", "reason": "source_error"}
        return items, {"status": "available" if items else "missing", "reason": None if items else "no_views"}

    async def read(self) -> dict:
        now = self._clock().astimezone(UTC)
        index, vix, breadth, credit, filings = await asyncio.gather(
            self._read_daily(INDEX, now),
            self._read_daily(VIX, now),
            self._measure("breadth", "%", self._breadth, now),
            self._measure("credit_spread", "bps", self._credit, now),
            self._read_filing(),
        )
        views, views_status = self._read_views(now)
        filed = {key: value for key, value in filings.items() if key != "items"}
        return {"market": "us", "as_of": _iso(now), "views": views, "filings": filings.get("items", []),
                "regime": _regime(index, vix, breadth, credit, now),
                "sources": {"filings": filed, "views": views_status}}
