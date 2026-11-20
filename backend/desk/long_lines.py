"""Long trendlines over months (#679): the support a person draws under three or more widely spaced swing
lows of a daily chart (3-12 months) or a weekly one (1-3 years), and the resistance over the highs.

desk.trend and desk.channels look at recent swings; a line from a May low through the July and September
lows, which the price comes back to in November, is older and wider than what they keep. Here:

- **Anchors**: two swing lows (rising support) or swing highs (falling resistance) of the window, at
  least MIN_GAP bars apart; the line is straight in log price against bar order, like desk.trend.
- **Touches**: swings within TOUCH_ATR of the line, at least TOUCH_GAP bars after the previous touch
  (one visit is one touch), and the last bar when its low (high) reaches the line while its close holds:
  "오늘 4번째 닿음". At least MIN_TOUCHES, the first to the last at least MIN_SPAN bars apart.
- **Closes beyond it**: a run of up to FALSE_BREAK_BARS closes more than BREAK_ATR past the line, after
  which a close comes back, is a false break (a wick never counts - closes only); more than
  MAX_FALSE_BREAKS of them, or a longer run before the last bars, and it is no line. A run still going on
  the last bars is the break: ``broken`` from CONFIRM_BARS closes, else ``testing``.
- **Score**: touches, how far apart they are and how long the line runs, less its false breaks; a line
  the same as a better one (same place now, same slope) is that one. The best PER_KIND per kind.

Each line comes in the /api/lines trendline shape (desk.channels) with ``long`` true and, besides,
``touch_points`` (each touch's bar and the line's price there), ``false_breaks``, ``status`` (holding ·
testing · broken) and an easy sentence ("오늘 −7.7% 내려 장기 지지선 478 을 시험 중"). Read from the
finished bars only; arithmetic on price history, not a forecast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

TF_KO = {"1d": "일봉", "1w": "주봉"}
# per timeframe: (window bars, swing k, min anchor gap, min span first-last touch, touch gap)
SPEC = {"1d": {"window": 252, "k": 3, "gap": 15, "span": 60, "touch_gap": 10},
        "1w": {"window": 156, "k": 2, "gap": 6, "span": 40, "touch_gap": 4}}
MIN_TOUCHES = 3
TOUCH_ATR = 0.5            # a swing this close to the line (in ATR) touches it
BREAK_ATR = 0.3            # a close this far past the line is beyond it
FALSE_BREAK_BARS = 3       # a run of closes beyond it this short that came back is a false break
MAX_FALSE_BREAKS = 2
CONFIRM_BARS = 2           # closes beyond it at the end from which the line is broken
TEST_ATR = 0.5             # the price this close to the line is testing it
KEEP_BROKEN = 20           # bars a broken line is still shown
MAX_DISTANCE_ATR = 8.0     # an unbroken line further than this from the price is not shown
MAX_DISTANCE_PCT = 25.0    # nor further than this (a leveraged product's ATR is wide)
SWINGS = 40                # the most recent swings tried as anchors
PER_KIND = 1
MIN_SCORE = 2.0            # below this a line is a weak fit (few touches, far, stale, false breaks)
ATR_N = 14


@dataclass
class _Long:
    kind: str
    i: int
    j: int
    a: float
    slope: float
    touches: list[int] = field(default_factory=list)
    false_breaks: list[tuple[int, int]] = field(default_factory=list)   # (first, last) bar of each run
    broken_at: int | None = None
    run: int = 0           # closes beyond it on the last bars (a run not yet over)
    score: float = 0.0

    def at(self, x: float) -> float:
        return self.a + self.slope * (x - self.i)


def atr(bars: list[dict], n: int = ATR_N) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
           for p, b in zip(bars[-n - 1:-1], bars[-n:], strict=True)]
    return sum(trs) / n


def _swings(bars: list[dict], k: int, fld: str) -> list[int]:
    pick = min if fld == "low" else max
    return [i for i in range(k, len(bars) - k) if bars[i][fld] == pick(b[fld] for b in bars[i - k:i + k + 1])]


def _closes_beyond(line: _Long, cl: list[float], sign: int, brk: float) -> tuple[list[tuple[int, int]], int] | None:
    """The false breaks after the first anchor and the run still going at the end; None when a run
    longer than FALSE_BREAK_BARS came back (a real break, then a recovery: not one line) or too many."""
    n = len(cl)
    xs = np.arange(line.i, n)
    out = sign * (np.asarray(cl[line.i:]) - (line.a + line.slope * (xs - line.i))) < -brk
    if not out.any():
        return [], 0
    edges = np.diff(np.concatenate(([0], out.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)   # ends exclusive
    runs, run = [], 0
    for s, e in zip(starts, ends, strict=True):
        if e == len(out):                       # still beyond it on the last bar
            run = int(e - s)
        elif e - s > FALSE_BREAK_BARS:
            return None
        else:
            runs.append((int(s) + line.i, int(e) - 1 + line.i))
    if len(runs) > MAX_FALSE_BREAKS:
        return None
    return runs, run


def find(bars: list[dict], tf: str, unit: float) -> list[_Long]:
    """The long lines over ``bars`` (finished bars, oldest first) of ``tf`` (1d · 1w); ``unit`` is one ATR
    as a log distance at the last close."""
    spec = SPEC[tf]
    off = max(0, len(bars) - spec["window"])
    bars = bars[off:]
    n = len(bars)
    k = spec["k"]
    if n < spec["span"] + 2 * k:
        return []
    cl = [math.log(b["close"]) for b in bars]
    touch, brk = TOUCH_ATR * unit, BREAK_ATR * unit
    out: list[_Long] = []
    for kind, fld in (("support", "low"), ("resistance", "high")):
        sign = 1 if kind == "support" else -1
        ys = [math.log(b[fld]) for b in bars]
        swings = _swings(bars, k, fld)[-SWINGS:]
        found: list[_Long] = []
        for p, i in enumerate(swings):
            for j in swings[p + 1:]:
                if j - i < spec["gap"]:
                    continue
                slope = (ys[j] - ys[i]) / (j - i)
                if sign * slope <= 0:              # support rises, resistance falls
                    continue
                line = _Long(kind, i, j, ys[i], slope)
                got = _closes_beyond(line, cl, sign, brk)
                if got is None:
                    continue
                line.false_breaks, line.run = got
                if line.run >= CONFIRM_BARS:
                    line.broken_at = n - line.run
                    if line.run > KEEP_BROKEN:
                        continue
                # a line far from the price (broken long ago in effect, or out of reach) is not worth showing
                if abs(cl[-1] - line.at(n - 1)) > min(MAX_DISTANCE_ATR * unit, math.log(1 + MAX_DISTANCE_PCT / 100)):
                    continue
                last = None
                for x in swings:
                    if x < i or abs(ys[x] - line.at(x)) > touch:
                        continue
                    if last is not None and x - last < spec["touch_gap"]:
                        # one visit is one touch: the swing nearest the line stands for it
                        if abs(ys[x] - line.at(x)) < abs(ys[last] - line.at(last)):
                            line.touches[-1] = last = x
                        continue
                    line.touches.append(x)
                    last = x
                # the last bar reaching the line with its close holding: a touch now (no swing yet)
                if (line.broken_at is None and last is not None and n - 1 - last >= spec["touch_gap"]
                        and sign * (ys[-1] - line.at(n - 1)) <= touch and sign * (cl[-1] - line.at(n - 1)) >= -brk):
                    line.touches.append(n - 1)
                if len(line.touches) < MIN_TOUCHES or line.touches[-1] - line.touches[0] < spec["span"]:
                    continue
                gaps = [b - a for a, b in zip(line.touches, line.touches[1:], strict=False)]
                if line.broken_at is not None and n - 1 - line.touches[-1] > spec["span"]:
                    continue                        # broken, and untouched for a long while before: stale
                # what a person draws: many touches far apart over a long run, few false breaks - and the
                # line the price is at now, touched lately, over an old one far below
                # (a fresh break is news however far the drop went: no distance penalty on it)
                near = 0.0 if line.broken_at is not None else min(abs(cl[-1] - line.at(n - 1)) / unit, 3.0)
                stale = (n - 1 - line.touches[-1]) / spec["span"]
                line.score = (len(line.touches) + 0.5 * (line.touches[-1] - line.touches[0]) / spec["span"]
                              + 0.25 * min(gaps) / spec["touch_gap"] - 0.75 * len(line.false_breaks)
                              - near - stale)
                if line.score >= MIN_SCORE:
                    found.append(line)
        found.sort(key=lambda ln: -ln.score)
        kept: list[_Long] = []
        for ln in found:
            if any(abs(ln.at(n - 1) - o.at(n - 1)) <= 2 * touch and
                   abs(ln.slope - o.slope) * (n - 1 - min(ln.i, o.i)) <= 2 * touch for o in kept):
                continue
            kept.append(ln)
            if len(kept) == PER_KIND:
                break
        out += kept
    for ln in out:                                  # back to the caller's bar indexes
        ln.i, ln.j = ln.i + off, ln.j + off
        ln.a = ln.a                                 # a is the log price at i: unchanged by the shift
        ln.touches = [x + off for x in ln.touches]
        ln.false_breaks = [(a + off, b + off) for a, b in ln.false_breaks]
        ln.broken_at = ln.broken_at + off if ln.broken_at is not None else None
    return out


def _price(x: float) -> float:
    return float(round(x)) if x >= 1000 else round(x, 2)


def _fmt(price: float) -> str:
    return f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"


def _day(at: str) -> str:
    d = date.fromisoformat(at[:10])
    return f"{d.month}월 {d.day}일"


def to_dict(ln: _Long, bars: list[dict], tf: str, unit: float) -> dict:
    """``ln`` in the /api/lines trendline shape (desk.channels) and the long line's own fields."""
    last = len(bars) - 1
    sign = 1 if ln.kind == "support" else -1
    close = math.log(bars[-1]["close"])
    gap = sign * (close - ln.at(last))
    if ln.broken_at is not None:
        state = "broken"
    elif abs(gap) <= TEST_ATR * unit or ln.run or ln.touches[-1] == last:
        state = "testing"
    else:
        state = "intact"
    now = _price(math.exp(ln.at(last)))
    point = [{"at": bars[x]["at"], "price": _price(math.exp(ln.at(x)))} for x in ln.touches]
    out = {
        "id": f"{tf}-long-{ln.kind}-{bars[ln.i]['at']}-{bars[ln.j]['at']}",
        "timeframe": tf, "kind": ln.kind, "long": True,
        "anchors": [{"at": bars[x]["at"], "price": _price(math.exp(ln.at(x)))} for x in (ln.i, ln.j)],
        "end": {"at": bars[last]["at"], "price": now},
        "touches": [bars[x]["at"] for x in ln.touches],
        "touch_points": point,
        "false_breaks": [{"from": bars[a]["at"], "to": bars[b]["at"], "closes": b - a + 1,
                          "extreme": _price(min(bars[x]["close"] for x in range(a, b + 1)) if sign > 0
                                            else max(bars[x]["close"] for x in range(a, b + 1)))}
                         for a, b in ln.false_breaks],
        "slope_pct_per_bar": round((math.exp(ln.slope) - 1) * 100, 4),
        "state": state,
        "status": {"intact": "holding", "testing": "testing", "broken": "broken"}[state],
        "broken_at": bars[ln.broken_at]["at"] if ln.broken_at is not None else None,
        "broken_price": _price(math.exp(ln.at(ln.broken_at))) if ln.broken_at is not None else None,
        "fresh_break": ln.broken_at is not None,
        "distance_atr": round(gap / unit, 2),
        "distance_pct": round((math.exp(ln.at(last)) / bars[-1]["close"] - 1) * 100, 2),
        "since": bars[ln.touches[0]]["at"],
        "span_bars": ln.touches[-1] - ln.touches[0],
        "score": round(ln.score, 2),
    }
    out["explain"] = sentence(out, bars, tf)
    return out


