"""Offline NQ weekly signal and outcome study (#675)."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import channels, chart_read, indicators, trend

NY = ZoneInfo("America/New_York")
WEEK_CLOSE = time(17)
WEEK = timedelta(days=7)
PERIODS = {"2016–2019": (2016, 2019), "2020–2022": (2020, 2022), "2023–2026": (2023, 2026)}
WARMUP = 55
DRAWDOWN_WEEKS = 52
CORRECTION_PCT = 5
SEVERE_PCT = 15
BB_WEEKS = 20
BB_MULT = 2
RSI_WEEKS = 14
RSI_LIMIT = 30
PIVOT_K = 3
FIB_RATIO = 2.618
APPROACH_ATR = 0.5
INVALIDATION_ATR = 0.25
BOUNCE_WEEKS = 2
BOUNCE_ATR = 1
RECOVERY_WEEKS = 12
RESISTANCE_WEEKS = 12
RECOVERY_CONFIRM = 2
METHODS = ("baseline", "bollinger", "rsi", "channel", "fib_2618", "confluence")
HORIZONS = ("bounce", "recovery")
PRICE_LEVELS = ("bollinger", "channel", "fib_2618")


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _monday(value: str) -> date:
    day = date.fromisoformat(value) if len(value) == 10 else _aware(value).astimezone(NY).date()
    if day.weekday() != 0:
        raise ValueError(f"weekly bar must have a New York Monday label: {value}")
    return day


def _week_closed_at(monday: date) -> datetime:
    return datetime.combine(monday + timedelta(days=4), WEEK_CLOSE, NY)


def _closed(rows: list[dict], as_of: datetime) -> list[dict]:
    """Validate chronology and the OHLC values available at this cutoff."""
    if as_of.utcoffset() is None:
        raise ValueError("as_of must include a UTC offset")
    if not isinstance(rows, list):
        raise TypeError("input must be a JSON array of weekly OHLC records")
    result, previous = [], None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("at"), str):
            raise TypeError("every weekly bar must have an ISO at label")
        monday = _monday(row["at"])
        if previous is not None and monday <= previous:
            raise ValueError(f"weekly bars must be unique and in ascending order: {monday}")
        prior, previous = previous, monday
        if _week_closed_at(monday) > as_of:
            continue
        if prior is not None and monday - prior != WEEK:
            raise ValueError(f"missing weekly bar between {prior} and {monday}")
        prices = {name: row.get(name) for name in ("open", "high", "low", "close")}
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
               for value in prices.values()):
            raise ValueError(f"weekly OHLC must be positive finite numbers: {monday}")
        if not (prices["low"] <= min(prices["open"], prices["close"])
                <= max(prices["open"], prices["close"]) <= prices["high"]):
            raise ValueError(f"weekly OHLC is inconsistent: {monday}")
        result.append({"at": monday.isoformat(), **prices})
    return result


def _outer_channel(prefix: list[dict], atr: float) -> tuple[float | None, list[str]]:
    """Reuse #611's ranked log channels and project its outer_lower onto the next bar."""
    last = len(prefix) - 1
    close = prefix[-1]["close"]
    unit = atr / close
    log_close = math.log(close)
    tolerance = channels.BREAK_ATR * unit
    for channel in channels.find_channels(prefix, PIVOT_K, unit):
        if channel.lower(last) - tolerance <= log_close <= channel.upper(last) + tolerance:
            return math.exp(channel.lower(last + 1) - channel.width), [
                prefix[index]["at"] for index in (channel.i, channel.j)
            ]
    return None, []


def _fib(prefix: list[dict]) -> tuple[float | None, list[str]]:
    """Select latest C, then B, then A, from pivots whose right-hand bars are already known."""
    highs = trend._swing_idx(prefix, PIVOT_K, "high")
    lows = trend._swing_idx(prefix, PIVOT_K, "low")
    for c in reversed(highs):
        c_price = prefix[c]["high"]
        for b in reversed(lows):
            if not (b < c and prefix[b]["low"] < c_price):
                continue
            a = next((a for a in reversed(highs) if a < b and prefix[a]["high"] > prefix[b]["low"]), None)
            if a is not None:
                projection = c_price * (prefix[b]["low"] / prefix[a]["high"]) ** FIB_RATIO
                anchors = [prefix[index]["at"] for index in (a, b, c)]
                return (projection if projection > 0 else None), anchors
    return None, []


def _near(low: float, level: float, atr: float) -> bool:
    return abs(low - level) <= APPROACH_ATR * atr


