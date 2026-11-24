"""Point-in-time daily resistance evidence from chart-read's own candle snapshot."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from . import channels, chart_read, trend
from . import market_calendar as calendar

_INSTRUMENTS = {
    "SOXX": ("etf", "USD"),
    "SOXL": ("etf", "USD"),
    "^NDX": ("cash_index", "index_points"),
    "NQ=F": ("continuous_future", "index_points"),
}
_UNSUPPORTED = frozenset({"SOXLUSDT", "SOXLUSDT.P", "SOXL-USDT", "NAS100", "NAS100.CFD"})
_LAG_MINUTES = {"us": 15, "cme": 10}
_WICK_RATIO = 0.40
_BODY_RATIO = 0.60
_CLOSE_POSITION = 0.75
_BODY_ATR = 0.80
_DAY = timedelta(days=1)


@dataclass(frozen=True)
class _Session:
    market: str
    day: date
    opens_at: datetime
    closes_at: datetime

    @property
    def confirm_after(self) -> datetime:
        return self.closes_at + timedelta(minutes=_LAG_MINUTES[self.market])

    def view(self) -> dict:
        return {"market": self.market, "trade_date": self.day.isoformat(),
                "opens_at": _utc(self.opens_at), "closes_at": _utc(self.closes_at),
                "confirm_after": _utc(self.confirm_after)}


def eligible(ticker: str) -> bool:
    return ticker in _INSTRUMENTS or ticker in _UNSUPPORTED


def unavailable(ticker: str, now: datetime, reason: str) -> dict:
    product = _INSTRUMENTS.get(ticker)
    return {"ticker": ticker, "timeframe": "1d", "status": "unavailable", "reason": reason,
            "instrument": {"kind": product[0], "unit": product[1]} if product else None,
            "observed_at": _utc(now), "source": None, "fetched_at": None, "session": None, "bar": None,
            "previous_close": None, "resistance": None, "metrics": None, "reaction": None, "volume": None}


def _utc(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="seconds")


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _cme_uncertain(day: date) -> bool:
    # The shared calendar knows ordinary Globex hours, and flags equity holidays or early closes.
    # The latter are a warning of a possible CME exception, without supplying that market's hours.
    return not calendar.is_open("us", day) or calendar.us_early_close(day) is not None


def _session(market: str, day: date) -> _Session | None:
    if market == "us":
        if not calendar.is_open(market, day):
            return None
        opens, closes = calendar.session_bounds(market, day)
    else:
        if _cme_uncertain(day):
            return None
        opens = datetime.combine(day - _DAY, calendar.CME_OPENS, calendar.ET)
        closes = datetime.combine(day, calendar.CME_CLOSES, calendar.ET)
    return _Session(market, day, opens, closes)


def _previous_day(market: str, day: date) -> date:
    if market == "us":
        return calendar.previous_open(market, day)
    prior = day - _DAY
    return prior - timedelta(days=max(0, prior.weekday() - 4))


def _target(market: str, now: datetime) -> date:
    local = now.astimezone(calendar.ET)
    day = local.date()
    if market == "us":
        if calendar.is_open(market, day) and local.time() >= calendar.US_OPEN:
            return day
        return _previous_day(market, day)
    if local.time() >= calendar.CME_OPENS and local.weekday() != 4:
        day += _DAY
    return day - timedelta(days=max(0, day.weekday() - 4))


def cache_phase(ticker: str, now: datetime) -> tuple[date, str] | None:
    """Key chart-read's existing cache by the daily observation's calendar phase."""
    if ticker not in _INSTRUMENTS:
        return None
    market = "cme" if ticker == "NQ=F" else "us"
    day = _target(market, now)
    session = _session(market, day)
    if session is None:
        return day, "unverified"
    if now < session.closes_at:
        return day, "open"
    if now < session.confirm_after:
        return day, "awaiting"
    return day, "closed"


def _number(value: object) -> bool:
    # The upper bound leaves headroom for percentage, log projection and true-range arithmetic.
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 1e-100 <= value <= 1e100


def _bar_rows(rows: list) -> dict[date, dict] | None:
    by_day = {}
    for row in rows:
        stamp = _timestamp(row.get("at")) if isinstance(row, dict) else None
        if stamp is None:
            return None
        # Both Yahoo US daily bars and PriceService's rebuilt Globex rows use midnight in
        # New York as the trade-day label. A reopened CME session can carry tomorrow's label.
        local = stamp.astimezone(calendar.ET)
        if local.time() != time.min:
            return None
        day = local.date()
        if day in by_day or any(not _number(row.get(key)) for key in ("open", "high", "low", "close")):
            return None
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            return None
        by_day[day] = row
    return by_day


def _bar_view(row: dict) -> dict:
    return {key: row[key] for key in ("at", "open", "high", "low", "close")}


def _resistance(history: list[dict], target: dict, atr: float) -> dict | None:
    last = history[-1]
    previous = last["close"]
    limit = previous + chart_read.MAX_DISTANCE_ATR * atr
    found, _ = channels.pick(history, "1d", atr / previous, atr)
    candidates = []
    for ch in found:
        projected = ch.upper(len(history))
        if projected > math.log(limit) or ch.upper(len(history) - 1) < math.log(previous):
            continue
        price = math.exp(projected)
        if price >= previous:
            ref = {"kind": "channel", "edge": "upper",
                   "id": f"1d-channel-{history[ch.i]['at']}-{history[ch.j]['at']}"}
            candidates.append((price, "channel_upper", ref))
    if not candidates:
        window = history[-chart_read.SWING_BARS:]
        for i in trend._swing_idx(window, channels.SWING_K["1d"], "high"):
            row = window[i]
            price = row["high"]
            if previous <= price <= limit and all(r["close"] <= price for r in window[i + 1:]):
                candidates.append((price, "swing_high", {"kind": "swing", "side": "high", "at": row["at"]}))
    if not candidates:
        return None
    price, kind, ref = min(candidates, key=lambda item: item[0])
    return {"kind": kind, "price": price, "basis_at": last["at"], "projected_at": target["at"], "ref": ref}


def _metrics(row: dict, atr: float | None, resistance: dict | None) -> dict:
    opens, high, low, close = (row[key] for key in ("open", "high", "low", "close"))
    span = high - low
    body = abs(close - opens)
    return {"range": span, "body_ratio": body / span if span else None,
            "upper_wick_ratio": (high - max(opens, close)) / span if span else None,
            "close_position": (close - low) / span if span else None, "body_atr": body / atr if atr else None,
            "close_distance_pct": (close / resistance["price"] - 1) * 100 if resistance else None}


def _reaction(row: dict, resistance: dict, metrics: dict, atr: float) -> dict:
    line = resistance["price"]
    band = channels.TOUCH_ATR * atr
    touched = row["high"] >= line - band and row["low"] <= line + band
    near = row["high"] >= line - channels.TEST_ATR * atr and row["low"] <= line + band
    breakout = row["close"] > line + channels.BREAK_ATR * atr
    wick = touched and (metrics["upper_wick_ratio"] or 0) >= _WICK_RATIO and row["close"] < line - band
    strong = (row["close"] > row["open"] and (metrics["body_ratio"] or 0) >= _BODY_RATIO
              and (metrics["close_position"] or 0) >= _CLOSE_POSITION and metrics["body_atr"] >= _BODY_ATR)
    priority = (("close_breakout", breakout), ("wick_rejection", wick), ("strong_bullish_close", strong),
                ("resistance_touch", touched), ("resistance_near", near))
    return {"kind": next((kind for kind, matched in priority if matched), "none"),
            "near": near, "touched": touched, "wick_rejection": wick,
            "strong_bullish_close": strong, "close_breakout": breakout}


def _volume(ticker: str, day: date, history: dict[date, dict], target: dict) -> dict | None:
    if _INSTRUMENTS[ticker][0] != "etf" or calendar.us_early_close(day):
        return None
    comparison = []
    for _ in range(chart_read.VOLUME_BARS):
        day = calendar.previous_open("us", day)
        row = history.get(day)
        if not row or calendar.us_early_close(day):
            return None
        comparison.append(row)
    rows = [*reversed(comparison), target]
    if any(not _number(row.get("volume")) for row in rows):
        return None
    return chart_read.volume(rows)


def build(ticker: str, daily: dict | None, now: datetime) -> dict:
    """Read one verified product and session, with resistance fitted before its candle."""
    if ticker not in _INSTRUMENTS:
        return unavailable(ticker, now, "product_unsupported")
    market = "cme" if ticker == "NQ=F" else "us"
    day = _target(market, now)
    session = _session(market, day)
    if session is None:
        return unavailable(ticker, now, "session_unverified")
    result = unavailable(ticker, now, "missing_daily_source")
    result["session"] = session.view()
    if daily is None:
        return result
    if daily.get("ticker") != ticker or daily.get("interval") != "1d":
        return {**result, "reason": "product_mismatch"}
    if daily.get("source") != "yahoo":
        return {**result, "reason": "source_unverified"}
    checked = _timestamp(daily.get("fetched_at"))
    if checked is None or checked > now + timedelta(seconds=5):
        return {**result, "reason": "confirmation_unverified", "source": "yahoo"}
    result.update(source="yahoo", fetched_at=_utc(checked))
    rows = daily.get("bars")
    by_day = _bar_rows(rows) if isinstance(rows, list) else None
    if by_day is None:
        return {**result, "reason": "invalid_bar"}
    target = by_day.get(day)
    if now < session.confirm_after:
        reason = "session_open" if now < session.closes_at else "awaiting_confirmation"
        return {**result, "status": "in_progress", "reason": reason, "bar": _bar_view(target) if target else None}
    if checked < session.confirm_after:
        return {**result, "reason": "source_delayed"}
    if target is None:
        return {**result, "reason": "bar_missing"}

    result.update(bar=_bar_view(target), metrics=_metrics(target, None, None))
    prior = _previous_day(market, day)
    if _session(market, prior) is None:
        return {**result, "reason": "session_unverified"}
    if prior not in by_day:
        return {**result, "reason": "previous_bar_missing"}
    previous = by_day[prior]
    history = [row for d, row in sorted(by_day.items()) if d < day and (
        calendar.is_open("us", d) if market == "us" else d.weekday() < 5)]
    atr = channels.atr(history)
    result.update(previous_close={"at": previous["at"], "price": previous["close"]},
                  volume=_volume(ticker, day, by_day, target))
    resistance = _resistance(history, target, atr) if atr else None
    metrics = _metrics(target, atr, resistance)
    if resistance is None:
        return {**result, "reason": "resistance_unavailable", "metrics": metrics}
    return {**result, "status": "confirmed", "reason": None, "resistance": resistance, "metrics": metrics,
            "reaction": _reaction(target, resistance, metrics, atr)}
