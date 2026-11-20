"""#695 head and shoulders (a top) and inverse head and shoulders (a bottom), in desk.patterns' shape.

Three swing peaks with the middle one highest (the head) and the outer two (the shoulders) at about the
same height, and the neckline through the two troughs between them. The inverse is the mirror: three
troughs with the middle one lowest, the neckline through the two peaks between. Prices are compared in
log, as desk.patterns and desk.trend do.

State, read on the closes after the right shoulder (``price``, live, for the last one):
``forming`` (no close through the neckline yet), ``broken`` (a close through it by BREAK_ATR),
``retest`` (broken, and the price came back to the neckline without closing back over it),
``failed`` (broken, then a close back over it - a false break) and ``invalid`` (a close beyond the head).
The target is the head's height over the neckline, from the neckline where it broke (or now).

How often the neckline broke the textbook way, the target was reached and the break failed is measured
on past bars by ``desk.hs_bt`` into ``data/hs_stats.json`` against the same walk on shuffled prices; a
kind and timeframe without an edge over that baseline says ``reference_only``. Arithmetic on price
history, not a forecast.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .trend import BREAK_ATR, TOUCH_ATR, _round, _swing_idx

LOOKBACK = 150             # bars a pattern may span (as desk.patterns)
MAX_PEAKS = 12             # the latest swing peaks looked at
RECENT_RS = 3              # the right shoulder is one of the latest this many peaks
GAP_PEAKS = 3              # the head at most this many peaks after the left shoulder (small swings between)
HEAD_ATR = 1.0             # the head stands at least this far beyond the higher shoulder
SHOULDER_SHARE = 0.5       # the shoulders differ by at most this share of the height
SHOULDER_MIN = 0.25        # each shoulder at least this share of the height beyond the neckline
NECK_SLOPE = 0.5           # the neckline moves at most this share of the height between the shoulders
TIME_RATIO = 3.0           # head-to-shoulder spans within this ratio of each other
FALSE_BARS = 5             # a close back over the neckline this soon after the break: a false break
STALE_SPANS = 1.0          # a break older than the pattern's own span is no longer shown
MIN_ODDS_N = 30
KIND_KO = {"head_shoulders": "헤드앤숄더", "inverse_head_shoulders": "역헤드앤숄더"}
STATE_KO = {"forming": "형성 중", "broken": "넥라인 이탈", "retest": "넥라인 되돌림", "failed": "거짓 이탈",
            "invalid": "무효(머리 넘음)"}
EXPECTED = {"head_shoulders": "down", "inverse_head_shoulders": "up"}
STATS_PATH = Path(__file__).parent / "data" / "hs_stats.json"
TF_KO = {"60m": "1시간봉", "1d": "일봉", "1w": "주봉"}
NO_EDGE_KO = "참고용 · 통계적 우위 없음"
_stats_cache: dict | None = None


def stats_for(timeframe: str, kind: str) -> dict | None:
    """The measured record of this kind on this timeframe (desk.hs_bt), or None."""
    global _stats_cache
    if _stats_cache is None:
        try:
            _stats_cache = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _stats_cache = {}
    return (_stats_cache.get("kinds") or {}).get(f"{timeframe}:{kind}")


def best(bars: list[dict], k: int, atr: float, top: bool):
    """The latest head and shoulders (``top``) or inverse over ``bars`` (already cut to LOOKBACK): a dict of
    the indexes and log prices, or None. desk.hs_bt follows the same shape forward."""
    n = len(bars)
    if n < 8 * k or not atr or any(b["low"] <= 0 or b["close"] <= 0 for b in bars):
        return None
    sign = 1 if top else -1                                  # top: peaks are highs; inverse: peaks are lows
    peak_f, trough_f = ("high", "low") if top else ("low", "high")
    unit = atr / bars[-1]["close"]
    peaks = _swing_idx(bars, k, peak_f)[-MAX_PEAKS:]
    lg = lambda i, f: sign * math.log(bars[i][f])
    found = None
    for r in range(max(2, len(peaks) - RECENT_RS), len(peaks)):
        b = r - 1
        for a in range(max(0, b - GAP_PEAKS), b):
            ls, hd, rs = peaks[a], peaks[b], peaks[r]
            if any(lg(i, peak_f) > lg(hd, peak_f) for i in peaks[a + 1:b]):
                continue                                     # the head is the highest peak between the shoulders
            if rs - ls < 4 * k:
                continue
            left, right = hd - ls, rs - hd
            if max(left, right) > TIME_RATIO * min(left, right):
                continue
            # the troughs between the shoulders and the head: the neckline's two points
            n1 = min(range(ls, hd + 1), key=lambda i: lg(i, trough_f))
            n2 = min(range(hd, rs + 1), key=lambda i: lg(i, trough_f))
            if n2 <= n1:
                continue
            y1, y2 = lg(n1, trough_f), lg(n2, trough_f)
            slope = (y2 - y1) / (n2 - n1)
            neck = lambda x, y1=y1, slope=slope, n1=n1: y1 + slope * (x - n1)
            height = lg(hd, peak_f) - neck(hd)
            if height <= 0:
                continue
            hl, hh, hr = lg(ls, peak_f), lg(hd, peak_f), lg(rs, peak_f)
            if hh - max(hl, hr) < HEAD_ATR * unit:
                continue
            if abs(hl - hr) > SHOULDER_SHARE * height:
                continue
            if min(hl - neck(ls), hr - neck(rs)) < SHOULDER_MIN * height:
                continue
            if abs(slope * (rs - ls)) > NECK_SLOPE * height:
                continue
            # no close beyond the head or through the neckline between the left shoulder and the right one
            if any(sign * math.log(bars[x]["close"]) > hh or sign * math.log(bars[x]["close"]) < neck(x) - BREAK_ATR * unit
                   for x in range(ls, rs + 1)):
                continue
            got = {"ls": ls, "head": hd, "rs": rs, "n1": n1, "n2": n2, "y1": y1, "slope": slope, "height": height,
                   "head_y": hh, "sign": sign, "unit": unit, "peak_f": peak_f, "trough_f": trough_f}
            if found is None or rs > found["rs"] or (rs == found["rs"] and height > found["height"]):
                found = got
    return found


def _neck(p: dict, x: float) -> float:
    return p["y1"] + p["slope"] * (x - p["n1"])


def state_of(bars: list[dict], p: dict, price: float | None = None) -> tuple[str, int | None]:
    """The state after the right shoulder and the bar where the neckline broke (if it did)."""
    last = len(bars) - 1
    sign, tol = p["sign"], BREAK_ATR * p["unit"]
    closes = [sign * math.log(b["close"]) for b in bars]
    if price and price > 0:
        closes[last] = sign * math.log(price)
    broke = None
    for x in range(p["rs"] + 1, last + 1):
        if closes[x] > p["head_y"]:
            return "invalid", broke
        if broke is None and closes[x] < _neck(p, x) - tol:
            broke = x
    if broke is None:
        return "forming", None
    after = range(broke + 1, last + 1)
    if any(closes[x] > _neck(p, x) + tol for x in after):
        return "failed", broke
    touch = TOUCH_ATR * p["unit"]
    near = "high" if sign > 0 else "low"      # the bar's side toward the neckline after the break
    if any(sign * math.log(bars[x][near]) >= _neck(p, x) - touch for x in after):
        return "retest", broke
    return "broken", broke


def find(bars: list[dict], k: int, atr: float, timeframe: str = "1d", price: float | None = None) -> list[dict]:
    """The latest head and shoulders and inverse over the last LOOKBACK bars - each at most one, the one
    whose right shoulder came last first. A break older than the pattern's own span is left out."""
    bars = bars[-LOOKBACK:]
    out = []
    for top in (True, False):
        p = best(bars, k, atr, top)
        if not p:
            continue
        state, broke = state_of(bars, p, price)
        last = len(bars) - 1
        span = p["rs"] - p["ls"]
        if state == "invalid" or (broke is not None and last - broke > STALE_SPANS * span):
            continue
        out.append(describe(bars, p, state, broke, timeframe))
    out.sort(key=lambda d: d["_rs"], reverse=True)
    for d in out:
        d.pop("_rs")
    return out


