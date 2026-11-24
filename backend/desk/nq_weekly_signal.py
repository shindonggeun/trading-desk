"""Closed NQ weekly oversold observations and bounded retrospective measurements (#914)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median

from . import indicators, market_calendar
from . import nq_weekly_research as nq
from . import weekly_chart_evidence as chart

TICKER = "NQ=F"
METHOD_VERSION = "nq-weekly-oversold-v1"
PRICE_CONDITIONS = tuple(chart.PRICE_KEYS)
DEFAULT_SETTINGS = {"enabled": False, "rsi_limit": 30.0, "price_conditions": list(PRICE_CONDITIONS), "min_hits": 1}
FETCH_WEEKS = 120
DEFAULT_HISTORY_WEEKS = 52
MAX_HISTORY_WEEKS = 104
HORIZONS = (4, 12)


def validate_settings(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.keys() != DEFAULT_SETTINGS.keys():
        raise ValueError("expected enabled, rsi_limit, price_conditions and min_hits")
    enabled, limit, conditions, minimum = (raw[key] for key in DEFAULT_SETTINGS)
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    if type(limit) not in (int, float) or not 0 <= limit <= 100:
        raise ValueError("rsi_limit must be a number from 0 to 100")
    if (not isinstance(conditions, list) or not 1 <= len(conditions) <= len(PRICE_CONDITIONS)
            or any(type(key) is not str or key not in PRICE_CONDITIONS for key in conditions)
            or len(set(conditions)) != len(conditions)):
        raise ValueError("price_conditions must contain unique bollinger, channel or fib values")
    if type(minimum) is not int or not 1 <= minimum <= len(conditions):
        raise ValueError("min_hits must be from 1 to the number of selected conditions")
    return {"enabled": enabled, "rsi_limit": float(limit),
            "price_conditions": [key for key in PRICE_CONDITIONS if key in conditions], "min_hits": minimum}


def _confirmed_at(monday: date) -> datetime:
    return datetime.combine(monday + timedelta(days=6), market_calendar.CME_OPENS, nq.NY).astimezone(UTC)


def _week(monday: date, fetched: datetime | None) -> dict:
    return {"start": monday.isoformat(), "closed_at": nq._week_closed_at(monday).astimezone(UTC).isoformat(),
            "confirmed_at": _confirmed_at(monday).isoformat(),
            "fetched_at": fetched.isoformat() if fetched else None, "source": chart.SOURCE if fetched else None}


@dataclass(frozen=True)
class _Weekly:
    fetched: datetime | None
    confirmed: list[dict]
    reason: str | None
    forming: dict | None


def _forming_week(now: datetime, labels: list[date]) -> dict | None:
    today = now.astimezone(nq.NY).date()
    monday = today - timedelta(days=today.weekday())
    if now >= _confirmed_at(monday):
        monday += nq.WEEK
    if now >= nq._week_closed_at(monday):
        return None
    return {"start": monday.isoformat(), "source_present": monday in labels, "status": "pending"}


def _read(weekly: dict | None, now: datetime) -> _Weekly:
    if now.utcoffset() is None:
        raise ValueError("now must include a UTC offset")
    if not isinstance(weekly, dict):
        return _Weekly(None, [], "weekly_source_unavailable", _forming_week(now, []))
    if weekly.get("source") != chart.SOURCE:
        return _Weekly(None, [], "unsupported_weekly_source", _forming_week(now, []))
    fetched = chart._timestamp(weekly.get("fetched_at"))
    if fetched is None or fetched > now:
        return _Weekly(None, [], "invalid_weekly_timestamp", _forming_week(now, []))
    raw = weekly.get("bars")
    if isinstance(raw, list):
        raw = raw[-FETCH_WEEKS:]
    try:
        closed = nq._closed(raw, fetched)
        labels = [nq._monday(row["at"]) for row in raw]
        confirmed = [row for i, row in enumerate(closed)
                     if i + 1 < len(labels) and labels[i + 1] == labels[i] + nq.WEEK
                     and fetched >= _confirmed_at(labels[i])]
    except (TypeError, ValueError, ArithmeticError):
        return _Weekly(fetched, [], "invalid_weekly_bars", _forming_week(now, []))
    forming = _forming_week(now, labels)
    if not closed:
        return _Weekly(fetched, [], "no_completed_week", forming)
    expected = chart._expected_week(now)
    if now < _confirmed_at(expected):
        return _Weekly(fetched, confirmed, "unconfirmed_weekly_close", forming)
    if closed[-1]["at"] != expected.isoformat():
        return _Weekly(fetched, confirmed, "stale_weekly_bars", forming)
    if not confirmed or confirmed[-1]["at"] != expected.isoformat():
        return _Weekly(fetched, confirmed, "unconfirmed_weekly_close", forming)
    return _Weekly(fetched, confirmed, None, forming)


def _evidence_at(bars: list[dict], bands: list, rsis: list, index: int, settings: dict) -> tuple[dict, list[str]]:
    bar, rsi = bars[index], rsis[index]
    basis = chart._at(bars, bands, index)
    previous = chart._evidence(bar, basis, rsi, None)
    limit = settings["rsi_limit"]
    rsi_valid = rsi is not None and math.isfinite(rsi)
    rsi_status = ("hit" if rsi <= limit else "miss") if rsi_valid else "unknown"
    evidence = {"rsi": {"value": rsi if rsi_valid else None, "limit": limit, "status": rsi_status,
                        "method": chart.METHODS["rsi"],
                        "reason": "invalid_rsi" if rsi is not None and not rsi_valid else previous["rsi"]["reasonCode"]}}
    hits = []
    for key in settings["price_conditions"]:
        row = previous[key]
        state = row["state"]
        status = "hit" if state == "near" else "unknown" if state == "missing" else "miss"
        evidence[key] = {
            "value": row["value"], "actual_low": bar["low"], "actual_close": bar["close"],
            "tolerance": basis.atr * nq.APPROACH_ATR if row["value"] is not None else None,
            "status": status, "state": state, "method": row["method"],
            "anchor_weeks": row["anchorWeeks"], "reason": row["reasonCode"],
        }
        if status == "hit":
            hits.append(key)
    return evidence, hits


def _point(bars: list[dict], bands: list, rsis: list, index: int, settings: dict) -> dict:
    evidence, hits = _evidence_at(bars, bands, rsis, index, settings)
    rsi = evidence["rsi"]["status"]
    minimum = settings["min_hits"]
    unknown = sum(evidence[key]["status"] == "unknown" for key in settings["price_conditions"])
    if rsi == "hit" and len(hits) >= minimum:
        status, reason = "matched", None
    elif rsi == "miss" or len(hits) + unknown < minimum:
        status, reason = "not_matched", "rsi_above_limit" if rsi == "miss" else "too_few_price_hits"
    else:
        status, reason = "unavailable", "insufficient_overlap_evidence"
    return {"status": status, "reason": reason, "evidence": evidence,
            "matched_conditions": hits, "min_hits": minimum}


def _series(bars: list[dict]) -> tuple[list, list]:
    closes = [bar["close"] for bar in bars]
    return indicators.bollinger(closes, nq.BB_WEEKS, nq.BB_MULT), indicators.rsi(closes, nq.RSI_WEEKS)


def _current(weekly: _Weekly, bands: list, rsis: list, settings: dict, now: datetime) -> dict:
    reason = weekly.reason
    root = {"observed_at": now.astimezone(UTC).isoformat(), "method_version": METHOD_VERSION,
            "rollover": "unknown", "forming_week": weekly.forming}
    expected = chart._expected_week(now)
    week = _week(expected, weekly.fetched)
    if reason:
        point = {"status": "pending" if reason == "unconfirmed_weekly_close" else "unavailable",
                 "reason": reason, "evidence": {}, "matched_conditions": [], "min_hits": settings["min_hits"]}
        return {**root, "week": week, **point}
    point = _point(weekly.confirmed, bands, rsis, len(weekly.confirmed) - 1, settings)
    return {**root, "week": week, **point}


def observe(weekly: dict | None, settings: dict, now: datetime) -> dict:
    """Evaluate only the latest conservatively confirmed week, with no historical replay."""
    rules = validate_settings(settings)
    read = _read(weekly, now)
    if read.reason:
        return _current(read, [], [], rules, now)
    try:
        return _current(read, *_series(read.confirmed), rules, now)
    except (ValueError, ArithmeticError):
        return _current(_Weekly(read.fetched, [], "weekly_evidence_error", read.forming), [], [], rules, now)


def _follow_up(bars: list[dict], index: int, horizon: int) -> dict:
    anchor = bars[index]
    following = bars[index + 1:index + horizon + 1]
    if len(following) != horizon:
        return {"status": "unverified", "through_week": None, "return_pct": None, "max_additional_decline_pct": None}
    close = anchor["close"]
    change = (following[-1]["close"] - close) / close * 100
    decline = max(0.0, (close - min(bar["low"] for bar in following)) / close * 100)
    if not math.isfinite(change) or not math.isfinite(decline):
        return {"status": "unverified", "through_week": None, "return_pct": None, "max_additional_decline_pct": None}
    return {"status": "observed", "through_week": following[-1]["at"], "return_pct": change,
            "max_additional_decline_pct": decline}


def _outcomes(markers: list[dict], horizon: int) -> dict:
    measured = [marker["follow_up"][f"{horizon}w"] for marker in markers
                if marker["follow_up"][f"{horizon}w"]["status"] == "observed"]
    returns = [row["return_pct"] for row in measured]
    declines = [row["max_additional_decline_pct"] for row in measured]
    return {"weeks": horizon, "sample_count": len(markers),
            "observed_count": len(measured), "unverified_count": len(markers) - len(measured),
            "negative_return_count": sum(value < 0 for value in returns),
            "additional_decline_count": sum(value > 0 for value in declines),
            "severe_additional_decline_count": sum(value >= nq.SEVERE_PCT for value in declines),
            "median_return_pct": median(returns) if returns else None,
            "max_additional_decline_pct": max(declines, default=None)}


def _replay(read: _Weekly, bands: list, rsis: list, settings: dict, weeks: int) -> tuple[list[dict], dict]:
    bars = read.confirmed
    first = max(0, len(bars) - weeks)
    markers = []
    unknown = 0
    previous = None
    for index in range(len(bars)):
        rsi = rsis[index]
        if rsi is not None and math.isfinite(rsi) and rsi > settings["rsi_limit"]:
            previous = (index, "not_matched")
            continue
        point = _point(bars, bands, rsis, index, settings)
        status = point["status"]
        if status == "unavailable":
            unknown += index >= first
            continue
        edge = previous == (index - 1, "not_matched")
        previous = (index, status)
        if index < first or status != "matched":
            continue
        week = _week(date.fromisoformat(bars[index]["at"]), read.fetched)
        prior = nq._drawdown(bars, index) if index + 1 >= nq.DRAWDOWN_WEEKS else None
        markers.append({"week": week, "close": bars[index]["close"], "edge": edge, **point,
                        "prior_drawdown_pct": prior, "follow_up": {
                            f"{horizon}w": _follow_up(bars, index, horizon) for horizon in HORIZONS
                        }})
    samples = [marker for marker in markers if marker["edge"]]
    coverage = {
        "source": chart.SOURCE if bars else None, "first_week": bars[0]["at"] if bars else None,
        "last_week": bars[-1]["at"] if bars else None, "confirmed_weeks": len(bars), "requested_weeks": weeks,
        "first_evaluated_week": bars[first]["at"] if bars else None, "scanned_weeks": len(bars) - first,
        "unverified_weeks": unknown, "matched_weeks": len(markers), "edge_sample_count": len(samples),
        "severe_prior_drawdown_count": sum(
            marker["prior_drawdown_pct"] is not None and marker["prior_drawdown_pct"] >= nq.SEVERE_PCT
            for marker in samples
        ),
    }
    study = {"status": "available" if bars else "unavailable", "reason": None if bars else read.reason,
             "coverage": coverage, "severe_decline_threshold_pct": nq.SEVERE_PCT, "rollover": "unknown",
             "horizons": {f"{h}w": _outcomes(samples, h) for h in HORIZONS}}
    return markers, study


def view(weekly: dict | None, settings: dict, now: datetime, *, weeks: int = DEFAULT_HISTORY_WEEKS) -> dict:
    """Return read-only observations and prefix-based replay under the supplied current rules."""
    if type(weeks) is not int or not 1 <= weeks <= MAX_HISTORY_WEEKS:
        raise ValueError(f"weeks must be from 1 to {MAX_HISTORY_WEEKS}")
    rules = validate_settings(settings)
    read = _read(weekly, now)
    try:
        bands, rsis = _series(read.confirmed)
        current = _current(read, bands, rsis, rules, now)
        markers, study = _replay(read, bands, rsis, rules, weeks)
    except (ValueError, ArithmeticError):
        read = _Weekly(read.fetched, [], "weekly_evidence_error", read.forming)
        current = _current(read, [], [], rules, now)
        markers, study = _replay(read, [], [], rules, weeks)
    return {"method_version": METHOD_VERSION, "current": current, "markers": markers, "study": study}