def _confluent(price_hits: int, rsi_hit: bool) -> bool:
    return price_hits >= 2 or (price_hits == 1 and rsi_hit)


@dataclass
class _PriceBasis:
    atr: float
    levels: dict[str, float | None]
    channel_anchors: list[str]
    fib_anchors: list[str]


def _price_basis(prefix: list[dict], lower_band: float | None) -> _PriceBasis:
    # A flat window has no usable ATR-based price levels; zero is not a proximity radius.
    atr = (channels.atr(prefix) or 0.0) if len(prefix) > channels.ATR_N else 0.0
    channel_level, channel_anchors = _outer_channel(prefix, atr) if atr > 0 else (None, [])
    fib_level, fib_anchors = _fib(prefix)
    return _PriceBasis(atr, {"bollinger": lower_band, "channel": channel_level, "fib_2618": fib_level},
                       channel_anchors, fib_anchors)


def _drawdown(bars: list[dict], i: int) -> float:
    peak = max(bar["high"] for bar in bars[i - DRAWDOWN_WEEKS + 1:i + 1])
    return (peak - bars[i]["close"]) / peak * 100


@dataclass
class _Terms:
    stop: float
    bounce_target: float
    resistance: float


def _terms(prefix: list[dict], signal: dict, atr: float) -> _Terms:
    return _Terms(
        signal["low"] - INVALIDATION_ATR * atr,
        max(signal["high"], signal["close"] + BOUNCE_ATR * atr),
        max(bar["high"] for bar in prefix[-RESISTANCE_WEEKS:]),
    )


def _reaction(future: list[dict], target: float, stop: float) -> tuple[str, str | None]:
    for bar in future:
        # Weekly OHLC cannot resolve order when both levels are reached in one bar.
        if bar["low"] < stop:
            return "invalidated", bar["at"]
        if bar["close"] >= target:
            return "success", bar["at"]
    return "expired", future[-1]["at"] if future else None


def _recovery(future: list[dict], states: list[bool], resistance: float, stop: float) -> tuple[str, str | None]:
    streak = 0
    for bar, uptrend in zip(future, states, strict=True):
        if bar["close"] < stop:
            return "invalidated", bar["at"]
        streak = streak + 1 if uptrend and bar["close"] > resistance else 0
        if streak >= RECOVERY_CONFIRM:
            return "success", bar["at"]
    return "expired", future[-1]["at"] if future else None


def _adverse_pct(future: list[dict], close: float) -> float:
    return max(0.0, (1 - min(bar["low"] for bar in future) / close) * 100)


def _event(bars: list[dict], i: int, bands: list, rsis: list, uptrends: list[bool], drawdown: float) -> dict:
    prefix, signal = bars[:i], bars[i]
    future = bars[i + 1:i + RECOVERY_WEEKS + 1]
    basis = _price_basis(prefix, bands[i - 1]["lower"])
    atr, levels = basis.atr, basis.levels
    hits = {name: atr > 0 and level is not None and _near(signal["low"], level, atr) for name, level in levels.items()}
    hits["rsi"] = rsis[i] <= RSI_LIMIT
    hits["confluence"] = _confluent(sum(hits[name] for name in PRICE_LEVELS), hits["rsi"])
    hits["baseline"] = True
    close = signal["close"]
    bounce = future[:BOUNCE_WEEKS]
    terms = _terms(prefix, signal, atr)
    return {
        "at": signal["at"],
        "drawdown_pct": round(drawdown, 6),
        "stratum": "severe" if drawdown >= SEVERE_PCT else "correction",
        "close": close,
        "previous_atr": atr,
        "rsi": rsis[i],
        "levels": levels,
        "channel_anchors": basis.channel_anchors,
        "fib_anchors": basis.fib_anchors,
        "methods": [name for name in METHODS if hits[name]],
        "invalidation": terms.stop,
        "bounce_target": terms.bounce_target,
        "recovery_resistance": terms.resistance,
        "bounce": {
            "outcome": _reaction(bounce, terms.bounce_target, terms.stop)[0],
            "adverse_pct": _adverse_pct(bounce, close),
        },
        "recovery": {
            "outcome": _recovery(future, uptrends[i + 1:i + RECOVERY_WEEKS + 1], terms.resistance, terms.stop)[0],
            "adverse_pct": _adverse_pct(future, close),
        },
    }