def describe(bars: list[dict], p: dict, state: str, broke: int | None, timeframe: str) -> dict:
    kind = "head_shoulders" if p["sign"] > 0 else "inverse_head_shoulders"
    sign, last = p["sign"], len(bars) - 1
    price_at = lambda y: _round(math.exp(sign * y))
    point = lambda i, f: {"at": bars[i]["at"], "price": _round(bars[i][f])}
    xb = broke if broke is not None else last
    target = price_at(_neck(p, xb) - p["height"])
    vols = [b.get("volume") or 0 for b in bars]
    left = vols[max(0, p["ls"] - 2): p["n1"] + 1]
    right = vols[p["n2"]: min(len(vols), p["rs"] + 3)]
    vol_lower = (sum(right) / len(right) < sum(left) / len(left)) if left and right and all(left + right) else None
    stats = stats_for(timeframe, kind)
    verdict = (stats or {}).get("verdict")
    out = {
        "kind": kind, "label": KIND_KO[kind], "timeframe": timeframe, "state": state, "state_label": STATE_KO[state],
        "expected": EXPECTED[kind],
        "points": {"left_shoulder": point(p["ls"], p["peak_f"]), "head": point(p["head"], p["peak_f"]),
                   "right_shoulder": point(p["rs"], p["peak_f"]),
                   "neck_left": point(p["n1"], p["trough_f"]), "neck_right": point(p["n2"], p["trough_f"])},
        "neckline": {"from": {"at": bars[p["n1"]]["at"], "price": price_at(_neck(p, p["n1"]))},
                     "to": {"at": bars[last]["at"], "price": price_at(_neck(p, last))},
                     "slope_pct_per_bar": round((math.exp(sign * p["slope"]) - 1) * 100, 3)},
        "broke_at": bars[broke]["at"] if broke is not None else None,
        "target": target, "height_pct": round((math.exp(p["height"]) - 1) * 100, 2),
        "shoulder_diff_pct": round(abs(bars[p["ls"]][p["peak_f"]] / bars[p["rs"]][p["peak_f"]] - 1) * 100, 2),
        "volume_right_lower": vol_lower,
        "stats": stats,
        "odds": _odds(stats),
        "reference_only": verdict != "edge",
        "reference_label": NO_EDGE_KO if verdict in ("no_edge", "unstable") else (
            "참고용 · 과거 통계 없음" if verdict != "edge" else None),
        "_rs": p["rs"],
    }
    out["explain"] = explain(out)
    return out


