"""Offline comparisons of explicitly supplied alert and reference levels."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite

from .line_watch_bars import TIMEFRAMES

_UP = {"touch_support", "break_up", "retest_up", "long_support", "long_break_up"}
_DOWN = {"touch_resistance", "break_down", "retest_down", "long_resistance", "long_break_down"}


@dataclass(frozen=True)
class Series:
    symbol: str
    product: str
    timeframe: str


@dataclass(frozen=True)
class Bar:
    # Index in an explicit calendar of expected bars for this series.
    index: int
    at: datetime
    end: datetime
    available_at: datetime
    low: float
    high: float
    close: float


@dataclass(frozen=True)
class Levels:
    target: float
    invalidation: float


@dataclass(frozen=True)
class Alert:
    message_id: str
    series: Series
    bar_at: datetime
    observed_at: datetime
    rule: str
    levels: Levels | None


@dataclass(frozen=True)
class Reference:
    series: Series
    bar_at: datetime
    rule: str
    available_at: datetime
    levels: Levels


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timestamps require UTC offsets")
    return value.astimezone(UTC)


def _normalized_bars(bars: list[Bar]) -> list[Bar]:
    normalized: list[Bar] = []
    for bar in bars:
        bar = replace(bar, at=_utc(bar.at), end=_utc(bar.end), available_at=_utc(bar.available_at))
        if (bar.index < 0 or not bar.at < bar.end <= bar.available_at
                or not all(isfinite(x) for x in (bar.low, bar.high, bar.close))
                or not 0 < bar.low <= bar.close <= bar.high):
            raise ValueError("invalid finalized bar")
        if normalized and (bar.index <= normalized[-1].index or bar.at < normalized[-1].end):
            raise ValueError("bars must be sorted and nonoverlapping")
        normalized.append(bar)
    return normalized


def _unique_alerts(alerts: list[Alert]) -> list[Alert]:
    messages = {}
    for alert in alerts:
        alert = replace(alert, bar_at=_utc(alert.bar_at), observed_at=_utc(alert.observed_at))
        if not alert.message_id.strip() or alert.rule not in _UP | _DOWN:
            raise ValueError("missing message ID or unknown alert rule")
        if messages.setdefault(alert.message_id, alert) != alert:
            raise ValueError("conflicting alert message ID")

    events = {}
    for alert in sorted(messages.values(), key=lambda a: (a.observed_at, a.message_id)):
        key = (alert.series, alert.bar_at, alert.rule, alert.levels)
        events.setdefault(key, alert)
    return list(events.values())


def _reference_index(references: list[Reference]) -> dict:
    index = {}
    for ref in references:
        ref = replace(ref, bar_at=_utc(ref.bar_at), available_at=_utc(ref.available_at))
        key = (ref.series, ref.bar_at, ref.rule)
        if index.setdefault(key, ref) != ref:
            raise ValueError("conflicting reference for bar and rule")
    return index


def _window(bars: list[Bar], start: Bar, observed_at: datetime, as_of: datetime, horizon: int) -> list[Bar]:
    window = []
    expected = start.index + 1
    for bar in bars:
        if bar.index <= start.index:
            continue
        if bar.index != expected or bar.available_at > as_of:
            break
        expected += 1
        if bar.at < observed_at:
            continue
        window.append(bar)
        if len(window) == horizon:
            break
    return window


def _outcome(levels: Levels | None, start: Bar, up: bool, bars: list[Bar], horizon: int) -> dict:
    if levels is None:
        return {"status": "levels_missing"}
    target, stop = levels.target, levels.invalidation
    if (not all(isfinite(x) and x > 0 for x in (target, stop))
            or not (stop < start.close < target if up else target < start.close < stop)):
        return {"status": "levels_outside_direction"}
    for n, bar in enumerate(bars, start=1):
        hit = bar.high >= target if up else bar.low <= target
        failed = bar.low <= stop if up else bar.high >= stop
        if hit or failed:
            status = "ambiguous" if hit and failed else "target_first" if hit else "invalidation_first"
            return {"status": status, "bars": n, "bar_at": bar.at}
    return {"status": "neither" if len(bars) == horizon else "incomplete", "bars": len(bars)}


def compare(
    series: Series, bars: list[Bar], alerts: list[Alert], desk: list[Reference], baseline: list[Reference],
    *, as_of: datetime, horizon: int,
) -> list[dict]:
    """Compare exact-series snapshots against the same finalized post-alert bars.

    The caller supplies point-in-time reference levels and the exchange bar calendar.
    No market data, alert transport, results aggregation or rules fitting runs here.
    """
    as_of = _utc(as_of)
    if not series.symbol.strip() or not series.product.strip() or series.timeframe not in TIMEFRAMES or horizon < 1:
        raise ValueError("invalid comparison series or horizon")
    bars = _normalized_bars(bars)
    by_at = {bar.at: bar for bar in bars}
    references = {"desk": _reference_index(desk), "baseline": _reference_index(baseline)}
    results = []
    for alert in _unique_alerts(alerts):
        match = (alert.series, alert.bar_at, alert.rule)
        start = by_at.get(alert.bar_at)
        row = {"message_id": alert.message_id, "series": alert.series, "bar_at": alert.bar_at, "rule": alert.rule}
        if alert.series != series or start is None:
            results.append({**row, "status": "unmatched"})
            continue
        if not start.available_at <= alert.observed_at <= as_of:
            results.append({**row, "status": "unavailable_at_alert"})
            continue
        if any(bar.index > start.index and bar.end <= alert.observed_at for bar in bars):
            results.append({**row, "status": "stale_alert"})
            continue
        future = _window(bars, start, alert.observed_at, as_of, horizon)
        up = alert.rule in _UP
        arms = {"external": _outcome(alert.levels, start, up, future, horizon)}
        for name, index in references.items():
            ref = index.get(match)
            if ref is None or not start.available_at <= ref.available_at <= alert.observed_at:
                arms[name] = {"status": "reference_unavailable_at_alert"}
            else:
                arms[name] = _outcome(ref.levels, start, up, future, horizon)
        results.append({**row, "status": "matched", "arms": arms})
    return results
