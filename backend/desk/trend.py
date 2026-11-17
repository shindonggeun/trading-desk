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
PER_KIND = 2               # lines kept per kind, timeframe and swing scale
MAX_DISTANCE_ATR = 8.0     # a broken line further than this (own ATR) is dropped; the playbook reads lines this near (primary ATR)
MAX_FAR_ATR = 25.0         # past this (own ATR) even an unbroken line is not worth showing
NEAR_ATR = 3.0             # an unbroken line further than this (primary ATR, distance_atr) is "far": a big move away
FIB_RATIOS = (0.0, 0.25, 0.382, 0.5, 0.618, 0.786, 1.0)
# #530 swing scales: (name, multiple of the horizon's k, touches needed). A long swing has few
# swings to touch, so its line is the classic two-top one, still unbroken by any close.
SCALES = (("short", 1, MIN_TOUCHES), ("mid", 2, MIN_TOUCHES), ("long", 4, 2))
MAX_PER_KIND = {"resistance": 5, "support": 4}   # over both timeframes
AHEAD = (5, 10, 20)        # bars ahead the line's value is projected to
VOLUME_BARS = 20           # a breakout's volume bar: this many bars' average volume
_STATUS_RANK = {"testing": 0, "near": 1, "broken": 2, "far": 3}


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


def _lines(bars: list[dict], k: int, kind: str, atr: float, min_touches: int = MIN_TOUCHES,
           held: dict[tuple[int, int], bool] | None = None) -> list[_Line]:
    """Rising support (kind support) or falling resistance lines that hold over ``bars``.

    ``held`` remembers whether the line through two swings held (no close beyond it) across
    calls on the same bars and kind: a swing at a larger k is one at a smaller k too, so the
    longer scales mostly ask about pairs a shorter one already checked."""
    held = {} if held is None else held
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
            ok = held.get((i, j))
            if ok is None:
                a = logs[i]
                ok = held[(i, j)] = all(sign * (closes[x] - a - slope * (x - i)) >= -break_tol
                                        for x in range(i, n - STATE_BARS))
            if not ok:
                continue
            line.touches = [s for s in swings if s >= i and abs(logs[s] - line.at(s)) <= touch_tol]
            if len(line.touches) >= min_touches:
                out.append(line)
    return out


def _same(a: _Line, b: _Line, n: int, unit: float) -> bool:
    """Near-duplicates: the same place on the last bar and about the same slope."""
    return (abs(a.at(n - 1) - b.at(n - 1)) <= TOUCH_ATR * unit and
            abs(a.slope - b.slope) * (n - 1 - min(a.i, b.i)) <= TOUCH_ATR * unit)


def _pick(lines: list[_Line], n: int, unit: float) -> list[_Line]:
    """Most touches, then the latest touch, then the longest; near-duplicates are one line."""
    lines = sorted(lines, key=lambda ln: (-len(ln.touches), -ln.touches[-1], ln.i))
    kept: list[_Line] = []
    for ln in lines:
        if any(_same(ln, o, n, unit) for o in kept):
            continue
        kept.append(ln)
        if len(kept) == PER_KIND:
            break
    return kept


def _merge(found: list[tuple[int, int, _Line]], n: int, unit: float) -> list[tuple[int, int, _Line]]:
    """``(scale index, k, line)`` over every scale: a line two scales both found is one, kept at the
    scale where it has the most touches (then the latest touch, the shorter scale, the longest)."""
    found = sorted(found, key=lambda f: (-len(f[2].touches), -f[2].touches[-1], f[0], f[2].i))
    kept: list[tuple[int, int, _Line]] = []
    for f in found:
        if not any(_same(f[2], o[2], n, unit) for o in kept):
            kept.append(f)
    return kept


