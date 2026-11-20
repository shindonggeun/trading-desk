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

#629 each pattern also says when a side is usually chosen and how far a break usually carries:
``breakout_window`` (where between the pattern's start and its apex the measured breaks of this
timeframe and kind came - their middle half - else the textbook 50-80%, as bar times that follow the
bars' own trading hours, holidays not known), ``apex_at``, ``targets`` (the pattern's starting height
added at the break: the measured move, with how often it was reached), ``odds`` (the measured up /
down / no-break shares - by the trend before when there are enough cases - the false breaks, and
whether the held-out cases differed from the same prices shuffled) and a plain ``explain``. Past
shares, not a forecast.

#664 the rules traders use, added beside those fields: ``quality`` (touches, closes poking outside the
lines, a trend before the pattern, volume drying up - a weak pattern says so), ``breakout`` (a wick only
is an attempt, a close outside is the break, a close back inside is a false break, a break past 80% of
the way to the apex is late, at the apex the pattern is spent), the window's ``mid`` (the measured
median place of the breaks, else the textbook one), ``targets.stages`` (first 60% of the height, then the
whole; a rising wedge's downside the pattern's lowest trough; a late break only the first), ``retest``
(the entry on a pullback to the broken line with its stop on a close back inside, and how often a
pullback came and won in the past) and ``reference_only`` (no edge over the shuffled baseline).

Arithmetic on price history, not a forecast.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import pairwise
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
WINDOW_DEFAULT = (0.5, 0.8)  # the textbook share of start -> apex where breaks come, without a measurement
MIN_ODDS_N = 30            # fewer measured cases: "표본 적음"
# a starting height past either is no target (a pattern that began in a big swing, e.g. 005930 1d at 94%):
# the width at the last touch stands in, and if that is too wide as well the targets are left out
MAX_HEIGHT_ATR = 8.0
MAX_HEIGHT_PCT = 60.0
SLOT_DAYS = 28             # the next bars' times follow the weekdays and clock times of this many past days
KST = timezone(timedelta(hours=9))
TF_KO = {"1m": "1분봉", "5m": "5분봉", "15m": "15분봉", "30m": "30분봉", "60m": "1시간봉", "120m": "2시간봉",
         "240m": "4시간봉", "1d": "일봉", "1w": "주봉", "1M": "월봉"}
SHAPE_KO = {"symmetric": "고점은 낮아지고 저점은 높아지는 중", "ascending": "고점은 비슷하고 저점은 높아지는 중",
            "descending": "고점은 낮아지고 저점은 비슷한 중",
            "rising_wedge": "고점 · 저점이 함께 오르지만 폭이 좁아지는 중",
            "falling_wedge": "고점 · 저점이 함께 내리지만 폭이 좁아지는 중"}
# #664
LATE_SHARE = 0.8           # a break past this share of start -> apex is late (weak; the first target only)
EXPIRED_BARS = 1.0         # the apex this close ahead: the pattern is spent
FIRST_SHARE = 0.6          # the first target: this share of the height (practice: 0.5 - 0.66)
TEXTBOOK_MID = {"symmetric": 0.74, "ascending": 0.64}   # Bulkowski's median break place; others 0.65
RETEST_STOP_ATR = 0.5      # a retest entry's stop: a close this far back inside the broken line
TREND_BARS = 20            # the move over this many bars before the start ...
TREND_ATR = 3.0            # ... at least this many ATR: a trend the pattern pauses
VOLUME_DRY = 0.85          # the second half's volume under this share of the first half's: drying up
GRADE_STRONG, GRADE_WEAK = 75, 55   # the minimum clean pattern (5 touches, no trend, no volume) scores 45
GRADE_KO = {"strong": "선명", "normal": "보통", "weak": "약함"}
STATUS_KO = {"converging": "수렴 중", "attempt": "돌파 시도(꼬리만)", "broken": "돌파(종가 확인)",
             "false": "거짓 돌파", "late": "늦은 돌파", "expired": "만료(꼭짓점 도달)"}
