"""Major (structural) support and resistance from years of weekly bars (#328).

desk.sr ranks levels by recent swings decayed by age, so a zone the price reacted to again and
again two or three years ago - a box top broken and then held as support - comes out weak. This
module looks at up to ten years of finished weekly bars (and monthly bars built from them) and
keeps the prices where swings from MIN_TIMES separate periods (SEP_WEEKS apart) turned:

- ``zone``: the members' span around their median, at most ZONE_ATR weekly ATR either side.
- ``reactions``: separate periods that turned there; ``as_high`` / ``as_low`` how many were
  swing highs or lows - both sides means a range edge, highs under the price now an old
  resistance that became support.
- big-wave Fibonacci: the retracements of the largest rise (fall) in the window that land in the
  zone are named in ``basis`` and add to the score; so does a round number inside it.

No decay by age: the record is the point. Only finished bars are read.
"""

from __future__ import annotations

import math
from itertools import pairwise
from statistics import median

SWING_K = 3            # weekly swing: the extreme of 3 weeks on each side
MONTH_K = 2
TOL_ATR = 0.4          # members of a zone sit within this many weekly ATR of its centre swing
ZONE_ATR = 0.35        # a zone reaches at most this far either side of its median
MIN_TIMES = 3          # separate periods that turned at the price
SEP_WEEKS = 6          # ...this many weeks apart
FIB_RATIOS = (0.382, 0.5, 0.618, 0.75, 0.786)
WAVE_WEEKS = 156       # the big wave is looked for in the last three years
ATR_WEEKS = 14


def _atr(bars: list[dict], n: int = ATR_WEEKS) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
           for p, b in pairwise(bars)]
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def _r(x: float) -> float:
    return float(round(x)) if abs(x) >= 1000 else round(x, 2)


def monthly(bars: list[dict]) -> list[dict]:
    """Weekly bars grouped by month (desk.bottom_zone.monthly, kept here to avoid the import)."""
    out: list[dict] = []
    for b in bars:
        if out and out[-1]["at"][:7] == b["at"][:7]:
            m = out[-1]
            m.update(close=b["close"], low=min(m["low"], b["low"]), high=max(m["high"], b["high"]))
        else:
            out.append({"at": b["at"], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]})
    return out


def _swings(bars: list[dict], k: int) -> list[tuple[int, float, str]]:
    """(week index, price, "high"|"low") of swing highs and lows."""
    out = []
    for i in range(k, len(bars) - k):
        w = bars[i - k:i + k + 1]
        if bars[i]["high"] == max(b["high"] for b in w):
            out.append((i, bars[i]["high"], "high"))
        if bars[i]["low"] == min(b["low"] for b in w):
            out.append((i, bars[i]["low"], "low"))
    return out


def _periods(weeks: list[int]) -> int:
    """How many separate periods (SEP_WEEKS apart) a set of week indexes covers."""
    n, last = 0, None
    for w in sorted(weeks):
        if last is None or w - last >= SEP_WEEKS:
            n += 1
        last = w
    return n


def big_wave(bars: list[dict]) -> dict | None:
    """The largest move in the last WAVE_WEEKS weeks: the lowest low to the highest high after it
    (a rise) or the highest high to the lowest low after it (a fall), whichever is larger."""
    seen = bars[-WAVE_WEEKS:]
    if len(seen) < 20:
        return None
    lo_i = min(range(len(seen)), key=lambda i: seen[i]["low"])
    hi_i = min(range(len(seen)), key=lambda i: -seen[i]["high"])
    up_hi = max(range(lo_i, len(seen)), key=lambda i: seen[i]["high"])
    down_lo = min(range(hi_i, len(seen)), key=lambda i: seen[i]["low"])
    rise = seen[up_hi]["high"] - seen[lo_i]["low"]
    fall = seen[hi_i]["high"] - seen[down_lo]["low"]
    if rise >= fall and rise > 0:
        a, b, up = seen[lo_i]["low"], seen[up_hi]["high"], True
    elif fall > 0:
        a, b, up = seen[hi_i]["high"], seen[down_lo]["low"], False
    else:
        return None
    # a retracement of a rise is measured down from its high; of a fall, up from its low
    levels = {r: b - r * (b - a) for r in FIB_RATIOS}
    return {"direction": "up" if up else "down", "from": _r(a), "to": _r(b), "levels": levels}