def _state(line: _Line, bars: list[dict], reference: float, tol_atr: float, at: float) -> tuple[float, float, str]:
    """The line's price at bar position ``at``, the price's distance from it in the line's own
    bars' ATR, and its state. Broken means the price — and the last close — are beyond the line
    now; a close through it that the price has since taken back is a test, not a break."""
    sign = 1 if line.kind == "support" else -1
    now = math.exp(line.at(at))
    own = (reference - now) / tol_atr
    last = len(bars) - 1
    closed_through = sign * (bars[last]["close"] - math.exp(line.at(last))) < -BREAK_ATR * tol_atr
    broken = sign * own < -BREAK_ATR and (closed_through or sign * own < -TEST_ATR)
    state = "broken" if broken else "testing" if abs(own) <= TEST_ATR else "intact"
    return now, own, state


def trendlines(bars: list[dict], timeframe: str, k: int, reference: float, atr: float,
               bar_atr: float | None = None, *, extend: float = 0.0, now_at: str | None = None,
               scales: tuple[tuple[str, int, int], ...] = SCALES[:1]) -> list[dict]:
    """Lines over ``bars`` (oldest first).

    ``bar_atr`` is these bars' own ATR (defaults to ``atr``): the break, touch and testing
    tolerances and the distance bands are in it, so a weekly line is judged on weekly
    volatility. ``distance_atr`` is reported in ``atr`` (the horizon's primary ATR, the one the
    agents size levels with). ``extend`` bars past the last one (a finished weekly bar is days
    behind today) is where "now" is on the line, dated ``now_at``.

    #530: each of ``scales`` looks for lines with swings ``k`` times its multiple; a line found at
    two scales is one. Unbroken lines stay up to MAX_FAR_ATR away (how much the price must move
    to reach one is the point), ``status`` "far" past NEAR_ATR primary ATRs; a broken one past
    MAX_DISTANCE_ATR is dropped as before."""
    tol_atr = bar_atr or atr
    if len(bars) < 4 * k + 2 or not atr or not tol_atr or any(b["low"] <= 0 or b["close"] <= 0 for b in bars):
        return []
    n = len(bars)
    unit = tol_atr / bars[-1]["close"]
    at = n - 1 + extend
    out = []
    for kind in ("support", "resistance"):
        found, held = [], {}
        for s, (_name, mult, touches) in enumerate(scales):
            kk = k * mult
            if n >= 4 * kk + 2:
                found += [(s, kk, ln) for ln in _pick(_lines(bars, kk, kind, tol_atr, touches, held), n, unit)]
        for s, kk, ln in _merge(found, n, unit):
            now, own, state = _state(ln, bars, reference, tol_atr, at)
            if abs(own) > MAX_FAR_ATR or (state == "broken" and abs(own) > MAX_DISTANCE_ATR):
                continue
            distance = (reference - now) / atr
            status = state if state != "intact" else "far" if abs(distance) > NEAR_ATR else "near"
            anchors = [{"at": bars[x]["at"], "price": _round(math.exp(ln.at(x)))} for x in (ln.i, ln.j)]
            out.append({
                # the anchors' full bar times: intraday lines share their anchors' dates
                "id": f"{timeframe}-{kind}-{anchors[0]['at']}-{anchors[1]['at']}",
                "kind": kind, "timeframe": timeframe, "scale": scales[s][0], "swing_k": kk,
                "anchors": anchors,
                "end": {"at": now_at or bars[-1]["at"], "price": _round(now)},
                "touches": [bars[x]["at"] for x in ln.touches],
                "slope_pct_per_bar": round((math.exp(ln.slope) - 1) * 100, 3),
                "price_now": _round(now), "distance_atr": round(distance, 2), "state": state,
                "status": status, "distance_pct": round((now / reference - 1) * 100, 2),
                "distance_bar_atr": round(own, 2),   # in the line's own bars' ATR: how far lines used to reach
                "ahead": [{"bars": a, "price": _round(p := math.exp(ln.at(at + a))),
                           "distance_pct": round((p / reference - 1) * 100, 2)} for a in AHEAD],
            })
    return out


