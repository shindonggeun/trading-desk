"""Trendlines and Fibonacci retracements from the same bars as desk.sr.

A trendline joins two swing lows (rising support) or two swing highs (falling resistance) and
must hold up: after its first anchor no close beyond it by more than a fraction of an ATR, and
at least three swings touching it. Lines are straight in log price against bar order, which is
how a chart with bar spacing draws them and how a long trend in an index or a leveraged product
grows. Each line says where it is on the last bar, how far the price is from it in ATR, and
whether the price is above it (intact), on it (testing) or has just closed through it (broken).

The Fibonacci retracement spans the highest high and lowest low of the primary bars; a level
within the zone tolerance of a horizontal support/resistance zone says so (confluence).

Arithmetic on price history, not a forecast — like desk.sr.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_TOUCHES = 3
BREAK_ATR = 0.3            # a close this far beyond the line breaks it
TOUCH_ATR = 0.5            # a swing this close to the line touches it
TEST_ATR = 0.5             # the price this close to the line is testing it
STATE_BARS = 3             # the last bars decide the state instead of disqualifying the line
MAX_SWINGS = 30            # the most recent swings considered as anchors
PER_KIND = 2               # lines kept per kind and timeframe
MAX_DISTANCE_ATR = 8.0     # lines further than this from the price are not useful
FIB_RATIOS = (0.0, 0.25, 0.382, 0.5, 0.618, 0.786, 1.0)


@dataclass
class _Line:
    kind: str              # support | resistance
    i: int
    j: int
    a: float               # log price at bar i
    slope: float           # log price per bar
    touches: list[int]

    def at(self, x: int) -> float:
        return self.a + self.slope * (x - self.i)


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def _swing_idx(bars: list[dict], k: int, field: str) -> list[int]:
    pick = min if field == "low" else max
    return [i for i in range(k, len(bars) - k)
            if bars[i][field] == pick(b[field] for b in bars[i - k:i + k + 1])]


def _lines(bars: list[dict], k: int, kind: str, atr: float) -> list[_Line]:
    """Rising support (kind support) or falling resistance lines that hold over ``bars``."""
    n = len(bars)
    field = "low" if kind == "support" else "high"
    sign = 1 if kind == "support" else -1   # support: closes must stay above (sign * (close - line) >= -tol)
    swings = _swing_idx(bars, k, field)[-MAX_SWINGS:]
    logs = [math.log(b[field]) for b in bars]
    closes = [math.log(b["close"]) for b in bars]
    ref = bars[-1]["close"]
    unit = atr / ref                       # one ATR as a log distance near the price
    break_tol, touch_tol = BREAK_ATR * unit, TOUCH_ATR * unit
    out: list[_Line] = []
    for p, i in enumerate(swings):
        for j in swings[p + 1:]:
            if j - i < 2 * k:
                continue
            slope = (logs[j] - logs[i]) / (j - i)
            if sign * slope <= 0:          # support rises, resistance falls
                continue
            line = _Line(kind, i, j, logs[i], slope, [])
            if any(sign * (closes[x] - line.at(x)) < -break_tol for x in range(i, n - STATE_BARS)):
                continue
            line.touches = [s for s in swings if s >= i and abs(logs[s] - line.at(s)) <= touch_tol]
            if len(line.touches) >= MIN_TOUCHES:
                out.append(line)
    return out


def _pick(lines: list[_Line], n: int, unit: float) -> list[_Line]:
    """Most touches, then the latest touch, then the longest; near-duplicates (same place on the
    last bar, about the same slope) are one line."""
    lines = sorted(lines, key=lambda ln: (-len(ln.touches), -ln.touches[-1], ln.i))
    kept: list[_Line] = []
    for ln in lines:
        if any(abs(ln.at(n - 1) - o.at(n - 1)) <= TOUCH_ATR * unit and
               abs(ln.slope - o.slope) * (n - 1 - min(ln.i, o.i)) <= TOUCH_ATR * unit for o in kept):
            continue
        kept.append(ln)
        if len(kept) == PER_KIND:
            break
    return kept


def _state(line: _Line, bars: list[dict], reference: float, atr: float) -> tuple[float, float, str]:
    n = len(bars)
    sign = 1 if line.kind == "support" else -1
    now = math.exp(line.at(n - 1))
    distance = (reference - now) / atr if atr else 0.0
    broken = sign * distance < -BREAK_ATR or any(
        sign * (bars[x]["close"] - math.exp(line.at(x))) < -BREAK_ATR * atr for x in range(max(0, n - STATE_BARS), n))
    state = "broken" if broken else "testing" if abs(distance) <= TEST_ATR else "intact"
    return now, distance, state


def trendlines(bars: list[dict], timeframe: str, k: int, reference: float, atr: float,
               bar_atr: float | None = None) -> list[dict]:
    """Lines over ``bars`` (oldest first). ``atr`` is the primary ATR the distances are in;
    ``bar_atr`` is these bars' own ATR for the break and touch tolerances (defaults to ``atr``)."""
    tol_atr = bar_atr or atr
    if len(bars) < 4 * k + 2 or not atr or not tol_atr or any(b["low"] <= 0 or b["close"] <= 0 for b in bars):
        return []
    n = len(bars)
    unit = tol_atr / bars[-1]["close"]
    out = []
    for kind in ("support", "resistance"):
        for ln in _pick(_lines(bars, k, kind, tol_atr), n, unit):
            now, distance, state = _state(ln, bars, reference, atr)
            if abs(distance) > MAX_DISTANCE_ATR:
                continue
            out.append({
                "kind": kind, "timeframe": timeframe,
                "anchors": [{"at": bars[x]["at"], "price": _round(math.exp(ln.at(x)))} for x in (ln.i, ln.j)],
                "end": {"at": bars[-1]["at"], "price": _round(now)},
                "touches": [bars[x]["at"] for x in ln.touches],
                "slope_pct_per_bar": round((math.exp(ln.slope) - 1) * 100, 3),
                "price_now": _round(now), "distance_atr": round(distance, 2), "state": state,
            })
    return out