def _metrics(events: list[dict], horizon: str) -> dict:
    records = [event[horizon] for event in events]
    count = len(records)
    successes = sum(record["outcome"] == "success" for record in records)
    return {
        "sample_count": count,
        "successes": successes,
        "false_signals": count - successes,
        "invalidated": sum(record["outcome"] == "invalidated" for record in records),
        "hit_rate_pct": round(100 * successes / count, 2) if count else None,
        "max_extra_adverse_pct": round(max(record["adverse_pct"] for record in records), 2) if count else None,
    }


def _segment(events: list[dict]) -> dict:
    result = {}
    for stratum in ("all", "correction", "severe"):
        eligible = [event for event in events if stratum == "all" or event["stratum"] == stratum]
        methods = {}
        for name in METHODS:
            selected = [event for event in eligible if name in event["methods"]]
            methods[name] = {horizon: _metrics(selected, horizon) for horizon in HORIZONS}
        result[stratum] = {
            "eligible_weeks": len(eligible),
            "available_level_weeks": {
                name: sum(event["previous_atr"] > 0 and event["levels"][name] is not None for event in eligible)
                for name in PRICE_LEVELS
            },
            **methods,
        }
    return result


def report(rows: list[dict], as_of: datetime) -> dict:
    """Build a deterministic report. All price inputs must have the same weekly bar convention."""
    bars = _closed(rows, as_of)
    closes = [bar["close"] for bar in bars]
    bands = indicators.bollinger(closes, BB_WEEKS, BB_MULT)
    rsis = indicators.rsi(closes, RSI_WEEKS)
    uptrends = [chart_read.trend_state(bars[:end])["state"] == "up" for end in range(1, len(bars) + 1)]
    completed_windows = dict.fromkeys(PERIODS, 0)
    events_by_period = {period: [] for period in PERIODS}
    for i in range(WARMUP, len(bars) - RECOVERY_WEEKS):
        year = date.fromisoformat(bars[i]["at"]).year
        end_year = date.fromisoformat(bars[i + RECOVERY_WEEKS]["at"]).year
        period = next((label for label, (start, end) in PERIODS.items() if start <= year <= end_year <= end), None)
        if period is None:
            continue
        completed_windows[period] += 1
        drawdown = _drawdown(bars, i)
        if drawdown >= CORRECTION_PCT:
            events_by_period[period].append(_event(bars, i, bands, rsis, uptrends, drawdown))
    events = [event for group in events_by_period.values() for event in group]
    return {
        "as_of": as_of.isoformat(),
        "coverage": {
            "input_weeks": len(rows),
            "completed_weeks": len(bars),
            "first_completed_week": bars[0]["at"] if bars else None,
            "last_completed_week": bars[-1]["at"] if bars else None,
            "complete_windows": {**completed_windows, "all": sum(completed_windows.values())},
        },
        "parameters": {
            "week_labels": "Monday America/New_York",
            "complete_after": "Friday 17:00 America/New_York",
            "warmup_weeks": WARMUP,
            "drawdown_lookback_weeks": DRAWDOWN_WEEKS,
            "correction_from_pct": CORRECTION_PCT,
            "severe_from_pct": SEVERE_PCT,
            "atr_weeks": channels.ATR_N,
            "bollinger": {"weeks": BB_WEEKS, "stdev_multiplier": BB_MULT, "input": "previous close"},
            "rsi": {"weeks": RSI_WEEKS, "at_most": RSI_LIMIT, "smoothing": "Wilder"},
            "channel": {"pivot_radius": PIVOT_K, "axis": "log", "level": "outer_lower"},
            "fib": {"pivot_radius": PIVOT_K, "extension": FIB_RATIO, "anchors": "high-low-high", "axis": "log"},
            "approach_atr": APPROACH_ATR,
            "invalidation_atr": INVALIDATION_ATR,
            "bounce": {"weeks": BOUNCE_WEEKS, "target_atr": BOUNCE_ATR},
            "recovery": {
                "weeks": RECOVERY_WEEKS,
                "resistance_lookback_weeks": RESISTANCE_WEEKS,
                "consecutive_weeks": RECOVERY_CONFIRM,
            },
            "baseline": "all eligible weeks, overlaps signal samples",
            "max_extra_adverse_pct": "maximum positive fall from signal close to any later low in the fixed window",
        },
        "segments": {**{label: _segment(group) for label, group in events_by_period.items()}, "all": _segment(events)},
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bars", type=Path, help="JSON array of weekly OHLC records")
    parser.add_argument("--as-of", required=True, help="ISO timestamp with UTC offset")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = report(json.loads(args.bars.read_text(encoding="utf-8")), _aware(args.as_of))
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output is None:
            print(rendered, end="")
        else:
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