NO_EDGE_KO = "참고용 · 통계적 우위 없음"
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


def quality(bars: list[dict], up: _Line, low: _Line, start: int, end: int, unit: float) -> dict:
    """#664 how clean a pattern is, 0-100: touches (30: 15 for the minimum two a line and five in all, 5 for
    each more), closes inside both lines from the start to ``end`` (30, less 1.5 for each percent outside),
    a trend before it (20, by the move over TREND_BARS in ATR) and volume drying up (20; 10 if only lower)."""
    total = len(up.touches) + len(low.touches)
    touch = min(30, 15 + 5 * (total - MIN_TOTAL))
    xs = range(start, end + 1)
    outside = sum(not low.at(x) <= math.log(bars[x]["close"]) <= up.at(x) for x in xs) / max(1, len(xs))
    inside = round(30 * max(0.0, 1 - 5 * outside))
    trend_atr = (abs(math.log(bars[start]["close"] / bars[start - TREND_BARS]["close"])) / unit
                 if start >= TREND_BARS and unit else None)
    trend = round(min(20.0, 20 * trend_atr / TREND_ATR)) if trend_atr is not None else 0
    vols = [b.get("volume") or 0 for b in bars[start:end + 1]]
    half = len(vols) // 2
    ratio = (sum(vols[half:]) / (len(vols) - half)) / (sum(vols[:half]) / half) if half >= 3 and all(vols) else None
    volume = 0 if ratio is None else 20 if ratio < VOLUME_DRY else 10 if ratio < 1 else 0
    score = touch + inside + trend + volume
    grade = "strong" if score >= GRADE_STRONG else "normal" if score >= GRADE_WEAK else "weak"
    return {"score": score, "grade": grade, "label": GRADE_KO[grade],
            "touches": [len(up.touches), len(low.touches)], "outside_close_pct": round(outside * 100, 1),
            "trend_before_atr": round(trend_atr, 1) if trend_atr is not None else None,
            "volume_ratio": round(ratio, 2) if ratio is not None else None,
            "points": {"touches": touch, "inside": inside, "trend": trend, "volume": volume}}


def breakout(bars: list[dict], up: _Line, low: _Line, start: int, apex: float, unit: float,
             price: float | None = None) -> dict:
    """#664 what the last STATE_BARS bars did with the lines (``price``, live, stands for the last close):
    a close beyond a line (by BREAK_ATR) is the break - false when the last close is back inside, late past
    LATE_SHARE of the way to the apex; without one a wick beyond is an attempt, the apex at hand spent.
    The last close decides the side (as ``state``): out one side then the other is a break of the last."""
    last = len(bars) - 1
    tol = BREAK_ATR * unit
    closes = [math.log(b["close"]) for b in bars]
    if price and price > 0:
        closes[last] = math.log(price)
    span = range(max(start, last - STATE_BARS + 1), last + 1)

    def out(x: int) -> str | None:
        return "up" if closes[x] > up.at(x) + tol else "down" if closes[x] < low.at(x) - tol else None

    side, xb = out(last), None
    if side:                                    # broken now: the break is where this run of closes outside began
        xb = last
        while xb - 1 in span and out(xb - 1) == side:
            xb -= 1
    else:                                       # inside now: the last close outside, if any, was a false break
        outs = [x for x in span if out(x)]
        if outs:
            xb = outs[-1]
            side = out(xb)
    share = None
    if side is None:
        for x in range(max(start, last - STATE_BARS + 1), last + 1):
            if math.log(bars[x]["high"]) > up.at(x) + tol:
                side = "up"
            elif math.log(bars[x]["low"]) < low.at(x) - tol:
                side = "down"
        status = "attempt" if side else "expired" if apex - last <= EXPIRED_BARS else "converging"
    else:
        share = round((xb - start) / (apex - start), 2)
        if not out(last):                                       # the last close (live price) back inside
            status = "false"
        else:
            status = "late" if share > LATE_SHARE else "broken"
    return {"status": status, "label": STATUS_KO[status], "side": side,
            "at": bars[xb]["at"] if xb is not None else None, "share": share}