def fibonacci(bars: list[dict], zones: list[dict] | None = None, tolerance: float = 0.0) -> dict | None:
    """Retracement of the move between the highest high and lowest low of ``bars``: ratio 0 is
    where the move ended, 1 where it started. A level within ``tolerance`` of a zone in
    ``zones`` (desk.sr levels) carries it as ``confluence``."""
    if len(bars) < 2:
        return None
    hi = max(range(len(bars)), key=lambda x: bars[x]["high"])
    lo = min(range(len(bars)), key=lambda x: bars[x]["low"])
    high, low = bars[hi]["high"], bars[lo]["low"]
    if high <= low:
        return None
    up = lo < hi
    start, end = ((bars[lo]["at"], low), (bars[hi]["at"], high)) if up else ((bars[hi]["at"], high), (bars[lo]["at"], low))
    levels = []
    for r in FIB_RATIOS:
        price = high - r * (high - low) if up else low + r * (high - low)
        level = {"ratio": r, "price": _round(price)}
        near = [z for z in zones or [] if abs(z["price"] - price) <= tolerance]
        if near:
            z = max(near, key=lambda z: z["strength"])
            level["confluence"] = {"price": z["price"], "strength": z["strength"], "basis": z["basis"]}
        levels.append(level)
    return {"direction": "up" if up else "down",
            "from": {"at": start[0], "price": _round(start[1])}, "to": {"at": end[0], "price": _round(end[1])},
            "levels": levels}


_STATE_EN = {"intact": "holding", "testing": "testing it", "broken": "just closed through it"}


def prompt_note(sr: dict | None) -> str | None:
    """Trendlines and the retracement as a reference block for the deciding agents."""
    if not sr or not (sr.get("trendlines") or sr.get("fibonacci")):
        return None
    lines = []
    for t in sr.get("trendlines") or []:
        name = "Rising support" if t["kind"] == "support" else "Falling resistance"
        lines.append(f"- {name} ({t['timeframe']}) at {t['price_now']} now, {len(t['touches'])} touches "
                     f"since {t['anchors'][0]['at'][:10]}, {t['slope_pct_per_bar']:+}% per bar; price "
                     f"{t['distance_atr']:+} ATR from it, {_STATE_EN[t['state']]}")
    fib = sr.get("fibonacci")
    if fib:
        move = "rise" if fib["direction"] == "up" else "fall"
        parts = []
        for lv in fib["levels"]:
            if lv["ratio"] in (0.0, 1.0):
                continue
            c = lv.get("confluence")
            parts.append(f"{lv['ratio']} {lv['price']}" + (f" (= zone {c['price']}, strength {c['strength']})" if c else ""))
        lines.append(f"- Fibonacci retracement of the {move} {fib['from']['price']} ({fib['from']['at'][:10]}) → "
                     f"{fib['to']['price']} ({fib['to']['at'][:10]}): " + ", ".join(parts))
    return ("Desk-computed trendlines (straight in log price) and Fibonacci retracement:\n" + "\n".join(lines) + "\n"
            "Where the price is testing a trendline, give both scenarios with their entry condition: the line "
            "holding (e.g. a long on a close back above rising support, stop below it) and a close through it "
            "(the opposite trade, or stand aside). A broken line is no longer support/resistance until retaken. "
            "A retracement level that coincides with a zone is stronger than either alone.")