def round_number(lo: float, hi: float) -> float | None:
    """A round price inside [lo, hi]: multiples of half the price's leading power of ten (150, 5000)."""
    mid = (lo + hi) / 2
    if mid <= 0:
        return None
    step = 10 ** math.floor(math.log10(mid)) / 2
    for s in (step, step / 5):
        r = round(mid / s) * s
        if lo <= r <= hi:
            return r
    return None


def find(weekly: list[dict], price: float) -> list[dict]:
    """Major levels from finished weekly bars, oldest first, around ``price``."""
    watr = _atr(weekly)
    if not watr or len(weekly) < 2 * SWING_K + 10:
        return []
    swings = [(i, p, side, "주봉") for i, p, side in _swings(weekly, SWING_K)]
    months = monthly(weekly)
    week_of_month = {}
    for i, b in enumerate(weekly):
        week_of_month.setdefault(b["at"][:7], i)
    for i, p, side in _swings(months, MONTH_K):
        swings.append((week_of_month.get(months[i]["at"][:7], 0), p, side, "월봉"))
    tol = TOL_ATR * watr
    # greedy: the swing price with the most separate periods within +-tol is a zone; its members
    # are taken out and the next best is looked for (a chain of prices never makes one wide zone)
    left = list(swings)
    clusters: list[list[tuple]] = []
    while left:
        best, members = 0, []
        lo_p, hi_p = min(t[1] for t in left), max(t[1] for t in left)
        steps = int((hi_p - lo_p) / (tol / 4)) + 1
        for k in range(steps + 1):                 # centres on a grid: a box edge sits between its swings
            c = lo_p + k * tol / 4
            near = [t for t in left if abs(t[1] - c) <= tol]
            n = _periods([t[0] for t in near])
            if n > best or (n == best and n and len(near) > len(members)):
                best, members = n, near
        if best < MIN_TIMES:
            break
        clusters.append(members)
        left = [t for t in left if t not in members]
    wave = big_wave(weekly)
    out = []
    for c in clusters:
        times = _periods([s[0] for s in c])
        if times < MIN_TIMES:
            continue
        mid = median(s[1] for s in c)
        lo = max(min(s[1] for s in c), mid - ZONE_ATR * watr)
        hi = min(max(s[1] for s in c), mid + ZONE_ATR * watr)
        as_high = _periods([s[0] for s in c if s[2] == "high"])
        as_low = _periods([s[0] for s in c if s[2] == "low"])
        kind = "support" if mid < price else "resistance"
        first, last = weekly[min(s[0] for s in c)]["at"][:4], weekly[max(s[0] for s in c)]["at"][:4]
        basis = [f"{first}~{last} {times}번 반응"]
        if kind == "support" and as_high:
            basis.append("옛 저항(고점) → 지지" if not as_low else "박스 경계(고점 · 저점 모두)")
        elif kind == "resistance" and as_low:
            basis.append("옛 지지(저점) → 저항" if not as_high else "박스 경계(고점 · 저점 모두)")
        score = float(times)
        fibs = []
        if wave:
            for r, v in wave["levels"].items():
                if lo - tol <= v <= hi + tol:
                    fibs.append(f"큰 파동 {r} 되돌림 {_r(v)}")
            score += 1.0 if fibs else 0.0
        basis += fibs
        rn = round_number(lo, hi)
        if rn is not None:
            basis.append(f"라운드 넘버 {_r(rn)}")
            score += 0.5
        out.append({"price": _r(mid), "zone": [_r(lo), _r(hi)], "kind": kind, "tier": "major",
                    "reactions": times, "as_high": as_high, "as_low": as_low, "score": round(score, 1),
                    "basis": " · ".join(basis)})
    return out
