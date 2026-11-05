"""The trade map (매매 지도, issue #106): entry → first / second target → the levels to watch on
the way back → stop, each with the reason it sits there and what to do at it.

Every number is the desk's arithmetic, never the model's: the plan's own entry / stop / target,
the support/resistance zones the agents were given (desk.sr, with their strength) and the volume
profile's thick nodes (desk.volume_profile). A long map reads upward from the entry for targets
and downward for the guards; a short map is the mirror. A hold plan gets two maps, one from each
trigger. Missing levels are filled with ATR multiples and say so.
"""

from __future__ import annotations

from . import volume_profile

MIN_GAP_ATR = 0.3       # a level closer than this to the entry is the entry's own zone
MERGE_ATR = 0.5         # levels this close are one zone
FILL_ATR = 2.0          # targets / stop filled at these ATR steps when the chart gives none
STOP_FILL_ATR = 1.5
BEYOND_ATR = 0.25       # a computed stop sits this far past the last guard
STRONG = 3.0            # sr strength from which a target counts as a heavy wall
FALLBACK_ATR_PCT = 0.02


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def _candidates(sr: dict | None, plan_target: float | None) -> list[dict]:
    out = []
    for lv in (sr or {}).get("levels") or []:
        out.append({"price": lv["price"], "source": "sr", "strength": lv.get("strength"), "basis": lv.get("basis")})
    vp = (sr or {}).get("volume_profile")
    for node in (vp or {}).get("nodes") or []:
        if node["kind"] == "thick":
            out.append({"price": node["price"], "source": "volume", "strength": None, "basis": "매물대"})
    if vp and vp.get("poc") is not None:
        out.append({"price": vp["poc"], "source": "volume", "strength": None, "basis": "최다 거래 가격(POC)"})
    if plan_target is not None:
        out.append({"price": plan_target, "source": "plan", "strength": None, "basis": "계획 목표가"})
    return out


def _zones(cands: list[dict], atr: float, up: bool) -> list[dict]:
    """Merge levels within MERGE_ATR into one zone, nearest first. A zone shows the plan's price
    when the plan is in it, else its strongest support/resistance, else the volume node."""
    rank = {"plan": 0, "sr": 1, "volume": 2}
    cands = sorted(cands, key=lambda c: c["price"], reverse=not up)
    zones: list[list[dict]] = []
    for c in cands:
        if zones and abs(c["price"] - zones[-1][0]["price"]) <= MERGE_ATR * atr:
            zones[-1].append(c)
        else:
            zones.append([c])
    out = []
    for zone in zones:
        head = min(zone, key=lambda c: (rank[c["source"]], -(c["strength"] or 0)))
        strength = max((c["strength"] or 0) for c in zone) or None
        sources = sorted({c["source"] for c in zone}, key=lambda s: rank[s])
        basis = " · ".join(dict.fromkeys(c["basis"] for c in zone if c["basis"]))
        out.append({"price": head["price"], "source": head["source"], "sources": sources,
                    "strength": strength, "basis": basis or None})
    return out


def _step(role: str, zone: dict | None, price: float, action: str, vp: dict | None, **extra) -> dict:
    z = zone or {"source": "atr", "sources": ["atr"], "strength": None, "basis": None}
    return {"role": role, "price": _round(price), "source": z["source"], "sources": z["sources"],
            "strength": z["strength"], "basis": z["basis"], "volume": volume_profile.density(vp, price),
            "action": action, **extra}


