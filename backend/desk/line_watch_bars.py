"""Closed market bars for line monitoring and the corresponding alert chart."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from . import market_calendar as calendar
from .indicators import annotate

TIMEFRAMES = ("5m", "10m", "15m", "60m", "120m", "240m", "1d", "1w")
MINUTES = dict(zip(TIMEFRAMES, (5, 10, 15, 60, 120, 240, 1440, 10080), strict=True))
ALERT_CHART_FRAMES = frozenset({"10m", "120m"})
_INPUT = {
    "5m": ("5m", 400), "10m": ("5m", 800), "15m": ("15m", 400),
    "60m": ("60m", 500), "120m": ("60m", 820), "240m": ("60m", 1220),
    "1d": ("1d", 300), "1w": ("1d", 1250),
}
_SUPPORTED_EXAMPLES = ("NQ=F", "^KS11", "005930.KS", "000660.KS", "SOXL", "KORU")
# Yahoo's exchange delay varies even among US venues. Use the most conservative
# supported bound so a timely HTTP fetch is not mistaken for finalized OHLC.
_LAG_MINUTES = {"CME": 10, "KRX": 20, "US": 15}
_CLOSE_DELAY = timedelta(seconds=20)
_FETCH_SLACK = timedelta(seconds=5)
_DAY = timedelta(days=1)
_WEEK = timedelta(days=7)
_SOURCES = frozenset({"yahoo", "toss", "yahoo+toss", "mock"})


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Plan:
    expected: Window | None
    next_at: datetime
    reason: str | None = None
    prior: Window | None = None


@dataclass(frozen=True)
class Sample:
    bars: list[dict]
    reason: str | None
    source: str | None = None
    fetched_at: datetime | None = None
    lag_minutes: int | None = None


def market_of(ticker: str) -> str | None:
    if ticker == "NQ=F":
        return "CME"
    if re.fullmatch(r"(?:[0-9]{6}\.(?:KS|KQ)|\^K(?:S|Q)[0-9]+)", ticker):
        return "KRX"
    if not ticker.endswith("-USD") and re.fullmatch(r"[A-Z]{1,5}(?:[.-][A-Z])?", ticker):
        return "US"
    return None


def allowed_support(ticker: str | None = None) -> dict:
    choices = (*_SUPPORTED_EXAMPLES, ticker) if ticker is not None else _SUPPORTED_EXAMPLES
    support = {}
    for symbol in choices:
        market = market_of(symbol)
        support[symbol] = {"market": market, "supported": list(TIMEFRAMES) if market else [],
                           "unsupported": {} if market else dict.fromkeys(TIMEFRAMES, "unsupported_market")}
    return support


def source_request(timeframe: str) -> tuple[str, int]:
    return _INPUT[timeframe]


def _zone(market: str):
    return calendar.KST if market == "KRX" else calendar.ET


def _cme_uncertain(day: date) -> bool:
    # There is no CME holiday schedule in the app. A NYSE holiday/half day is a cue
    # to suppress the affected futures session, not a claim that CME is closed then.
    return day.weekday() < 5 and (not calendar.is_open("us", day) or calendar.us_early_close(day) is not None)


@lru_cache(maxsize=4096)
def _session(market: str, day: date, *, nominal: bool = False) -> Window | None:
    if market == "CME":
        if day.weekday() >= 5 or (not nominal and _cme_uncertain(day)):
            return None
        start = datetime.combine(day - _DAY, calendar.CME_OPENS, calendar.ET)
        end = datetime.combine(day, calendar.CME_CLOSES, calendar.ET)
    else:
        exchange = "kr" if market == "KRX" else "us"
        if not calendar.is_open(exchange, day):
            return None
        start, end = calendar.session_bounds(exchange, day)
    return Window(start.astimezone(UTC), end.astimezone(UTC))


@lru_cache(maxsize=1024)
def _week(market: str, monday: date, *, nominal: bool = False) -> tuple[Window, ...]:
    days = [monday + i * _DAY for i in range(5)]
    # Unknown CME closes also make the week incomplete rather than moving its
    # purported final close back to the previous known session.
    if market == "CME" and not nominal and any(_cme_uncertain(day) for day in days):
        return ()
    return tuple(window for day in days if (window := _session(market, day, nominal=nominal)) is not None)


def _monday(day: date) -> date:
    return day - day.weekday() * _DAY


@lru_cache(maxsize=512)
def _windows(session: Window, tf: str) -> tuple[Window, ...]:
    if tf == "1d":
        return (session,)
    width = timedelta(minutes=MINUTES[tf])
    start = session.start
    windows = []
    while start < session.end:
        end = min(start + width, session.end)
        # A five-minute remainder at the close is not a ten-minute aggregate.
        if tf != "10m" or end - start == width:
            windows.append(Window(start, end))
        start = end
    return tuple(windows)


def _fresh_for(tf: str) -> timedelta:
    return timedelta(minutes=min(60, max(2 * MINUTES[tf], 10)))


def plan(market: str, tf: str, now: datetime) -> Plan:
    """Due bar and next useful read; never replay a stale period after a restart."""
    day = now.astimezone(_zone(market)).date()
    fallback = now + timedelta(minutes=15)
    if market == "CME":
        trade_day = calendar.cme_trade_day(now)
        nominal = _session(market, trade_day, nominal=True)
        if nominal and nominal.start <= now <= nominal.end + _CLOSE_DELAY and _cme_uncertain(trade_day):
            return Plan(None, fallback, "session_unverified")
    if tf == "1w":
        monday = _monday(day)
        candidates = []
        for i in range(-2, 3):
            start = monday + i * _WEEK
            week = _week(market, start, nominal=True)
            if week:
                candidates.append((Window(week[0].start, week[-1].end), bool(_week(market, start))))
    else:
        days = [day + i * _DAY for i in range(-15, 9)]
        sessions = ((_session(market, d, nominal=True), _session(market, d) is not None) for d in days)
        candidates = [(bar, verified) for session, verified in sessions if session for bar in _windows(session, tf)]
    lag = timedelta(minutes=_LAG_MINUTES[market])
    boundary = now - _CLOSE_DELAY - lag
    index = next((i for i in range(len(candidates) - 1, -1, -1) if candidates[i][0].end <= boundary), -1)
    following = candidates[index + 1][0] if index + 1 < len(candidates) else None
    next_at = following.end + _CLOSE_DELAY + lag if following else fallback
    if index < 1:
        return Plan(None, next_at, "session_closed")
    previous, verified = candidates[index]
    prior, prior_verified = candidates[index - 1]
    if now - previous.end - lag > _fresh_for(tf):
        waiting = following is not None and following.end <= now
        return Plan(None, next_at, "source_delayed" if waiting else "stale_bar")
    if not (verified and prior_verified):
        return Plan(None, next_at, "session_unverified")
    return Plan(previous, next_at, prior=prior)


def _timestamp(value: object) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
        return stamp.astimezone(UTC) if stamp is not None and stamp.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _valid_row(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    prices = [row.get(key) for key in ("open", "high", "low", "close")]
    if any(type(value) not in (int, float) or not 0 < value < 1e30 for value in prices):
        return False
    opening, high, low, close = prices
    return low <= min(opening, close) <= max(opening, close) <= high


def _combine(start: datetime, rows: list[dict]) -> dict:
    return {"at": start.isoformat(timespec="seconds"), "open": rows[0]["open"],
            "high": max(row["high"] for row in rows), "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"],
            "volume": sum(row.get("volume", 0) for row in rows
                          if type(row.get("volume")) in (float, int) and 0 <= row["volume"] < 1e30)}


def _intraday(market: str, tf: str, rows: dict[datetime, dict], cutoff: datetime) -> dict[datetime, dict]:
    width = timedelta(minutes=MINUTES[tf])
    base = timedelta(minutes=MINUTES[_INPUT[tf][0]])
    groups = {}
    for stamp in rows:
        day = calendar.cme_trade_day(stamp) if market == "CME" else stamp.astimezone(_zone(market)).date()
        session = _session(market, day)
        if session is None or not session.start <= stamp < session.end or (stamp - session.start) % base:
            continue
        start = session.start + (stamp - session.start) // width * width
        groups[start] = min(start + width, session.end)
    out = {}
    for start, end in groups.items():
        if end > cutoff or (tf == "10m" and end - start != width):
            continue
        # Hourly source bars can have a short final member at a verified equity close.
        members = [rows.get(start + i * base) for i in range(math.ceil((end - start) / base))]
        if all(member is not None for member in members):
            out[end] = _combine(start, members)
    return out


def _daily(market: str, tf: str, rows: dict[datetime, dict], cutoff: datetime) -> dict[datetime, dict]:
    daily = {}
    weeks = set()
    for stamp, row in rows.items():
        day = market_calendar_day(market, stamp)
        session = _session(market, day)
        if session is not None and session.end <= cutoff:
            daily[session.end] = row
            weeks.add(_monday(day))
    if tf == "1d":
        return daily
    out = {}
    for monday in weeks:
        sessions = _week(market, monday)
        if sessions and all(s.end in daily for s in sessions):
            out[sessions[-1].end] = _combine(sessions[0].start, [daily[s.end] for s in sessions])
    return out


def market_calendar_day(market: str, stamp: datetime) -> date:
    return stamp.astimezone(_zone(market)).date()


def _unique_rows(got: dict) -> dict[datetime, dict]:
    stamps = {}
    duplicates = set()
    for row in got.get("bars") or []:
        if _valid_row(row) and (stamp := _timestamp(row.get("at"))) is not None:
            if stamp in stamps:
                duplicates.add(stamp)
            stamps[stamp] = row
    for stamp in duplicates:
        stamps.pop(stamp)
    return stamps


def alert_chart_history(market: str, tf: str, got: dict, now: datetime) -> Sample:
    """Reuse the watch's session grid and cutoff without requiring a currently due signal."""
    source = got.get("source")
    checked = _timestamp(got.get("fetched_at"))
    if checked is None or checked > now + _FETCH_SLACK:
        return Sample([], "confirmation_unverified")
    if not isinstance(source, str) or source not in _SOURCES:
        return Sample([], "source_unverified")
    lag_minutes = _LAG_MINUTES[market]
    cutoff = min(now, checked - _FETCH_SLACK) - timedelta(minutes=lag_minutes)
    bars = _intraday(market, tf, _unique_rows(got), cutoff)
    return Sample([bars[end] for end in sorted(bars)], None if bars else "bar_missing",
                  source, checked, lag_minutes)