def _extremes(bars: list[dict], start: int) -> tuple[float, float]:
    """The pattern's lowest trough and highest peak (log)."""
    return (min(math.log(b["low"]) for b in bars[start:]), max(math.log(b["high"]) for b in bars[start:]))


def retest(p: dict, up: _Line, low: _Line, last: int, xb: float, unit: float, stats: dict | None) -> dict:
    """#664 the entry most traders take: not the chase but a pullback to the broken line, the stop a close
    RETEST_STOP_ATR back inside it, the staged targets - in the trade map's (#652) shape. A pattern broken
    (or late) gives that side's scenario at the next bar; a converging one (or an attempt) both sides at the
    likeliest break place; a false break or a spent pattern none."""
    status, side = p["breakout"]["status"], p["breakout"]["side"]
    stats = stats or {}
    stages = p["targets"].get("stages") or []
    if status in ("broken", "late"):
        sides, x, when = [side], last + 1, "after_break"
    elif status in ("converging", "attempt"):
        sides, x, when = ["up", "down"], xb, "if_break"
    else:
        sides, x, when = [], None, None
    out = []
    for s in sides:
        sign = 1 if s == "up" else -1
        line = up if s == "up" else low
        # as desk.patterns_bt walks it: a limit just outside the line (TOUCH_ATR), the stop RETEST_STOP_ATR inside
        entry = math.exp(line.at(x) + sign * TOUCH_ATR * unit)
        stop = math.exp(line.at(x) - sign * RETEST_STOP_ATR * unit)
        risk = abs(entry - stop)
        targets = []
        for st in stages:
            price = st["up"] if s == "up" else st["down"]
            if price is not None and sign * (price - entry) > 0:
                targets.append({"stage": st["stage"], "price": price, "rr": round(abs(price - entry) / risk, 2)})
        out.append({"side": "long" if s == "up" else "short", "kind": "retest", "when": when,
                    "entry": _round(entry), "entry_type": "limit", "stop": _round(stop),
                    "stop_atr": TOUCH_ATR + RETEST_STOP_ATR, "stop_pct": round(risk / entry * 100, 1),
                    "targets": targets, "rr": targets[0]["rr"] if targets else None})
    note = {"false": "거짓 돌파 뒤라 리테스트 진입은 없어요", "expired": "꼭짓점에 다다라 진입 근거가 약해요"}.get(status)
    return {"scenarios": out, "pullback_pct": stats.get("pullback_pct"), "win_pct": stats.get("retest_win_pct"),
            "r_avg": stats.get("retest_r_avg"), "n": stats.get("retest_n"), "chase_r_avg": stats.get("chase_r_avg"),
            "note": note}


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
    close, as desk.trend does for its lines. The future bar times read all of ``bars`` (their
    weekdays and hours), not only the last LOOKBACK."""
    every = bars
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

    stats = stats_for(timeframe, kind)
    brk = breakout(bars, up, low, start, apex, unit, price)
    out = {
        "kind": kind, "label": KIND_KO[kind], "timeframe": timeframe, "state": state,
        "expected": expected,
        "upper": side(up), "lower": side(low),
        "apex": {"bars_ahead": round(apex - last, 1), "price": _round(math.exp(up.at(apex)))},
        "width_pct": round((math.exp(up.at(last) - low.at(last)) - 1) * 100, 2),
        "narrowed": round((up.at(last) - low.at(last)) / (up.at(start) - low.at(start)), 2),
        "stats": stats,
        "quality": quality(bars, up, low, start, last - STATE_BARS, unit),
        "breakout": brk,
    }
    out.update(outlook(bars, up, low, start, apex, timeframe, stats, _prior_trend(bars, start), state, every,
                       unit, kind=kind, late=brk["status"] == "late", extremes=_extremes(bars, start)))
    out["retest"] = retest(out, up, low, last, out.pop("_xb"), unit, stats)
    verdict = (stats or {}).get("verdict")
    out["reference_only"] = verdict != "edge"
    out["reference_label"] = (NO_EDGE_KO if verdict in ("no_edge", "unstable") else
                              "참고용 · 과거 통계 없음" if verdict != "edge" else None)
    out["explain"] = explain(out)
    return [out]


def future_times(bars: list[dict], count: int, daily: bool = False) -> list[str]:
    """The open times of the next ``count`` bars after ``bars`` (oldest first), following the bars'
    own rhythm: the clock times seen on each weekday in the last SLOT_DAYS days (a session's hours,
    a market's weekend; not its holidays). A weekday's older days that opened at another clock time
    than its latest one (a daylight-saving change, the times being UTC) are left out; a Monday to
    Friday not seen at all (too short a history) takes the clock times of the others.
    ``daily``: one bar a day at the last bar's clock time."""
    if not bars or count <= 0:
        return []
    times = [datetime.fromisoformat(b["at"]) for b in bars]
    last = times[-1]
    zone = last.tzinfo
    days: dict = {}
    for t in times:
        t = t.astimezone(zone)
        if last - t <= timedelta(days=SLOT_DAYS):
            days.setdefault(t.date(), []).append(t.timetz())
    slots: dict[int, list] = {}
    if daily:
        for d in days:
            slots[d.weekday()] = [last.timetz()]
    else:
        opens: dict[int, object] = {}
        for d in sorted(days, reverse=True):               # the latest day of each weekday sets its open
            first = min(days[d], key=lambda c: (c.hour, c.minute))
            if opens.setdefault(d.weekday(), first) != first:
                continue                                   # another regime (before a clock change)
            for clock in days[d]:
                if clock not in slots.setdefault(d.weekday(), []):
                    slots[d.weekday()].append(clock)
        if len(days) < 7:                                  # a short history (1-minute bars: a few hours)
            union = _fill(sorted({c for cs in slots.values() for c in cs}, key=lambda c: (c.hour, c.minute)), times)
            for w in {*range(5), *slots}:                  # the clock times between those seen, on every
                slots[w] = list(union)                     # weekday seen and Monday to Friday
    out: list[str] = []
    day = last.date()
    for _ in range(count * 8 + 400):                   # days: enough even for one bar a week
        for clock in sorted(slots.get(day.weekday(), []), key=lambda c: (c.hour, c.minute)):
            at = datetime.combine(day, clock)
            if at > last:
                out.append(at.isoformat(timespec="seconds"))
                if len(out) == count:
                    return out
        day += timedelta(days=1)
    return out


def _fill(clocks: list, times: list[datetime]) -> list:
    """The clock times from the first to the last of ``clocks`` at the bars' usual step (the most
    common gap within a day): a session's hours not all seen yet - its rest today, say."""
    gaps = [b - a for a, b in pairwise(times) if timedelta(0) < b - a < timedelta(hours=12)]
    if not gaps or not clocks:
        return clocks
    step = max(set(gaps), key=gaps.count)
    first, end = clocks[0], clocks[-1]
    day = datetime(2000, 1, 3)
    at, stop = datetime.combine(day, first), datetime.combine(day, end)
    out = []
    while at <= stop and len(out) < 2000:
        out.append(at.timetz().replace(tzinfo=first.tzinfo))
        at += step
    return sorted({*out, *clocks}, key=lambda c: (c.hour, c.minute))


def outlook(bars: list[dict], up: _Line, low: _Line, start: int, apex: float, timeframe: str,
            stats: dict | None, prior: str, state: str = "converging", every: list[dict] | None = None,
            unit: float | None = None, kind: str | None = None, late: bool = False,
            extremes: tuple[float, float] | None = None) -> dict:
    """#629 when a side is usually chosen, how far a break usually carries and the measured odds.
    ``every``: the bars the future times are read from (more than the pattern's)."""
    last = len(bars) - 1
    whole = apex - start
    stats = stats or {}
    measured = stats.get("frac_q25") is not None and stats.get("frac_q75") is not None
    f0, f1 = (stats["frac_q25"], stats["frac_q75"]) if measured else WINDOW_DEFAULT
    x0, x1 = start + f0 * whole, start + f1 * whole
    ahead_apex = max(1, math.ceil(apex - last))
    # a month is no fixed count of days: no bar times for 1M
    times = [] if timeframe == "1M" else future_times(every or bars, ahead_apex, daily=timeframe in ("1d", "1w"))
    lo = max(1, math.ceil(x0 - last))
    hi = max(lo, math.ceil(x1 - last))

    def when(n: int) -> str | None:
        return times[min(n, len(times)) - 1] if times else None

    # #664 the median break place inside the window (measured, else Bulkowski's for the kind)
    fm = stats.get("frac_q50") if measured and stats.get("frac_q50") is not None else TEXTBOOK_MID.get(kind or "", 0.65)
    fm = min(max(fm, f0), f1)
    mid = max(1, math.ceil(start + fm * whole - last))
    window = {"from": when(lo), "to": when(hi), "bars_ahead": [lo, hi], "share": [round(f0, 2), round(f1, 2)],
              "basis": "measured" if measured else "textbook", "passed": x1 <= last,
              "mid": {"at": when(mid), "bars_ahead": mid, "share": round(fm, 2), "passed": start + fm * whole <= last}}
    # the measured move: the height at the start added where a break is likeliest (the window's middle)
    height = up.at(start) - low.at(start)
    # (a pattern already broken: at the break, the last bars - as desk.patterns_bt measures the reach)
    xb = last if state != "converging" else min(max((x0 + x1) / 2, last), apex)
    targets = _targets(up, low, start, xb, height, unit, stats.get("reach_pct"), kind=kind, late=late,
                       extremes=extremes, stats=stats)
    odds = None
    if stats:
        by = (stats.get("by_prior") or {}).get(prior)
        use = by if by and (by.get("n") or 0) >= MIN_ODDS_N else stats
        n = use.get("n") or 0
        odds = {"up_pct": use.get("up_pct"), "down_pct": use.get("down_pct"), "none_pct": use.get("none_pct"),
                "n": n, "prior": prior if use is by else None, "small_sample": n < MIN_ODDS_N,
                "false_break_pct": stats.get("false_break_pct"), "verdict": stats.get("verdict"),
                "edge_pct": stats.get("edge_pct")}
    return {"apex_at": times[ahead_apex - 1] if len(times) >= ahead_apex else None, "breakout_window": window,
            "targets": targets, "odds": odds, "_xb": xb}


def _too_wide(height: float, unit: float | None) -> bool:
    return (math.exp(height) - 1) * 100 > MAX_HEIGHT_PCT or bool(unit and height > MAX_HEIGHT_ATR * unit)


def _stages(up: _Line, low: _Line, xb: float, h: float, full: dict, kind: str | None, late: bool,
            extremes: tuple[float, float] | None, stats: dict) -> list[dict]:
    """#664 the first target (FIRST_SHARE of the height from the break) and the second (the whole height, or
    for a rising wedge's downside the pattern's lowest trough - a falling wedge's upside its highest peak);
    a late break keeps the first only."""
    measured = full["basis"] == "measured_move"
    first = {"stage": 1, "share": FIRST_SHARE, "up": _round(math.exp(up.at(xb) + FIRST_SHARE * h)),
             "down": _round(math.exp(low.at(xb) - FIRST_SHARE * h)),
             "reach_pct": stats.get("reach1_pct") if measured else None}
    second = {"stage": 2, "share": 1.0, "up": full["up"], "down": full["down"], "up_basis": "height",
              "down_basis": "height", "reach_pct": full["reach_pct"]}
    if extremes and kind == "rising_wedge" and extremes[0] < low.at(xb):
        second.update(down=_round(math.exp(extremes[0])), down_basis="pattern_low")
        if extremes[0] > low.at(xb) - FIRST_SHARE * h:              # the trough nearer than the first target
            first["down"] = _round(math.exp((low.at(xb) + extremes[0]) / 2))
    if extremes and kind == "falling_wedge" and extremes[1] > up.at(xb):
        second.update(up=_round(math.exp(extremes[1])), up_basis="pattern_high")
        if extremes[1] < up.at(xb) + FIRST_SHARE * h:
            first["up"] = _round(math.exp((up.at(xb) + extremes[1]) / 2))
    if kind in ("rising_wedge", "falling_wedge"):
        second["extreme_reach_pct"] = stats.get("extreme_reach_pct") if measured else None
    return [first] if late else [first, second]


def _targets(up: _Line, low: _Line, start: int, xb: float, height: float, unit: float | None,
             reach: float | None, kind: str | None = None, late: bool = False,
             extremes: tuple[float, float] | None = None, stats: dict | None = None) -> dict:
    """The measured move from ``xb``; a starting height too wide (MAX_HEIGHT_PCT / MAX_HEIGHT_ATR) gives
    way to the width at the last touch (``basis`` recent_swing - the measured reach then no longer
    applies), and with that too wide the targets are left out with the reason."""
    def at(h: float, basis: str, reached: float | None) -> dict:
        full = {"up": _round(math.exp(up.at(xb) + h)), "down": _round(math.exp(low.at(xb) - h)),
                "height_pct": round((math.exp(h) - 1) * 100, 2), "reach_pct": reached, "basis": basis}
        full["stages"] = _stages(up, low, xb, h, full, kind, late, extremes, stats or {})
        return full

    if not _too_wide(height, unit):
        return at(height, "measured_move", reach)
    touch = max([*up.touches, *low.touches, start])
    recent = up.at(touch) - low.at(touch)
    if recent > 0 and not _too_wide(recent, unit):
        return at(recent, "recent_swing", None)
    return {"up": None, "down": None, "height_pct": round((math.exp(height) - 1) * 100, 2), "reach_pct": None,
            "basis": "omitted", "stages": [],
            "omitted": f"패턴 처음 높이가 {(math.exp(height) - 1) * 100:.0f}% 로 너무 넓어 목표는 생략했어요"}


def _clock(at: str | None, timeframe: str) -> str:
    """A bar time in KST as the timeframe reads it: a day, an hour, or an hour and minute."""
    if not at:
        return "?"
    t = datetime.fromisoformat(at).astimezone(KST)
    if timeframe in ("1d", "1w", "1M"):
        return t.strftime("%m-%d")
    return t.strftime("%m-%d %H시") if timeframe in ("60m", "120m", "240m") else t.strftime("%m-%d %H:%M")


def _span(a: str | None, b: str | None, timeframe: str) -> str:
    """"11-18 18시~22시", "11-18 18시~11-19 02시", or one time ("11-18 18시 무렵")."""
    x, y = _clock(a, timeframe), _clock(b, timeframe)
    if x == y:
        return f"{x} 무렵"
    if x[:5] == y[:5] and len(x) > 5:
        return f"{x}~{y[6:]}"
    return f"{x}~{y}"


def _fmt(price: float) -> str:
    return f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"


def explain(p: dict) -> str:
    """One plain sentence (#629): the shape, when a side is usually chosen, the targets, the record."""
    tf = TF_KO.get(p["timeframe"], p["timeframe"])
    name = p["label"] + ("수렴" if "wedge" not in p["kind"] else "")
    head = f"{tf} {name} — {SHAPE_KO[p['kind']]}."
    t, w, o = p["targets"], p["breakout_window"], p.get("odds")
    how = "최근 폭만큼" if t["basis"] == "recent_swing" else "처음 높이만큼"
    if t["basis"] == "omitted":
        middle = (f" 이미 {'위' if p['state'] == 'broke_up' else '아래'}로 깼어요." if p["state"] != "converging"
                  else "") + f" {t['omitted']}."
        if p["state"] == "converging":
            w = p["breakout_window"]
            basis = "보통" if w["basis"] == "measured" else "교과서로는"
            middle = (" 흔히 방향이 나오는 구간은 지났고 꼭짓점이 가까워요." if w["passed"] else
                      f" {basis} 꼭짓점 전 {_span(w['from'], w['to'], p['timeframe'])}에 방향이 나와요.") + middle
    elif p["state"] == "broke_up":
        middle = f" 이미 위로 깼어요 — {how} 가면 {_fmt(t['up'])} 근처."
    elif p["state"] == "broke_down":
        middle = f" 이미 아래로 깼어요 — {how} 가면 {_fmt(t['down'])} 근처."
    else:
        basis = "보통" if w["basis"] == "measured" else "교과서로는"
        if w["passed"]:
            when = " 흔히 방향이 나오는 구간은 지났고 꼭짓점이 가까워요."
        else:
            when = f" {basis} 꼭짓점 전 {_span(w['from'], w['to'], p['timeframe'])}에 방향이 나와요."
        middle = f"{when} 위로 깨면 {_fmt(t['up'])}, 아래로 깨면 {_fmt(t['down'])} 근처" + (
            "(처음 높이가 너무 넓어 최근 폭 기준)." if t["basis"] == "recent_swing" else ".")
    if t.get("reach_pct") is not None:
        middle += f" 과거 그 목표까지 간 비율 {t['reach_pct']:.0f}%."
    if o and o.get("up_pct") is not None:
        small = ", 표본 적음" if o["small_sample"] else ""
        record = f" 과거 위 {o['up_pct']:.0f}% · 아래 {o['down_pct']:.0f}%(표본 {o['n']}{small})"
        if o.get("prior"):
            record = record.replace("과거 위", f"과거 앞선 {'상승' if o['prior'] == 'up' else '하락'} 추세에서 위", 1)
        record += {"edge": "", "unstable": " — 과거 구간마다 결과가 달라 방향 근거는 약해요",
                   "unmeasured": " — 가격을 섞은 기준과의 비교는 아직 없어요"}.get(
            o.get("verdict"), " — 가격을 무작위로 섞어도 비슷해서 방향 근거는 약해요")
        record += "."
    else:
        record = " 과거 통계는 아직 없어요."
    return head + middle + record + _rules_said(p) + " 예측 아님."


def _rules_said(p: dict) -> str:
    """#664 the break's status, the weak pattern, the retest entry, no edge."""
    out = ""
    b, q, r = p.get("breakout") or {}, p.get("quality") or {}, p.get("retest") or {}
    out += {"attempt": " 꼬리만 선 밖으로 나갔어요 — 종가로 확인되기 전이에요.",
            "false": " 선 밖으로 나갔다가 다시 안으로 들어왔어요(거짓 돌파).",
            "late": " 꼭짓점 가까이 나온 늦은 돌파라 1차 목표만 봐요.",
            "expired": " 꼭짓점에 다다라 패턴이 힘을 잃었어요."}.get(b.get("status"), "")
    if b.get("status") in ("broken", "late") and r.get("pullback_pct") is not None:
        # measured, not advised: the retest entry did not beat the chase in our record (#664 PR table)
        out += f" 과거 돌파 뒤 선으로 되돌아온 비율 {r['pullback_pct']:.0f}%"
        if r.get("r_avg") is not None and r.get("chase_r_avg") is not None:
            out += f", 그 되돌림 진입 평균 {r['r_avg']:+.2f}R · 바로 따라간 진입 {r['chase_r_avg']:+.2f}R"
        out += "."
    if q.get("grade") == "weak":
        out += f" 선이 약한 패턴이에요(점수 {q['score']})."
    if p.get("reference_label") == NO_EDGE_KO:
        out += f" {NO_EDGE_KO}."
    return out


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
