"""#695 where a weekly chart stands in a Minsky-style bubble: a quiet rise, awareness, mania (a parabola far
over the 40-week line), the first sharp drop, the "bull trap" rebound that fails, and fear (back under the
40-week line, far off the peak).

The picture is easy to fit after the fact, so the stage here is read from numbers only, each of them
returned with it (``numbers``):
- ``dist_40w_pct``: the close over the 40-week moving average
- ``accel``: the last 13 weeks' log slope over the 26 weeks' before
- ``drawdown_pct``: the close under the highest close of the last 104 weeks
- ``peak_dist_40w_pct``: how far over its 40-week line that peak stood (a mania peak: MANIA_DIST or more)
- ``drop_pct`` / ``retrace_pct``: the fall from the peak to the lowest close after it, and how much of
  that fall the rebound after it took back
- ``volume_x``: the last 13 weeks' volume over the 52 weeks' before

``caution`` is on for mania, the first drop and the bull trap. What came after the same reading in the
past (13 / 26 / 52-week returns and how often a 30% fall followed within a year, against every week of the
same tickers) is measured by ``desk.bubble_bt`` into ``data/bubble_stats.json``. A reading, not a forecast.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

MA_WEEKS = 40
PEAK_WEEKS = 104
MIN_WEEKS = MA_WEEKS + 26
MANIA_DIST = 0.40          # the close this far over the 40-week line ...
MANIA_ACCEL = 1.5          # ... and rising this much faster than before: a parabola
MANIA_NEAR_PEAK = -0.10    # ... and within this of its peak
AWARE_DIST = 0.15
FIRST_DROP = -0.20         # a fall this deep from a mania peak: the first drop
TRAP_RETRACE = 0.50        # a rebound taking back this much of the fall ...
TRAP_ROLL = -0.05          # the close this far off the rebound's high: the trap closing (said, not required)
FEAR_DRAWDOWN = -0.35      # this far off the peak and under the 40-week line: fear
STAGE_KO = {"quiet": "조용한 상승", "awareness": "관심", "mania": "열광 · 포물선", "first_drop": "첫 급락",
            "bull_trap": "반등 실패(불 트랩)", "fear": "공포 · 평균 회귀", "none": "해당 없음"}
CAUTION = ("mania", "first_drop", "bull_trap")
STATS_PATH = Path(__file__).parent / "data" / "bubble_stats.json"
MIN_N = 30
_stats_cache: dict | None = None


def stats_for(stage: str) -> dict | None:
    global _stats_cache
    if _stats_cache is None:
        try:
            _stats_cache = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _stats_cache = {}
    return (_stats_cache.get("stages") or {}).get(stage)


def numbers(weeks: list[dict]) -> dict | None:
    """The readings over finished weekly bars (oldest first), or None with too short a history."""
    closes = [b["close"] for b in weeks if b.get("close")]
    if len(closes) < MIN_WEEKS or min(closes) <= 0:
        return None
    close = closes[-1]
    ma = sum(closes[-MA_WEEKS:]) / MA_WEEKS
    ma_before = sum(closes[-MA_WEEKS - 4:-4]) / MA_WEEKS
    dist = close / ma - 1
    recent = math.log(close / closes[-14]) / 13
    before = math.log(closes[-14] / closes[-40]) / 26 if len(closes) >= 40 else 0.0
    accel = recent / before if before > 0 else None
    window = closes[-PEAK_WEEKS:]
    pk = max(range(len(window)), key=lambda i: window[i])
    peak = window[pk]
    at = len(closes) - len(window) + pk                       # the peak's index in ``closes``
    peak_ma = sum(closes[max(0, at - MA_WEEKS + 1): at + 1]) / len(closes[max(0, at - MA_WEEKS + 1): at + 1])
    after = closes[at:]
    lo_i = min(range(len(after)), key=lambda i: after[i])
    trough = after[lo_i]
    rebound = max(after[lo_i:]) if lo_i < len(after) else trough
    drop = trough / peak - 1
    retrace = (rebound - trough) / (peak - trough) if peak > trough else 0.0
    vols = [b.get("volume") or 0 for b in weeks]
    vx = None
    if len(vols) >= 65 and all(vols[-65:]):
        vx = (sum(vols[-13:]) / 13) / (sum(vols[-65:-13]) / 52)
    return {"close": close, "ma_40w": round(ma, 4), "ma_rising": ma > ma_before,
            "dist_40w_pct": round(dist * 100, 1), "accel": round(accel, 2) if accel is not None else None,
            "drawdown_pct": round((close / peak - 1) * 100, 1), "peak": peak, "peak_weeks_ago": len(closes) - 1 - at,
            "peak_dist_40w_pct": round((peak / peak_ma - 1) * 100, 1), "drop_pct": round(drop * 100, 1),
            "retrace_pct": round(retrace * 100, 1), "rebound_high": rebound,
            "off_rebound_pct": round((close / rebound - 1) * 100, 1) if rebound else None,
            "volume_x": round(vx, 2) if vx is not None else None}


def classify(n: dict) -> str:
    """The stage from the readings (see the module notes)."""
    dist, dd = n["dist_40w_pct"] / 100, n["drawdown_pct"] / 100
    mania_peak = n["peak_dist_40w_pct"] / 100 >= MANIA_DIST
    if dist >= MANIA_DIST and dd > MANIA_NEAR_PEAK and (n["accel"] or 0) >= MANIA_ACCEL:
        return "mania"
    if mania_peak and n["peak_weeks_ago"] > 0 and n["drop_pct"] / 100 <= FIRST_DROP:
        if dd <= FEAR_DRAWDOWN and dist < 0 and n["retrace_pct"] / 100 < TRAP_RETRACE:
            return "fear"
        if n["retrace_pct"] / 100 >= TRAP_RETRACE and dd < 0:      # the rebound phase, rolling over or not yet
            return "bull_trap"
        if n["retrace_pct"] / 100 < TRAP_RETRACE:
            return "first_drop"
    if dd <= FEAR_DRAWDOWN and dist < 0 and mania_peak:
        return "fear"
    if n["ma_rising"] and AWARE_DIST <= dist < MANIA_DIST:
        return "awareness"
    if n["ma_rising"] and 0 <= dist < AWARE_DIST:
        return "quiet"
    return "none"


def stage(weeks: list[dict]) -> dict | None:
    """The stage of a weekly chart, with its numbers and the past record of the same reading."""
    n = numbers(weeks)
    if n is None:
        return None
    st = classify(n)
    rec = stats_for(st)
    verdict = (rec or {}).get("verdict")
    out = {"stage": st, "label": STAGE_KO[st], "caution": st in CAUTION, "numbers": n, "stats": rec,
           "reference_only": verdict != "edge",
           "reference_label": ("참고용 · 통계적 우위 없음" if verdict in ("no_edge", "unstable") else
                               "참고용 · 과거 통계 없음" if verdict != "edge" else None)}
    out["explain"] = explain(out)
    return out


def explain(s: dict) -> str:
    n, st = s["numbers"], s["stage"]
    head = f"주봉 민스키형 단계: {s['label']}."
    why = {"mania": f" 40주선보다 {n['dist_40w_pct']:.0f}% 위, 최근 13주 기울기가 그 전의 {n['accel']}배예요.",
           "first_drop": f" 40주선보다 {n['peak_dist_40w_pct']:.0f}% 높던 고점에서 {abs(n['drop_pct']):.0f}% 빠졌고 "
                         f"반등은 그 낙폭의 {n['retrace_pct']:.0f}% 예요.",
           "bull_trap": f" 고점에서 {abs(n['drop_pct']):.0f}% 빠진 뒤 낙폭의 {n['retrace_pct']:.0f}% 를 되찾았어요."
                        + (f" 반등 고점보다 {abs(n['off_rebound_pct']):.0f}% 내려와 반등이 꺾이는 중이에요."
                           if (n.get("off_rebound_pct") or 0) / 100 <= TRAP_ROLL else " 아직 전고점은 넘지 못했어요."),
           "fear": f" 고점보다 {abs(n['drawdown_pct']):.0f}% 아래, 40주선 아래예요.",
           "awareness": f" 40주선보다 {n['dist_40w_pct']:.0f}% 위에서 오르는 중이에요.",
           "quiet": f" 40주선 근처({n['dist_40w_pct']:+.0f}%)에서 천천히 오르는 중이에요.",
           "none": ""}[st]
    rec = s.get("stats") or {}
    after = ""
    if rec.get("crash30_pct") is not None:
        small = ", 표본 적음" if (rec.get("n") or 0) < MIN_N else ""
        after = (f" 과거 같은 단계 뒤 52주 안에 30% 넘게 빠진 경우 {rec['crash30_pct']:.0f}%"
                 f"(모든 주 평균 {rec.get('base_crash30_pct', 0):.0f}%, 표본 {rec.get('n')}{small}).")
    caution = " 들어가기 조심스러운 자리예요." if s["caution"] else ""
    ref = f" {s['reference_label']}." if s.get("reference_label") == "참고용 · 통계적 우위 없음" else ""
    return head + why + after + caution + ref + " 지나고 나서 맞춰 보기 쉬운 그림이라 숫자로만 봐요. 예측 아님."