def side_map(side: str, entry: float, stop: float | None, target: float | None, sr: dict | None,
             atr: float, entry_type: str | None = None, origin: str = "plan") -> dict:
    """One direction's map. ``origin``: "plan" (the plan's own levels) or "trigger" (a hold
    plan's long/short trigger as the entry, no plan stop or target)."""
    up = side == "long"
    sign = 1 if up else -1
    vp = (sr or {}).get("volume_profile")
    cands = _candidates(sr, target)
    ahead = [c for c in cands if sign * (c["price"] - entry) > MIN_GAP_ATR * atr]
    behind = [c for c in cands if sign * (entry - c["price"]) > MIN_GAP_ATR * atr and c["source"] != "plan"]
    targets = _zones(ahead, atr, up)
    guards = _zones(behind, atr, not up)

    t_idx = next((i for i, z in enumerate(targets) if "plan" in z["sources"]), None)
    if t_idx is None or t_idx == 0:
        t1 = targets[0] if targets else None
        t2 = targets[1] if len(targets) > 1 else None
    else:
        t1, t2 = targets[0], targets[t_idx]
    p1 = t1["price"] if t1 else entry + sign * FILL_ATR * atr
    p2 = t2["price"] if t2 else p1 + sign * FILL_ATR * atr

    if stop is not None:
        stop_zone = {"source": "plan", "sources": ["plan"], "strength": None, "basis": "계획 손절가"}
        p_stop = stop
    else:
        stop_zone = None
        last = guards[1] if len(guards) > 1 else guards[0] if guards else None
        p_stop = (last["price"] - sign * BEYOND_ATR * atr) if last else entry - sign * STOP_FILL_ATR * atr
    # only the guards between the entry and the stop matter; past the stop the position is gone
    guards = [g for g in guards if sign * (g["price"] - p_stop) > 0][:2]

    risk = abs(entry - p_stop) or None
    heavy = bool(t1) and ((t1["strength"] or 0) >= STRONG or volume_profile.density(vp, p1) == "thick")
    steps = [
        _step("target2", t2, p2, "take_rest", vp, rr=round(abs(p2 - entry) / risk, 2) if risk else None),
        _step("target1", t1, p1, "take_partial", vp, share=50 if heavy else 33, then_stop=_round(entry),
              rr=round(abs(p1 - entry) / risk, 2) if risk else None),
        _step("entry", {"source": origin, "sources": [origin], "strength": None,
                        "basis": "계획 진입가" if origin == "plan" else ("롱 조건" if up else "숏 조건")},
              entry, "enter", vp, entry_type=entry_type),
        *[_step(f"guard{i + 1}", g, g["price"], "hold_check" if i == 0 else "reduce", vp) for i, g in enumerate(guards)],
        _step("stop", stop_zone, p_stop, "exit", vp),
    ]
    if not up:
        steps.reverse()   # always highest price first
    return {"side": side, "origin": origin, "steps": steps}


def build(plan: dict, sr: dict | None) -> dict | None:
    """The plan's trade map(s), or None when there is nothing to anchor on."""
    sr = sr or plan.get("support_resistance")
    entry, stop, target = plan.get("entry_price"), plan.get("stop_loss"), plan.get("price_target")
    ref = (plan.get("live_price") or {}).get("price") or (sr or {}).get("reference_price") or entry
    atr = (sr or {}).get("atr") or plan.get("atr") or (FALLBACK_ATR_PCT * ref if ref else None)
    if not atr:
        return None
    opinion = plan.get("opinion")
    bias = plan.get("bias")
    side = ("long" if opinion == "buy" else "short" if opinion == "sell" else
            bias if opinion is None and bias in ("long", "short") else None)
    maps = []
    if side and entry is not None:
        maps.append(side_map(side, entry, stop, target, sr, atr, plan.get("entry_type")))
    else:
        if plan.get("long_trigger") is not None:
            maps.append(side_map("long", plan["long_trigger"], None, None, sr, atr, "stop", origin="trigger"))
        if plan.get("short_trigger") is not None:
            maps.append(side_map("short", plan["short_trigger"], None, None, sr, atr, "stop", origin="trigger"))
        if not maps and bias in ("long", "short") and entry is not None:
            maps.append(side_map(bias, entry, stop, target, sr, atr, plan.get("entry_type")))
    if not maps:
        return None
    vp = (sr or {}).get("volume_profile")
    return {"maps": maps, "reference_price": _round(ref) if ref else None, "atr": _round(atr),
            "volume_profile": bool(vp), "levels": bool((sr or {}).get("levels"))}
