"""A run's look ahead, frozen when it ends (#437): the chart's projection (#299, ``desk.projection``)
read at one week and one month, with the analysis' decision laid on it as a direction and a pick
among the projection's own scenarios.

No new forecast: every price here is the desk's - the projection's calibrated cone, its paths
through the desk's levels and their first-touch odds, and the plan's own stop as the line that
voids the call. The model's part is only what it already decided (rating, bias) and the closing
summary's one line. The live projection moves with the bars; this one stays as it was when the
plan was made, so the page can show it beside the plan and a later pass can grade it (``due``).

The result goes out as an ``outlook`` event (no schema change) and the run detail carries the
newest one. Arithmetic on price history, not a forecast.
"""

from __future__ import annotations

import datetime as dt
import math
import random

from . import projection

VERSION = 1
TIMEFRAME = "1d"
PERIODS = {"1w": {"bars": 5, "days": 7, "label": "1주"}, "1m": {"bars": 20, "days": 30, "label": "1개월"}}
LEAN = {"long": "up", "short": "down", "flat": "sideways"}
SCENARIO = {"up": "bull", "down": "bear", "sideways": "range"}
DISCLAIMER = "예상 범위와 시나리오이며 예측이 아닙니다. 투자 권유가 아닙니다."
REASON_CHARS = 200


def _period(proj: dict, bars: int) -> dict | None:
    """The cone at ``bars`` ahead (or its last bar when shorter): the 68% and 95% bands."""
    cone = proj.get("cone") or []
    if not cone:
        return None
    at = cone[min(bars, len(cone)) - 1]
    return {"bars": at["bar"], "band68": [at["p16"], at["p84"]], "band95": [at["p2_5"], at["p97_5"]]}


def _reason(plan: dict) -> str | None:
    text = ((plan.get("consensus") or {}).get("conclusion") or "").strip()
    return text[:REASON_CHARS] or None


def build(proj: dict, plan: dict, as_of: str, trade_date: str) -> dict:
    """The outlook from a ``desk.projection.project`` result on the daily frame and the finished plan.

    ``direction`` follows the plan's bias (long up, short down, otherwise sideways); ``scenario`` is
    the projection path of that direction when the projection has one, with its first-touch odds,
    and ``agrees`` says whether that path is also the likeliest of the projection's paths.
    ``invalidation`` is the plan's stop on a long or short call (none on a flat one)."""
    bias = plan.get("bias")
    direction = LEAN.get(bias or "", "sideways")
    paths = proj.get("paths") or []
    by_name = {p["name"]: p for p in paths}
    pick = by_name.get(SCENARIO[direction])
    likeliest = max(paths, key=lambda p: p.get("prob") or 0)["name"] if paths else None
    stop = plan.get("stop_loss") if direction in ("up", "down") else None
    day = dt.date.fromisoformat(trade_date)
    periods = {}
    for key, spec in PERIODS.items():
        cell = _period(proj, spec["bars"])
        if cell:
            periods[key] = {**cell, "label": spec["label"], "due": (day + dt.timedelta(days=spec["days"])).isoformat()}
    return {
        "version": VERSION,
        "as_of": as_of,
        "trade_date": trade_date,
        "timeframe": proj.get("timeframe"),
        "price": proj.get("price"),
        "sigma": proj.get("sigma"),
        "periods": periods,
        "paths": paths,
        "first_touch": proj.get("first_touch"),
        "levels": proj.get("levels") or {"above": [], "below": []},
        "calibration": proj.get("calibration"),
        "decision": {
            "rating": plan.get("rating"),
            "bias": bias,
            "direction": direction,
            "scenario": pick["name"] if pick else None,
            "scenario_prob": pick.get("prob") if pick else None,
            "likeliest": likeliest,
            "agrees": (pick["name"] == likeliest) if pick and likeliest else None,
            "desk_direction": (plan.get("direction") or {}).get("bias"),   # #166 rule-based, for comparison
            "reason": _reason(plan),
        },
        "invalidation": stop if isinstance(stop, (int, float)) else None,
        "note_ko": proj.get("note_ko"),
        "disclaimer": DISCLAIMER,
    }


def render(outlook: dict) -> str:
    """The one line the run's log shows for the event (the page draws the rest)."""
    d = outlook["decision"]
    ko = {"up": "상승", "down": "하락", "sideways": "횡보"}[d["direction"]]
    cells = []
    for key in PERIODS:
        cell = outlook["periods"].get(key)
        if cell:
            lo, hi = cell["band68"]
            cells.append(f"{cell['label']} {projection.fmt(lo)}~{projection.fmt(hi)}")
    return f"전망 고정: {ko} 쪽" + (f" · {' · '.join(cells)}(68%)" if cells else "")


def fake_projection(price: float, sr: dict | None = None, seed: int = 437) -> dict:
    """DESK_FAKE_RUNS: the projection on a scripted daily walk around ``price`` (no prices source),
    through the scripted levels when the plan has them, else 5% above and below."""
    rng = random.Random(seed)
    closes = [price]
    for _ in range(projection.SIGMA_BARS + 5):
        closes.append(closes[-1] * math.exp(rng.gauss(0.0, 0.018)))
    scale = price / closes[-1]
    bars = [{"close": c * scale} for c in closes]
    levels = sr if sr and sr.get("levels") else {"levels": [{"price": price * 1.05, "kind": "resistance", "basis": "대본 저항"},
                                                            {"price": price * 0.95, "kind": "support", "basis": "대본 지지"}]}
    return projection.project(levels, bars, TIMEFRAME, price)