def select(lines: list[dict]) -> list[dict]:
    """At most MAX_PER_KIND lines of each kind over both timeframes. The lines in reach (testing,
    near, broken) come first - the nearest of each timeframe and scale, so a long swing's line
    is not crowded out by short ones, then the nearest of the rest - and far lines fill what is
    left the same way: a far line never takes a near one's place (the playbook builds zones from
    these). Support first, then resistance, nearest first."""
    def spread(group: list[dict], room: int) -> list[dict]:
        seen, kept = set(), []
        for t in group:
            if len(kept) < room and (t["timeframe"], t.get("scale")) not in seen:
                seen.add((t["timeframe"], t.get("scale")))
                kept.append(t)
        rest = [t for t in group if not any(t is k for k in kept)]
        return kept + rest[:room - len(kept)]

    out = []
    for kind in ("support", "resistance"):
        ranked = sorted((t for t in lines if t["kind"] == kind),
                        key=lambda t: (_STATUS_RANK.get(t.get("status"), 1), abs(t.get("distance_pct", 0))))
        cap = MAX_PER_KIND[kind]
        kept = spread([t for t in ranked if t.get("status") != "far"], cap)
        kept += spread([t for t in ranked if t.get("status") == "far"], cap - len(kept))
        out += sorted(kept, key=lambda t: abs(t.get("distance_pct", 0)))
    return out


# ------------------------------------------------------------------ #530 scenarios

TF_KO = {"1m": "1분봉", "5m": "5분봉", "60m": "60분봉", "1d": "일봉", "1w": "주봉", "1M": "월봉"}
_SCALE_KO = {"short": "짧은 스윙", "mid": "중간 스윙", "long": "긴 스윙"}
_MIN_STEP = 0.002          # a target must be at least this far past the trigger


def _num(price: float) -> str:
    return f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"


def _frame(bars: list[dict], k: int) -> tuple[float | None, list[tuple[float, str, str]]]:
    """The average volume of the VOLUME_BARS bars before the last (None without volume; the last
    one may still be forming, and it is the bar a breakout's volume is held against) and the
    bars' swing highs and lows as (price, "high" | "low", timeframe-less basis)."""
    vols = [b.get("volume") or 0 for b in bars[-VOLUME_BARS - 1:-1]]
    vol = sum(vols) / len(vols) if vols and any(vols) else None
    pivots = [(bars[i]["high"], "high", "앞 고점") for i in _swing_idx(bars, k, "high")]
    pivots += [(bars[i]["low"], "low", "앞 저점") for i in _swing_idx(bars, k, "low")]
    return vol, pivots


