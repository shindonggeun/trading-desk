"""Converging patterns (#297): triangles (symmetric, ascending, descending) and wedges.

An upper line through two or more swing highs and a lower line through two or more swing lows
that narrow toward each other, with no close outside either (beyond a fraction of an ATR) from
the pattern's start until the last few bars - those decide its state: still converging, or
broken up or down. Unlike desk.trend's trendlines (support must rise, resistance must fall) a
side may be flat here, which is what tells an ascending triangle (flat top) from a descending one
(flat bottom). Lines are straight in log price against bar order, as in desk.trend.

How often each kind broke which way, and how far the move went, is measured on past bars by
``desk.patterns_bt`` and read from ``data/pattern_stats.json``; without a measurement a pattern
says so (``stats: None``) - a drawing is not evidence.

Arithmetic on price history, not a forecast.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .trend import BREAK_ATR, TOUCH_ATR, _round, _swing_idx

LOOKBACK = 150             # bars a pattern may span
MAX_SWINGS = 20            # the most recent swings per side considered as anchors
MAX_LINES = 12             # candidate lines kept per side before pairing
MIN_TOUCHES = 2            # per line
MIN_TOTAL = 5              # both lines together
STATE_BARS = 3             # the last bars decide the state instead of disqualifying the lines
FLAT_ATR = 1.0             # a side moving less than this over the pattern is flat
NARROW = 0.75              # the width now at most this share of the width at the start
APEX_AHEAD = 1.5           # the apex no further ahead than this many pattern lengths
KIND_KO = {"symmetric": "대칭 삼각", "ascending": "상승 삼각", "descending": "하강 삼각",
           "rising_wedge": "상승 쐐기", "falling_wedge": "하락 쐐기"}
# the textbook break: a symmetric triangle continues the trend before it (decided per pattern)
EXPECTED = {"ascending": "up", "descending": "down", "rising_wedge": "down", "falling_wedge": "up"}
STATS_PATH = Path(__file__).parent / "data" / "pattern_stats.json"
_stats_cache: dict | None = None


@dataclass
class _Line:
    i: int
    a0: float              # log price at bar 0
    slope: float           # log price per bar
    touches: list[int]

    def at(self, x: float) -> float:
        return self.a0 + self.slope * x


def _lines(bars: list[dict], k: int, upper: bool, unit: float, end: int) -> list[_Line]:
    """Lines through two swings of one side that no close crosses (by BREAK_ATR) up to ``end``."""
    field = "high" if upper else "low"
    sign = -1 if upper else 1                 # upper: closes stay under it
    swings = [s for s in _swing_idx(bars, k, field) if s <= end][-MAX_SWINGS:]
    logs = [math.log(b[field]) for b in bars]
    closes = [math.log(b["close"]) for b in bars]
    tol, touch = BREAK_ATR * unit, TOUCH_ATR * unit
    out = []
    for p, i in enumerate(swings):
        for j in swings[p + 1:]:
            if j - i < 2 * k:
                continue
            slope = (logs[j] - logs[i]) / (j - i)
            line = _Line(i, logs[i] - slope * i, slope, [])
            if any(sign * (closes[x] - line.at(x)) < -tol for x in range(i, end + 1)):
                continue
            line.touches = [s for s in swings if s >= i and abs(logs[s] - line.at(s)) <= touch]
            if len(line.touches) >= MIN_TOUCHES:
                out.append(line)
    out.sort(key=lambda ln: (-len(ln.touches), -ln.touches[-1]))
    return out[:MAX_LINES]


def _kind(up: _Line, low: _Line, span: int, unit: float) -> str | None:
    flat = FLAT_ATR * unit
    u = 0 if abs(up.slope * span) <= flat else (1 if up.slope > 0 else -1)
    lo = 0 if abs(low.slope * span) <= flat else (1 if low.slope > 0 else -1)
    return {(-1, 1): "symmetric", (0, 1): "ascending", (-1, 0): "descending",
            (1, 1): "rising_wedge", (-1, -1): "falling_wedge"}.get((u, lo))


def _prior_trend(bars: list[dict], start: int, n: int = 20) -> str:
    if start < n:
        return "none"
    return "up" if bars[start]["close"] > bars[start - n]["close"] else "down"


def best(bars: list[dict], k: int, atr: float):
    """(upper, lower, start, apex, kind, unit) of the best pattern over ``bars`` (already cut to
    LOOKBACK), or None - desk.patterns_bt follows the same lines forward."""
    n = len(bars)
    if n < 6 * k or not atr or any(b["low"] <= 0 or b["close"] <= 0 for b in bars):
        return None
    last = n - 1
    unit = atr / bars[-1]["close"]
    end = last - STATE_BARS
    uppers, lowers = _lines(bars, k, True, unit, end), _lines(bars, k, False, unit, end)
    found, best_key = None, None
    for up in uppers:
        for low in lowers:
            if len(up.touches) + len(low.touches) < MIN_TOTAL:
                continue
            start = max(up.i, low.i)
            span = last - start
            if span < 4 * k or up.slope >= low.slope:          # the lines must close in on each other
                continue
            w0, w1 = up.at(start) - low.at(start), up.at(last) - low.at(last)
            if w0 <= 0 or w1 <= 0 or w1 > NARROW * w0:
                continue
            apex = (low.a0 - up.a0) / (up.slope - low.slope)
            if not last < apex <= last + APEX_AHEAD * span:
                continue
            kind = _kind(up, low, span, unit)
            if not kind:
                continue
            key = (len(up.touches) + len(low.touches), max(up.touches[-1], low.touches[-1]), -w1 / w0)
            if best_key is None or key > best_key:
                found, best_key = (up, low, start, apex, kind, unit), key
    return found


def find(bars: list[dict], k: int, atr: float, timeframe: str = "1d", price: float | None = None) -> list[dict]:
    """The best converging pattern over the last LOOKBACK bars (oldest first), as a list (empty
    or one). ``atr`` is these bars' ATR; ``price`` (live) decides the state instead of the last
    close, as desk.trend does for its lines."""
    bars = bars[-LOOKBACK:]
    got = best(bars, k, atr)
    if not got:
        return []
    up, low, start, apex, kind, unit = got
    last = len(bars) - 1
    close = math.log(price if price and price > 0 else bars[-1]["close"])
    tol = BREAK_ATR * unit
    state = ("broke_up" if close > up.at(last) + tol else "broke_down" if close < low.at(last) - tol
             else "converging")
    expected = EXPECTED.get(kind) or _prior_trend(bars, start)

    def side(line: _Line) -> dict:
        first = min(line.touches)
        return {"from": {"at": bars[first]["at"], "price": _round(math.exp(line.at(first)))},
                "to": {"at": bars[last]["at"], "price": _round(math.exp(line.at(last)))},
                "touches": [bars[x]["at"] for x in line.touches],
                "slope_pct_per_bar": round((math.exp(line.slope) - 1) * 100, 3)}

    return [{
        "kind": kind, "label": KIND_KO[kind], "timeframe": timeframe, "state": state,
        "expected": expected,
        "upper": side(up), "lower": side(low),
        "apex": {"bars_ahead": round(apex - last, 1), "price": _round(math.exp(up.at(apex)))},
        "width_pct": round((math.exp(up.at(last) - low.at(last)) - 1) * 100, 2),
        "narrowed": round((up.at(last) - low.at(last)) / (up.at(start) - low.at(start)), 2),
        "stats": stats_for(timeframe, kind),
    }]


def stats_for(timeframe: str, kind: str) -> dict | None:
    """The measured record of this kind on this timeframe (desk.patterns_bt), or None."""
    global _stats_cache
    if _stats_cache is None:
        try:
            _stats_cache = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _stats_cache = {}
    return (_stats_cache.get("kinds") or {}).get(f"{timeframe}:{kind}")


def _record(s: dict | None) -> str:
    """What the measurement allows saying: the held-out share against the shuffled-price baseline."""
    if not s:
        return "no measured record - treat it as a drawing, not evidence"
    valid, null = s.get("valid") or {}, s.get("null") or {}
    head = (f"held-out {valid.get('n')} cases broke the textbook way {valid.get('expected_pct')}% vs "
            f"{null.get('expected_pct')}% on the same prices shuffled")
    verdict = s.get("verdict")
    if verdict == "edge":
        return f"{head} ({s.get('edge_pct'):+} points: an edge over chance)"
    if verdict == "unstable":
        return f"{head} - train and held-out disagree: no reliable lean"
    return f"{head} - no edge over chance: the break side is the lines' geometry, not information"


def prompt_note(patterns: list[dict] | None) -> str | None:
    """One line per pattern for the deciding agents, with only what the measurement supports."""
    if not patterns:
        return None
    lines = [f"{p['kind'].replace('_', ' ')} ({p['timeframe']}), {p['state'].replace('_', ' ')}, upper "
             f"{p['upper']['to']['price']} / lower {p['lower']['to']['price']}, apex in "
             f"{p['apex']['bars_ahead']} bars - {_record(p.get('stats'))}" for p in patterns]
    return ("Converging pattern (desk, from swing highs and lows): " + "; ".join(lines)
            + ". The pattern's lines are support / resistance like any other; do not take a direction from "
            "its textbook name unless an edge is stated.")