async def alert_chart(candles, ticker: str, tf: str, count: int, indicators: tuple[str, ...]) -> dict:
    market = market_of(ticker)
    if market is None:
        history = Sample([], "unsupported_market")
    else:
        interval, source_count = source_request(tf)
        got = await candles(ticker, interval, source_count)
        history = alert_chart_history(market, tf, got, datetime.now(UTC))
    annotate(history.bars, indicators)
    count = max(count, 1)
    return {"ticker": ticker, "interval": tf, "bars": history.bars[-count:],
            "source": history.source or "", "fetched_at": history.fetched_at.isoformat() if history.fetched_at else "",
            "delay_sec": history.lag_minutes * 60 if history.lag_minutes is not None else None,
            "indicators": list(indicators), "truncated": len(history.bars) < count}


def sample(market: str, tf: str, plan: Plan, got: dict, now: datetime) -> Sample:
    """Only the exact due bar can trigger; missing/stale/partial source data cannot substitute another."""
    if plan.expected is None:
        return Sample([], plan.reason)
    lag_minutes = _LAG_MINUTES[market]
    lag = timedelta(minutes=lag_minutes)
    if now - plan.expected.end - lag > _fresh_for(tf):
        return Sample([], "stale_bar")
    checked = _timestamp(got.get("fetched_at"))
    if checked is None or checked > now + _FETCH_SLACK:
        return Sample([], "confirmation_unverified")
    cutoff = min(now, checked - _FETCH_SLACK) - lag
    if plan.expected.end > cutoff:
        return Sample([], "source_delayed")
    stamps = _unique_rows(got)
    bars = (_daily if tf in {"1d", "1w"} else _intraday)(market, tf, stamps, cutoff)
    if plan.expected.end not in bars or (plan.prior is not None and plan.prior.end not in bars):
        return Sample([], "bar_missing")
    source = got.get("source")
    source = source if isinstance(source, str) and source in _SOURCES else None
    if source is None:
        return Sample([], "source_unverified")
    history = [bars[end] for end in sorted(bars) if end <= plan.expected.end]
    return Sample(history, None, source, checked, lag_minutes)