def _odds(stats: dict | None) -> dict | None:
    if not stats:
        return None
    n = stats.get("n") or 0
    return {"expected_pct": stats.get("expected_pct"), "false_break_pct": stats.get("false_break_pct"),
            "reach_pct": stats.get("reach_pct"), "n": n, "small_sample": n < MIN_ODDS_N,
            "verdict": stats.get("verdict"), "edge_pct": stats.get("edge_pct")}


def _fmt(price: float) -> str:
    return f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"


def explain(p: dict) -> str:
    """One plain sentence: the shape, the state, the neckline and the target, the record."""
    tf = TF_KO.get(p["timeframe"], p["timeframe"])
    top = p["kind"] == "head_shoulders"
    neck = p["neckline"]["to"]["price"]
    head = f"{tf} {p['label']} — {'세 봉우리 가운데 머리가 가장 높고' if top else '세 골 가운데 머리가 가장 낮고'} 양 어깨가 비슷해요."
    side = "아래" if top else "위"
    state = {"forming": f" 아직 넥라인({_fmt(neck)})을 종가로 {'깨지' if top else '넘지'} 않았어요.",
             "broken": f" 넥라인을 {side}로 {'깼어요' if top else '넘었어요'}.",
             "retest": f" 넥라인을 {side}로 {'깬' if top else '넘은'} 뒤 넥라인 근처로 되돌아왔어요.",
             "failed": f" 넥라인을 {side}로 나갔다가 다시 돌아왔어요(거짓 이탈)."}[p["state"]]
    target = f" 머리 높이만큼이면 목표 {_fmt(p['target'])}." if p["state"] != "failed" else ""
    vol = " 오른어깨 거래량이 왼어깨보다 적어요." if p.get("volume_right_lower") else ""
    o = p.get("odds")
    if o and o.get("expected_pct") is not None:
        small = ", 표본 적음" if o["small_sample"] else ""
        rec = (f" 과거 넥라인 쪽으로 깬 비율 {o['expected_pct']:.0f}%(표본 {o['n']}{small})"
               + (f", 목표 도달 {o['reach_pct']:.0f}%" if o.get("reach_pct") is not None else "")
               + (f", 거짓 이탈 {o['false_break_pct']:.0f}%" if o.get("false_break_pct") is not None else "") + ".")
    else:
        rec = " 과거 통계는 아직 없어요."
    ref = f" {NO_EDGE_KO}." if p.get("reference_label") == NO_EDGE_KO else ""
    return head + state + target + vol + rec + ref + " 예측 아님."