def annotate(lines: list[dict], levels: list[dict], reference: float, frames: dict[str, dict]) -> None:
    """Each line's scenarios and plain sentences, in place. ``frames`` maps a timeframe to
    ``{"bars", "k", "atr"}`` (its bars, swing size and own ATR - the break tolerance); ``levels``
    are desk.sr's horizontal zones. Targets are the nearest zone, other unbroken line or swing
    high/low past the trigger. Arithmetic only: every number in the text is one computed here."""
    vols, cands = {}, []        # cands: (price, "up" | "down" side it can be a target on, basis[, line])
    for tf, f in frames.items():
        vols[tf], pivots = _frame(f["bars"], f["k"])
        cands += [(p, "up" if side == "high" else "down", f"{TF_KO.get(tf, tf)} {basis}") for p, side, basis in pivots]
    for lv in levels or []:
        tf = TF_KO.get(lv.get("timeframe"), lv.get("timeframe") or "")
        cands += [(lv["price"], "up", f"{tf} 저항 구역".strip()), (lv["price"], "down", f"{tf} 지지 구역".strip())]
    for t in lines:
        if t.get("status") != "broken":
            name = "하락 저항선" if t["kind"] == "resistance" else "상승 지지선"
            for side in ("up", "down"):
                cands.append((t["price_now"], side, f"{TF_KO.get(t['timeframe'], t['timeframe'])} {name}", t))

    def target(past: float, side: str, own: dict) -> tuple[float | None, str | None]:
        pool = [c for c in cands if c[1] == side and (len(c) < 4 or c[3] is not own)
                and (c[0] > past * (1 + _MIN_STEP) if side == "up" else c[0] < past * (1 - _MIN_STEP))]
        if not pool:
            return None, None
        best = (min if side == "up" else max)(pool, key=lambda c: c[0])
        return _round(best[0]), best[2]

    for t in lines:
        f = frames.get(t["timeframe"])
        if not f:
            continue
        now, tol, vol = t["price_now"], BREAK_ATR * f["atr"], vols.get(t["timeframe"])
        vol_min = round(vol) if vol else None
        vol_txt = f" + 거래량 {VOLUME_BARS}봉 평균({vol_min:,}) 이상" if vol_min else ""
        if _role(t) == "resistance":
            up, down = _round(now + tol), _round(now)
            t1, b1 = target(max(up, reference), "up", t)
            t2, b2 = target(min(down, reference), "down", t)
            go = f"다음 목표 {_num(t1)}({b1})" if t1 else "위로 가까운 목표 없음"
            back = f"{_num(t2)}({b2})까지 되돌림을 본다" if t2 else "아래 받침이 가까이 없다"
            scenarios = [
                {"kind": "breakout", "trigger": up, "volume_min": vol_min, "target": t1, "target_basis": b1,
                 "invalid": down,
                 "text": f"종가 {_num(up)} 위 마감{vol_txt}이면 돌파 → {go}. 다시 {_num(down)} 아래로 마감하면 무효."},
                {"kind": "reject", "trigger": down, "volume_min": None, "target": t2, "target_basis": b2,
                 "invalid": up,
                 "text": f"{_num(down)} 근처에서 막히고 밀리면 {back}. {_num(up)} 위 마감이면 무효."},
            ]
            last = (f"{_num(up)} 위 마감이면 {_num(t1) if t1 else '위'}까지, "
                    f"막히면 {_num(t2) + '까지 되돌림' if t2 else '되돌림'}.")
        else:
            up, down = _round(now), _round(now - tol)
            t1, b1 = target(max(up, reference), "up", t)
            t2, b2 = target(min(down, reference), "down", t)
            go = f"{_num(t1)}({b1})까지 본다" if t1 else "위로 가까운 목표가 없다"
            fall = f"{_num(t2)}({b2})까지 밀릴 수 있다" if t2 else "아래 받침이 가까이 없다"
            scenarios = [
                {"kind": "bounce", "trigger": up, "volume_min": None, "target": t1, "target_basis": b1,
                 "invalid": down,
                 "text": f"{_num(up)} 근처에서 받치고 반등하면 {go}. {_num(down)} 아래 마감이면 무효."},
                {"kind": "breakdown", "trigger": down, "volume_min": vol_min, "target": t2, "target_basis": b2,
                 "invalid": up,
                 "text": f"종가 {_num(down)} 아래 마감{vol_txt}이면 이탈 → {fall}. 다시 {_num(up)} 위로 올라오면 무효."},
            ]
            last = (f"{_num(up)}에서 받치면 {_num(t1) if t1 else '위'}까지, "
                    f"{_num(down)} 아래 마감이면 {_num(t2) + '까지' if t2 else '더 아래로'}.")
        t["scenarios"] = scenarios
        t["summary"] = [_position(t), _drift(t), last]


def _role(t: dict) -> str:
    """What the line does to the price now: a broken support is overhead (retaking it is the
    breakout), a broken resistance is underneath (holding it is the bounce)."""
    if t.get("status") != "broken":
        return t["kind"]
    return "support" if t["kind"] == "resistance" else "resistance"


def _position(t: dict) -> str:
    res = t["kind"] == "resistance"
    name = (f"{TF_KO.get(t['timeframe'], t['timeframe'])} {_SCALE_KO.get(t.get('scale'), '')} "
            f"{'하락 저항선' if res else '상승 지지선'}")
    d, now, status = t["distance_pct"], _num(t["price_now"]), t["status"]
    if status == "testing":
        return f"{name}이 지금 {now} — 지금가가 선에 붙어 있다({d:+.1f}%)."
    if status == "broken":
        return (f"{name}({now})을 종가로 뚫고 올라섰다(지금가가 선보다 {abs(d):.1f}% 위)." if res else
                f"{name}({now})을 종가로 깨고 내려갔다(지금가가 선보다 {abs(d):.1f}% 아래).")
    far = " 한 번에 닿기엔 먼 선이다." if status == "far" else ""
    if res:
        return f"{name}이 지금 {now} — 닿으려면 {d:+.1f}% 올라야 한다(ATR {abs(t['distance_atr'])}배).{far}"
    return f"{name}이 지금 {now} — 지금가에서 {abs(d):.1f}% 아래(ATR {abs(t['distance_atr'])}배).{far}"


