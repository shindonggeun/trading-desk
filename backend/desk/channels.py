"""Parallel channels and short trendlines on one timeframe's bars (#611).

A **channel** is a base line through two swing highs (or two swing lows) and the parallel line through
the opposite extreme since the base's first anchor. It holds when, before the last STATE_BARS bars, at
most MAX_BODY_BREAKS closes went beyond either line, and each side was touched at least
MIN_SIDE_TOUCHES times by a swing (wicks count). Besides its upper and lower lines it has the middle
line and the outer lines one width above and below (drawn dashed). Channels are scored on touches,
how recent the last one is, how long the channel runs and how few closes broke it; the best PER_TF
that are not the same channel are kept.

A **short trendline** joins two recent swing lows (rising, support) or swing highs (falling,
resistance) with no close through it between them. After its second anchor the first close beyond it
by BREAK_ATR is its break ("5분봉 상승추세 이탈"); a line broken more than KEEP_BROKEN bars ago is
dropped. A line the same as one of desk.trend's on these bars (any swing scale) is left to desk.trend
(one line, not two). A bar still trading is never a break: it only makes a line "testing".

Lines are straight in log price against bar order, like desk.trend. Everything is read from the bars
given and nothing after them: the backtest (desk.lines_bt) calls the same functions on each past
window. Arithmetic on price history, not a forecast - not a recommendation to buy or sell.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise

from . import trend

# the chart's intervals (desk.prices) and the issue's names for them
TF_ALIASES = {"5m": "5m", "15m": "15m", "1h": "60m", "60m": "60m", "2h": "120m", "120m": "120m", "4h": "240m",
              "240m": "240m", "1d": "1d"}
TIMEFRAMES = ("5m", "15m", "60m", "120m", "240m", "1d")
BARS = {"5m": 400, "15m": 400, "60m": 500, "120m": 400, "240m": 300, "1d": 300}       # bars read per timeframe
SWING_K = {"5m": 3, "15m": 3, "60m": 3, "120m": 3, "240m": 2, "1d": 3}             # bars each side of a swing
# desk.prices folds these from 60m bars by the exchange day: the last fold may run past its length
GROUPED = frozenset({"120m", "240m"})
MINUTES = {"5m": 5, "15m": 15, "60m": 60, "120m": 120, "240m": 240, "1d": 24 * 60}   # one bar
TF_KO = {"5m": "5분봉", "15m": "15분봉", "60m": "1시간봉", "120m": "2시간봉", "240m": "4시간봉", "1d": "일봉"}
KST = timezone(timedelta(hours=9))

ATR_N = 14
WINDOW = 240               # bars a channel's first anchor may lie back
SWINGS = 12                # the most recent swings per side tried as anchors
MIN_SPAN = 20              # bars from a channel's first anchor to now
TOUCH_ATR = 0.35           # a swing this close to a line touches it
BREAK_ATR = 0.3            # a close this far beyond a line breaks it
TEST_ATR = 0.5             # the price this close to a line is testing it
STATE_BARS = 3             # the last bars tell the state instead of disqualifying a line
MAX_BODY_BREAKS = 2        # closes beyond a channel's lines it may have had before
MIN_SIDE_TOUCHES = 2
MIN_WIDTH_ATR = 2.0
MAX_WIDTH_ATR = 20.0
FLAT_SHARE = 0.25          # a channel moving less than this share of its width over its span is flat
PER_TF = 2

TL_RECENT = 60             # a trendline's second anchor at most this many bars back
TL_SWINGS = 10
KEEP_BROKEN = 24           # a trendline broken within this many bars is still shown, with its break
MAX_DISTANCE_ATR = 6.0     # an unbroken trendline further from the price than this is not shown
TL_PER_KIND = 1
# bars after a touch or a break the backtest watches (desk.lines_bt); a break this recent is "fresh"
HORIZON = {"5m": 12, "15m": 8, "60m": 8, "120m": 6, "240m": 6, "1d": 5}

NOTE = "선은 지난 가격으로 그은 산수예요 — 앞날 예측도, 매수 · 매도 추천도 아니에요."


def is_live(bars: list[dict], tf: str, now: datetime | None = None) -> bool:
    """Whether the last bar still trades: its start plus its length is ahead of ``now``; a folded
    2h / 4h bar (GROUPED) always counts as trading - it may run past its length."""
    if not bars:
        return False
    if tf in GROUPED:
        return True
    end = datetime.fromisoformat(bars[-1]["at"]) + timedelta(minutes=MINUTES[tf])
    return end > (now or datetime.now(UTC))


def atr(bars: list[dict], n: int = ATR_N) -> float | None:
    trs = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
           for p, b in pairwise(bars)][-n:]
    return sum(trs) / len(trs) if trs else None


def _swings(bars: list[dict], k: int, field: str, first: int = 0) -> list[int]:
    return [i for i in trend._swing_idx(bars, k, field) if i >= first]


def _price(x: float) -> float:
    return float(round(x)) if x >= 1000 else round(x, 2)


def _fmt(price: float) -> str:
    return f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"


def _point(bars: list[dict], x: int, y: float) -> dict:
    return {"at": bars[x]["at"], "price": _price(math.exp(y))}


@dataclass
class Channel:
    base: str              # upper | lower: the side through the two anchors
    i: int                 # first anchor (the channel starts here)
    j: int
    a: float               # the upper line's log price at bar i
    slope: float           # log price per bar
    width: float           # log distance between the lines
    upper_touches: list[int]
    lower_touches: list[int]
    breaks: int
    score: float

    def upper(self, x: float) -> float:
        return self.a + self.slope * (x - self.i)

    def lower(self, x: float) -> float:
        return self.upper(x) - self.width


@dataclass
class Trendline:
    kind: str              # support (rising, through lows) | resistance (falling, through highs)
    i: int
    j: int
    a: float
    slope: float
    touches: list[int]
    broken_at: int | None
    score: float

    def at(self, x: float) -> float:
        return self.a + self.slope * (x - self.i)


def find_channels(bars: list[dict], k: int, unit: float) -> list[Channel]:
    """The best channels over ``bars`` (``unit``: one ATR as a log distance)."""
    n = len(bars)
    last = n - 1 - STATE_BARS                      # the bars that may disqualify
    first = max(0, n - WINDOW)
    hi = [math.log(b["high"]) for b in bars]
    lo = [math.log(b["low"]) for b in bars]
    cl = [math.log(b["close"]) for b in bars]
    highs, lows = _swings(bars, k, "high", first), _swings(bars, k, "low", first)
    touch, brk = TOUCH_ATR * unit, BREAK_ATR * unit
    found: list[Channel] = []
    for base, anchors in (("upper", highs[-SWINGS:]), ("lower", lows[-SWINGS:])):
        for p, i in enumerate(anchors):
            if n - 1 - i < MIN_SPAN:
                break
            for j in anchors[p + 1:]:
                if j - i < k or j > last:
                    continue
                ys = hi if base == "upper" else lo
                slope = (ys[j] - ys[i]) / (j - i)
                line = [ys[i] + slope * (x - i) for x in range(i, n)]
                # the opposite side through the extreme since i (before the last bars)
                if base == "upper":
                    width = max(line[x - i] - lo[x] for x in range(i, last + 1))
                else:
                    width = max(hi[x] - line[x - i] for x in range(i, last + 1))
                if not MIN_WIDTH_ATR * unit <= width <= MAX_WIDTH_ATR * unit:
                    continue
                up = line if base == "upper" else [y + width for y in line]
                breaks = sum(1 for x in range(i, last + 1)
                             if cl[x] > up[x - i] + brk or cl[x] < up[x - i] - width - brk)
                if breaks > MAX_BODY_BREAKS:
                    continue
                ut = [x for x in highs if x >= i and abs(hi[x] - up[x - i]) <= touch]
                lt = [x for x in lows if x >= i and abs(lo[x] - (up[x - i] - width)) <= touch]
                if len(ut) < MIN_SIDE_TOUCHES or len(lt) < MIN_SIDE_TOUCHES:
                    continue
                span = n - 1 - i
                recent = 1 - (n - 1 - max(ut + lt)) / span
                score = len(ut) + len(lt) + recent + span / WINDOW - 1.5 * breaks
                found.append(Channel(base, i, j, up[0], slope, width, ut, lt, breaks, score))
    found.sort(key=lambda c: -c.score)
    kept: list[Channel] = []
    for c in found:
        if not any(_same_channel(c, o, n) for o in kept):
            kept.append(c)
        if len(kept) == PER_TF:
            break
    return kept


def _same_channel(a: Channel, b: Channel, n: int) -> bool:
    w = min(a.width, b.width)
    span = n - 1 - min(a.i, b.i)
    return (abs(a.slope - b.slope) * span < 0.5 * w and abs(a.upper(n - 1) - b.upper(n - 1)) < 0.5 * w
            and abs(a.lower(n - 1) - b.lower(n - 1)) < 0.5 * w)


def find_trendlines(bars: list[dict], k: int, unit: float, exclude=None) -> list[Trendline]:
    """Recent rising support and falling resistance lines, broken ones with the bar they broke at;
    ``exclude(line)`` true leaves a line out before the best of each kind is taken."""
    n = len(bars)
    cl = [math.log(b["close"]) for b in bars]
    touch, brk = TOUCH_ATR * unit, BREAK_ATR * unit
    out: list[Trendline] = []
    for kind, field in (("support", "low"), ("resistance", "high")):
        ys = [math.log(b[field]) for b in bars]
        sign = 1 if kind == "support" else -1      # support: closes stay above
        swings = _swings(bars, k, field)
        tried = swings[-TL_SWINGS:]
        found: list[Trendline] = []
        for p, i in enumerate(tried):
            for j in tried[p + 1:]:
                if j - i < k or n - 1 - j > TL_RECENT:
                    continue
                slope = (ys[j] - ys[i]) / (j - i)
                if sign * slope <= 0:                  # support rises, resistance falls
                    continue
                def at(x, i=i, slope=slope, y=ys[i]):
                    return y + slope * (x - i)
                if any(sign * (cl[x] - at(x)) < -brk for x in range(i, j + 1)):
                    continue
                broken = next((x for x in range(j + 1, n) if sign * (cl[x] - at(x)) < -brk), None)
                if broken is not None and n - 1 - broken > KEEP_BROKEN:
                    continue
                end = broken if broken is not None else n
                touches = [x for x in swings if i <= x < end and abs(ys[x] - at(x)) <= touch]
                if broken is None and abs(cl[-1] - at(n - 1)) > MAX_DISTANCE_ATR * unit:
                    continue
                score = len(touches) + (j - i) / TL_RECENT + 1 - (n - 1 - j) / TL_RECENT
                found.append(Trendline(kind, i, j, ys[i], slope, touches, broken, score))
        found.sort(key=lambda t: -t.score)
        kept: list[Trendline] = []
        for t in found:
            if exclude is not None and exclude(t):
                continue
            if not any(abs(t.at(n - 1) - o.at(n - 1)) < touch * 2 for o in kept):
                kept.append(t)
            if len(kept) == TL_PER_KIND:
                break
        out += kept
    return out


def _same_as_trend(t: Trendline, lines: list[dict], bars: list[dict], unit: float) -> bool:
    """desk.trend has this line on the same bars: both anchors on the same bars, or the same price now
    and the same slope."""
    ats = {bars[t.i]["at"], bars[t.j]["at"]}
    n = len(bars)
    for ln in lines:
        if ln.get("kind") != t.kind:
            continue
        if {a["at"] for a in ln.get("anchors", [])} == ats:
            return True
        now = math.log(ln["price_now"]) if ln.get("price_now") else None
        slope = math.log(1 + ln.get("slope_pct_per_bar", 0) / 100)
        if now is not None and abs(now - t.at(n - 1)) < TOUCH_ATR * unit and abs(slope - t.slope) * 20 < TOUCH_ATR * unit:
            return True
    return False


def pick(bars: list[dict], tf: str, unit: float, atr_now: float, *, swing_k: int | None = None
         ) -> tuple[list[Channel], list[Trendline]]:
    """The channels and the short trendlines shown for ``bars`` - those desk.trend draws (every swing
    scale, as desk.sr asks it) left to it. The API and the backtest (desk.lines_bt) both read this."""
    k = SWING_K[tf] if swing_k is None else swing_k
    theirs = trend.trendlines(bars, tf, k, bars[-1]["close"], atr_now, scales=trend.SCALES)
    return (find_channels(bars, k, unit),
            find_trendlines(bars, k, unit, exclude=lambda t: _same_as_trend(t, theirs, bars, unit)))


def _clock(at: str, tf: str) -> str:
    when = datetime.fromisoformat(at).astimezone(KST)
    return when.strftime("%m-%d") if tf == "1d" else when.strftime("%m-%d %H:%M")


def _direction(c: Channel, n: int) -> str:
    move = c.slope * (n - 1 - c.i)
    return "flat" if abs(move) < FLAT_SHARE * c.width else "up" if move > 0 else "down"


_DIR_KO = {"up": "상승", "down": "하락", "flat": "횡보"}
_STATE_KO = {"inside": "채널 안", "near_upper": "상단 근처", "near_lower": "하단 근처",
             "above": "상단 위로 벗어남", "below": "하단 아래로 벗어남"}


def _beyond(c: Channel, bars: list[dict], x: int, brk: float) -> bool:
    y = math.log(bars[x]["close"])
    return y > c.upper(x) + brk or y < c.lower(x) - brk


def _channel_dict(c: Channel, bars: list[dict], closed: int, tf: str, unit: float) -> dict:
    """``bars[:closed]`` are the finished bars the channel was found on; the last bar may be the one
    still trading - the lines run to it and the position is the price now."""
    last = len(bars) - 1
    close = math.log(bars[-1]["close"])
    up, low = c.upper(last), c.lower(last)
    position = (close - low) / c.width
    brk, test = BREAK_ATR * unit, TEST_ATR * unit
    state = ("above" if close > up + brk else "below" if close < low - brk
             else "near_upper" if up - close <= test else "near_lower" if close - low <= test else "inside")
    direction = _direction(c, len(bars))
    lines = {"upper": 0.0, "lower": -c.width, "mid": -c.width / 2, "outer_upper": c.width, "outer_lower": -2 * c.width}
    # a channel break is a finished bar's close beyond it: the first of the run up to the last finished bar
    broke_at = None
    if _beyond(c, bars, closed - 1, brk):
        x = closed - 1
        while x - 1 > c.j and _beyond(c, bars, x - 1, brk):
            x -= 1
        broke_at = bars[x]["at"]
    out = {
        "id": f"{tf}-channel-{bars[c.i]['at']}-{bars[c.j]['at']}",
        "timeframe": tf, "direction": direction, "base": c.base,
        "start_at": bars[c.i]["at"],
        **{name: {"start": _point(bars, c.i, c.upper(c.i) + off), "end": _point(bars, last, up + off)}
           for name, off in lines.items()},
        "slope_pct_per_bar": round((math.exp(c.slope) - 1) * 100, 4),
        "width_pct": round((math.exp(c.width) - 1) * 100, 2), "width_atr": round(c.width / unit, 1),
        "position": round(position, 2), "state": state, "broke_at": broke_at,
        "touches": {"upper": [bars[x]["at"] for x in c.upper_touches],
                    "lower": [bars[x]["at"] for x in c.lower_touches]},
        "body_breaks": c.breaks, "score": round(c.score, 2),
    }
    name = f"{TF_KO[tf]} {_DIR_KO[direction]} 채널"
    since = _clock(bars[c.i]["at"], tf)
    where = _STATE_KO[state]
    out["explain"] = (f"{name} — {since}부터 같은 기울기로 위 {len(c.upper_touches)}번 · 아래 {len(c.lower_touches)}번 "
                      f"닿았어요. 지금은 {where}(아래 0 · 위 1 기준 {position:.2f}), "
                      f"상단 {_fmt(out['upper']['end']['price'])} · 하단 {_fmt(out['lower']['end']['price'])}.")
    return out


def _trend_dict(t: Trendline, bars: list[dict], closed: int, tf: str, unit: float) -> dict:
    """The break is read on the finished bars (``bars[:closed]``); a bar still trading beyond the line
    only makes it "testing" - it may close back."""
    last = len(bars) - 1
    close = math.log(bars[-1]["close"])
    sign = 1 if t.kind == "support" else -1
    gap = sign * (close - t.at(last))
    state = ("broken" if t.broken_at is not None
             else "testing" if abs(gap) <= TEST_ATR * unit or gap < -BREAK_ATR * unit else "intact")
    out = {
        "id": f"{tf}-trend-{t.kind}-{bars[t.i]['at']}-{bars[t.j]['at']}",
        "timeframe": tf, "kind": t.kind,
        "anchors": [_point(bars, x, t.at(x)) for x in (t.i, t.j)],
        "end": _point(bars, last, t.at(last)),
        "touches": [bars[x]["at"] for x in t.touches],
        "slope_pct_per_bar": round((math.exp(t.slope) - 1) * 100, 4),
        "state": state,
        "broken_at": bars[t.broken_at]["at"] if t.broken_at is not None else None,
        "broken_price": _price(math.exp(t.at(t.broken_at))) if t.broken_at is not None else None,
        "fresh_break": t.broken_at is not None and closed - 1 - t.broken_at < HORIZON[tf],
        "distance_atr": round(gap / unit, 2),
    }
    word = "상승추세" if t.kind == "support" else "하락추세"
    since = _clock(bars[t.i]["at"], tf)
    if state == "broken":
        side = "아래로" if t.kind == "support" else "위로"
        out["explain"] = (f"{TF_KO[tf]} {word} 이탈 — {since} {'저점' if t.kind == 'support' else '고점'}부터 이어 온 선을 "
                          f"{_clock(out['broken_at'], tf)} 종가가 {side} 넘었어요(선 {_fmt(out['broken_price'])}).")
    else:
        now = ", 가격이 선을 시험 중" if state == "testing" else ""
        out["explain"] = (f"{TF_KO[tf]} {word}선 — {since}부터 {len(t.touches)}번 닿았어요. "
                          f"지금 선은 {_fmt(out['end']['price'])}{now}.")
    return out


def _next_line(price: float, channels: list[dict], below: bool) -> tuple[str, float] | None:
    """The nearest channel line below (or above) ``price``: its name and price now."""
    names = {"upper": "상단", "lower": "하단", "mid": "가운데", "outer_upper": "바깥 상단", "outer_lower": "바깥 하단"}
    best = None
    for ch in channels:
        for key, ko in names.items():
            p = ch[key]["end"]["price"]
            if (p < price if below else p > price) and (best is None or abs(p - price) < abs(best[1] - price)):
                best = (f"{_DIR_KO[ch['direction']]} 채널 {ko}", p)
    return best


def _empty(tf: str, usable: list[dict]) -> dict:
    return {"timeframe": tf, "channels": [], "trendlines": [], "events": [], "patterns": [], "hs": [], "bubble": None,
            "atr": None,
            "reference_price": round(usable[-1]["close"], 2) if usable else None,
            "as_of": usable[-1]["at"] if usable else None, "note": NOTE}


def lines(bars: list[dict], tf: str, rates: dict | None = None, live: bool = False) -> dict:
    """Channels, short trendlines and their break events over ``bars`` (oldest first) of ``tf``.
    ``live``: the last bar is still trading - the lines are found and breaks judged on the bars before
    it (as the backtest reads them), the lines drawn to it and the price now read on it.
    ``rates`` (desk.lines_bt's table for this ticker and timeframe) adds each line's past rate."""
    usable = [b for b in bars if b["low"] > 0 and b["close"] > 0 and b["high"] >= b["low"]]
    closed = len(usable) - 1 if live else len(usable)
    done = usable[:closed]
    k = SWING_K[tf]
    a = atr(done)
    if len(done) < MIN_SPAN + 4 * k or not a:
        return _empty(tf, usable)
    unit = a / done[-1]["close"]
    rates = rates or {}
    found_channels, found_lines = pick(done, tf, unit, a)
    channels = [_channel_dict(c, usable, closed, tf, unit) for c in found_channels]
    for ch in channels:
        ch["hit_rate"] = rates.get("channel")
    reference = usable[-1]["close"]
    trends = [_trend_dict(t, usable, closed, tf, unit) for t in found_lines]
    events = []
    for t in trends:
        # the follow-through rate was measured on fresh breaks only
        t["hit_rate"] = (rates.get("trend_break") if t["fresh_break"] else None) if t["state"] == "broken" \
            else rates.get(f"trend_{t['kind']}")
        if t["state"] == "broken":
            nxt = _next_line(reference, channels, below=t["kind"] == "support")
            if nxt:
                t["explain"] += f" 다음 채널 선은 {nxt[0]} {_fmt(nxt[1])}."
            events.append({"at": t["broken_at"], "kind": "trend_break", "line_id": t["id"], "text": t["explain"]})
    for ch in channels:
        if ch["broke_at"]:
            events.append({"at": ch["broke_at"], "kind": "channel_break", "line_id": ch["id"], "text": ch["explain"]})
    events.sort(key=lambda e: e["at"])
    # #629 the converging pattern (desk.patterns) on the same finished bars, the state read on the price now
    from . import patterns
    from .sr import _atr

    try:                                # the ATR the pattern record was measured with (desk.patterns_bt)
        found, found_error = patterns.find(done, k, _atr(done) or a, tf, reference), None
    except Exception as exc:  # noqa: BLE001 - the lines stand without the pattern
        found, found_error = [], type(exc).__name__
    # #695 head and shoulders on the same bars, and on the weekly frame the Minsky-style bubble stage
    from . import bubble_stage, hs_pattern

    try:
        hs_found = hs_pattern.find(done, k, _atr(done) or a, tf, reference)
    except Exception as exc:  # noqa: BLE001
        hs_found, found_error = [], found_error or type(exc).__name__
    try:
        bubble = bubble_stage.stage(done) if tf == "1w" else None
    except Exception:  # noqa: BLE001
        bubble = None
    return {"timeframe": tf, "channels": channels, "trendlines": trends, "events": events, "patterns": found,
            "patterns_error": found_error, "hs": hs_found, "bubble": bubble,
            "atr": round(a, 4), "reference_price": round(reference, 2), "as_of": usable[-1]["at"], "note": NOTE}