def sentence(d: dict, bars: list[dict], tf: str) -> str:
    """One easy line: what the line is, since when, how many touches and false breaks, and now."""
    up = d["kind"] == "support"
    what = f"{TF_KO[tf]} 장기 {'상승 지지선' if up else '하락 저항선'}"
    fb = f", 종가가 잠깐 {'아래' if up else '위'}로 갔다 돌아온 거짓 이탈 {len(d['false_breaks'])}번" if d["false_breaks"] else ""
    head = f"{what} — {_day(d['since'])}부터 {len(d['touches'])}번 닿았어요{fb}."
    move = (bars[-1]["close"] / bars[-2]["close"] - 1) * 100 if len(bars) > 1 else 0.0
    today = "오늘" if tf == "1d" else "이번 주"
    if d["state"] == "broken":
        return f"{head} {_day(d['broken_at'])} 종가로 {'아래' if up else '위'}로 이탈했어요(선 {_fmt(d['broken_price'])})."
    if d["state"] == "testing":
        return (f"{head} {today} {move:+.1f}% {'내려' if move < 0 else '올라'} 장기 {'지지' if up else '저항'}선 "
                f"{_fmt(d['end']['price'])} 을 시험 중이에요.")
    return f"{head} 지금 선은 {_fmt(d['end']['price'])}({d['distance_pct']:+.1f}%)."


def lines(bars: list[dict], tf: str) -> list[dict]:
    """The long lines of finished ``bars`` (oldest first) of ``tf`` as dicts; [] when too few bars."""
    usable = [b for b in bars if b["low"] > 0 and b["close"] > 0 and b["high"] >= b["low"]]
    a = atr(usable)
    if not a or len(usable) < SPEC[tf]["span"] + 2 * SPEC[tf]["k"]:
        return []
    unit = a / usable[-1]["close"]
    return [to_dict(ln, usable, tf, unit) for ln in find(usable, tf, unit)]