def _drift(t: dict) -> str:
    slope, a = t["slope_pct_per_bar"], {x["bars"]: x for x in t["ahead"]}
    first, last = a[AHEAD[0]], a[AHEAD[-1]]
    way = "내려와" if slope < 0 else "올라가"
    text = (f"선은 봉마다 {slope:+}%씩 {way} {AHEAD[0]}봉 뒤 {_num(first['price'])}, "
            f"{AHEAD[-1]}봉 뒤 {_num(last['price'])}(지금가 대비 {last['distance_pct']:+.1f}%).")
    if _role(t) == "resistance" and slope < 0 and t["status"] != "testing":
        text += " 기다리면 선이 내려와 닿기 쉬워진다."
    return text


def fibonacci(bars: list[dict], zones: list[dict] | None = None, tolerance: float = 0.0) -> dict | None:
    """Retracement of the move between the highest high and lowest low of ``bars``: ratio 0 is
    where the move ended, 1 where it started. A level within ``tolerance`` of a zone in
    ``zones`` (desk.sr levels) carries the strongest such zone as ``confluence`` — the weight is
    shown to the agents next to the level, the zones themselves are not re-scored."""
    if len(bars) < 2:
        return None
    hi = max(range(len(bars)), key=lambda x: bars[x]["high"])
    lo = min(range(len(bars)), key=lambda x: bars[x]["low"])
    high, low = bars[hi]["high"], bars[lo]["low"]
    if high <= low or hi == lo:   # one bar holds both extremes: no move to retrace
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


_STATE_EN = {"intact": "holding", "near": "holding", "far": "holding, far away",
             "testing": "testing it", "broken": "closed through it"}
PROMPT_LINES = 3           # the nearest lines the agents read (#530: more lines, same tokens)


def prompt_note(sr: dict | None) -> str | None:
    """Trendlines and the retracement as a reference block for the deciding agents."""
    if not sr or not (sr.get("trendlines") or sr.get("fibonacci") or sr.get("patterns")):
        return None
    from .patterns import prompt_note as pattern_note  # desk.patterns builds on this module

    if not (sr.get("trendlines") or sr.get("fibonacci")):
        return pattern_note(sr.get("patterns"))   # no lines: the trendline header and advice do not apply
    lines = []
    for t in sorted(sr.get("trendlines") or [], key=lambda t: abs(t["distance_atr"]))[:PROMPT_LINES]:
        name = "Rising support" if t["kind"] == "support" else "Falling resistance"
        swing = f", {t['scale']} swing" if t.get("scale") else ""
        lines.append(f"- {name} ({t['timeframe']}{swing}) at {t['price_now']} now, {len(t['touches'])} touches "
                     f"since {t['anchors'][0]['at'][:10]}, {t['slope_pct_per_bar']:+}% per bar; price "
                     f"{t['distance_atr']:+} ATR from it, {_STATE_EN[t.get('status') or t['state']]}")
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
    if (note := pattern_note(sr.get("patterns"))):
        lines.append(f"- {note}")
    return ("Desk-computed trendlines (straight in log price) and Fibonacci retracement:\n" + "\n".join(lines) + "\n"
            "Where the price is testing a trendline, give both scenarios with their entry condition: the line "
            "holding (e.g. a long on a close back above rising support, stop below it) and a close through it "
            "(the opposite trade, or stand aside). A broken line is no longer support/resistance until retaken. "
            "A retracement level that coincides with a zone is stronger than either alone.")
