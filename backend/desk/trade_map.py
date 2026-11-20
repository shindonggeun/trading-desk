"""The trade map (매매 지도, issue #106): entry → first / second target → the levels to watch on
the way back → stop, each with the reason it sits there and what to do at it.

Every number is the desk's arithmetic, never the model's: the plan's own entry / stop / target,
the support/resistance zones the agents were given (desk.sr, with their strength) and the volume
profile's thick nodes (desk.volume_profile). A long map reads upward from the entry for targets
and downward for the guards; a short map is the mirror. A hold plan gets two maps, one from each
trigger. Missing levels are filled with ATR multiples and say so.
"""

from __future__ import annotations

from . import trade_map_record, volume_profile

MIN_GAP_ATR = 0.3       # a level closer than this to the entry is the entry's own zone
MERGE_ATR = 0.5         # levels this close are one zone
FILL_ATR = 2.0          # targets / stop filled at these ATR steps when the chart gives none
STOP_FILL_ATR = 1.5
BEYOND_ATR = 0.25       # a computed stop sits this far past the last guard
STRONG = 3.0            # sr strength from which a target counts as a heavy wall
FALLBACK_ATR_PCT = 0.02
# #652 the two entry scenarios per side
SUPPORT_STRENGTH = 2.0  # an sr level this strong (or a thick volume node) is a support worth entering at
SUPPORT_REACH_ATR = 4.0  # a support further than this from the price is no "near" entry
AT_ZONE_ATR = 1.0       # within this above a support's top the price is already at it: the entry is now
ZONE_ENTRY_ATR = 0.25   # else a limit this far above the support zone's top
SUPPORT_STOP_ATR = 0.5  # a support entry's stop: this far past the support zone's far edge
BREAKOUT_STOP_ATR = 0.5  # a breakout's stop: this far back under the level it broke
BROKEN_REACH_ATR = 1.5  # the broken level is looked for this far behind a breakout entry
BREAKOUT_ATR = 0.25     # without a trigger, a breakout enters this far past the nearest resistance
MAX_STOP_ATR = 3.0      # no computed stop further than this from its entry (the plan's own stop aside)
LOW_RR = 1.5


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
             atr: float, entry_type: str | None = None, origin: str = "plan", ref: float | None = None) -> dict:
    """One direction's map. ``origin``: "plan" (the plan's own levels) or "trigger" (a hold
    plan's long/short trigger as the entry, no plan stop or target)."""
    up = side == "long"
    sign = 1 if up else -1
    vp = (sr or {}).get("volume_profile")
    # The plan's own levels only count on their proper side: a long's stop above its entry or a
    # target behind it is a model mistake — set aside (and said so), the chart fills the slot.
    ignored: dict[str, float] = {}
    if stop is not None and sign * (entry - stop) <= 0:
        ignored["stop_loss"], stop = stop, None
    if target is not None and sign * (target - entry) <= MIN_GAP_ATR * atr:
        ignored["price_target"], target = target, None
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
    if p1 <= 0:   # a short filled from ATR on a cheap product: no target below zero
        t1, p1 = None, None
    if p2 is not None and p2 <= 0 or p1 is None:
        t2, p2 = None, None

    if stop is not None:
        stop_zone = {"source": "plan", "sources": ["plan"], "strength": None, "basis": "계획 손절가"}
        p_stop = stop
    elif origin == "trigger":
        # #652 a breakout fails when the price falls back under the level it broke - not two supports down
        # the same price the scenarios use (build's ref: live, else the levels' close) - one stop on one screen
        stop_zone, p_stop, _ = _breakout_stop(cands, entry, atr, sign,
                                              ref if ref is not None else (sr or {}).get("reference_price"))
    else:
        stop_zone = None
        last = guards[1] if len(guards) > 1 else guards[0] if guards else None
        p_stop = (last["price"] - sign * BEYOND_ATR * atr) if last else entry - sign * STOP_FILL_ATR * atr
        p_stop = _clamp_stop(entry, p_stop, atr, sign)
    # only the guards between the entry and the stop matter; past the stop the position is gone
    guards = [g for g in guards if sign * (g["price"] - p_stop) > 0][:2]

    risk = abs(entry - p_stop) or None
    heavy = p1 is not None and (((t1 or {}).get("strength") or 0) >= STRONG
                                or volume_profile.density(vp, p1) == "thick")
    rr = (lambda p: round(abs(p - entry) / risk, 2) if risk else None)
    targets_out = []
    if p2 is not None:
        targets_out.append(_step("target2", t2, p2, "take_rest", vp, rr=rr(p2),
                                 gap_thin=volume_profile.gap_thin(vp, p1, p2)))
    if p1 is not None:
        targets_out.append(_step("target1", t1, p1, "take_partial", vp, share=50 if heavy else 33, heavy=heavy,
                                 then_stop=_round(entry), rr=rr(p1)))
    steps = [
        *targets_out,
        _step("entry", {"source": origin, "sources": [origin], "strength": None,
                        "basis": "계획 진입가" if origin == "plan" else ("롱 조건" if up else "숏 조건")},
              entry, "enter", vp, entry_type=entry_type),
        *[_step(f"guard{i + 1}", g, g["price"], "hold_check" if i == 0 else "reduce", vp) for i, g in enumerate(guards)],
        _step("stop", stop_zone, p_stop, "exit", vp),
    ]
    steps.sort(key=lambda s: -s["price"])   # always highest price first
    return {"side": side, "origin": origin, "steps": steps, "ignored": ignored}


def _clamp_stop(entry: float, stop: float, atr: float, sign: int) -> float:
    """A computed stop no further than MAX_STOP_ATR from its entry."""
    return entry - sign * min(sign * (entry - stop), MAX_STOP_ATR * atr)


def _breakout_stop(cands: list[dict], entry: float, atr: float, sign: int,
                   ref: float | None = None) -> tuple[dict | None, float, float | None]:
    """A breakout's stop: BREAKOUT_STOP_ATR back past the level it broke - the zone right behind the entry
    (within BROKEN_REACH_ATR; levels within MERGE_ATR of its nearest one are that zone, its far edge is the
    base), else the entry itself. A level on the price's other side (a support under the price for a long)
    is not what a breakout breaks. Returns (the stop's zone, the stop, the broken level's price)."""
    broken = sorted((c for c in cands if c["source"] != "plan"
                     and 0 <= sign * (entry - c["price"]) <= BROKEN_REACH_ATR * atr
                     # the price's other side is out - unless the price is already past the entry
                     and (ref is None or sign * (entry - ref) <= 0 or sign * (c["price"] - ref) >= 0)),
                    key=lambda c: -sign * c["price"])                # nearest the entry first
    zone_lv = [c for c in broken if abs(c["price"] - broken[0]["price"]) <= MERGE_ATR * atr] if broken else []
    zone = level = None
    base = entry
    if zone_lv:
        level = broken[0]["price"]
        base = min(zone_lv, key=lambda c: sign * c["price"])["price"]  # the zone's far edge
        what = " · ".join(dict.fromkeys(c["basis"] for c in zone_lv if c["basis"])) or "지지 · 저항"
        head = max(zone_lv, key=lambda c: c["strength"] or 0)
        zone = {"source": head["source"], "sources": sorted({c["source"] for c in zone_lv}),
                "strength": max((c["strength"] or 0) for c in zone_lv) or None,
                "basis": f"돌파한 레벨({what}) 아래" if sign > 0 else f"이탈한 레벨({what}) 위"}
    stop = base - sign * BREAKOUT_STOP_ATR * atr
    if sign * (entry - stop) < BREAKOUT_STOP_ATR * atr:         # at least half an ATR of room
        stop = entry - sign * BREAKOUT_STOP_ATR * atr
    return zone, _clamp_stop(entry, stop, atr, sign), level


def _strong(c: dict) -> bool:
    return (c["source"] == "sr" and (c["strength"] or 0) >= SUPPORT_STRENGTH) or \
        (c["source"] == "volume" and c["basis"] == "매물대")


def _targets(cands: list[dict], entry: float, atr: float, sign: int, risk: float) -> list[dict]:
    """The first two zones ahead of the entry (ATR steps when the chart has none), with their reward/risk."""
    ahead = [c for c in cands if sign * (c["price"] - entry) > MIN_GAP_ATR * atr]
    zones = _zones(ahead, atr, sign > 0)[:2]
    prices = [z["price"] for z in zones]
    bases = [z["basis"] for z in zones]
    while len(prices) < 2:
        prices.append((prices[-1] if prices else entry) + sign * FILL_ATR * atr)
        bases.append("ATR 2배 간격(그 너머 레벨 없음)")
    return [{"price": _round(p), "rr": round(abs(p - entry) / risk, 2), "basis": b}
            for p, b in zip(prices, bases, strict=True) if p > 0]


def _scenario(side: str, kind: str, entry: float, entry_type: str, stop: float, level: dict | None,
              cands: list[dict], ref: float, atr: float) -> dict | None:
    sign = 1 if side == "long" else -1
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0 or stop <= 0:
        return None
    targets = _targets(cands, entry, atr, sign, risk)
    rr = targets[0]["rr"] if targets else None
    return {"side": side, "kind": kind, "entry": _round(entry), "entry_type": entry_type, "stop": _round(stop),
            "stop_atr": round(risk / atr, 2), "stop_pct": round(risk / entry * 100, 1),
            "targets": targets, "rr": rr, "low_rr": rr is None or rr < LOW_RR,
            "distance_pct": round((entry / ref - 1) * 100, 2) if ref else None,
            "level": _round(level["price"]) if level else None, "basis": (level or {}).get("basis")}


def scenarios(sr: dict | None, ref: float, atr: float, side: str, trigger: float | None = None) -> list[dict]:
    """#652 two ways into ``side``: a **breakout** past the nearest resistance (support for a short) - the
    trigger when the plan has one - its stop back under the broken level; and a **support** entry at the
    nearest strong support (resistance) within SUPPORT_REACH_ATR, its stop past that zone's far edge. Every
    stop at most MAX_STOP_ATR from its entry. Plan arithmetic on the chart's levels, not a forecast."""
    sign = 1 if side == "long" else -1
    cands = _candidates(sr, None)
    out = []
    entry = trigger
    if entry is None:
        ahead = [c for c in cands if c["source"] == "sr" and 0 < sign * (c["price"] - ref) <= SUPPORT_REACH_ATR * atr]
        if ahead:
            nearest = min(ahead, key=lambda c: sign * c["price"])
            entry = nearest["price"] + sign * BREAKOUT_ATR * atr
    if entry is not None:
        zone, stop, level = _breakout_stop(cands, entry, atr, sign, ref)
        broken = {"price": level, "basis": (zone or {}).get("basis")} if level is not None else None
        sc = _scenario(side, "breakout", entry, "stop", stop, broken, cands, ref, atr)
        if sc:
            out.append(sc)
    # support (resistance for a short): the nearest strong one on the far side of the price, or the price in it
    near = [c for c in cands
            if _strong(c) and 0 <= sign * (ref - c["price"]) <= SUPPORT_REACH_ATR * atr]
    if near:
        top = max(near, key=lambda c: sign * c["price"])            # the one nearest the price
        zone = [c for c in cands if c["source"] != "plan" and abs(c["price"] - top["price"]) <= MERGE_ATR * atr
                and sign * (ref - c["price"]) >= 0]                 # the zone stays on its side of the price
        edge = max(zone, key=lambda c: sign * c["price"])["price"]  # the zone's near edge (its top for a long)
        far = min(zone, key=lambda c: sign * c["price"])["price"]   # its far edge
        if sign * (ref - edge) <= AT_ZONE_ATR * atr:
            entry, how = ref, "now"
        else:
            entry, how = edge + sign * ZONE_ENTRY_ATR * atr, "limit"
        stop = _clamp_stop(entry, far - sign * SUPPORT_STOP_ATR * atr, atr, sign)
        basis = " · ".join(dict.fromkeys(c["basis"] for c in zone if c["basis"])) or None
        sc = _scenario(side, "support", entry, how, stop, {"price": top["price"], "basis": basis}, cands, ref, atr)
        if sc:
            sc["strength"] = max((c["strength"] or 0) for c in zone) or None
            out.append(sc)
    return out


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
            maps.append(side_map("long", plan["long_trigger"], None, None, sr, atr, "stop", origin="trigger",
                                ref=ref))
        if plan.get("short_trigger") is not None:
            maps.append(side_map("short", plan["short_trigger"], None, None, sr, atr, "stop", origin="trigger",
                                ref=ref))
        if not maps and bias in ("long", "short") and entry is not None:
            maps.append(side_map(bias, entry, stop, target, sr, atr, plan.get("entry_type")))
    if not maps:
        return None
    vp = (sr or {}).get("volume_profile")
    # #652 the entry scenarios for the plan's side (both when it holds), with their past record by regime
    regime = ((plan.get("playbooks") or {}).get("regime") or {}).get("id")
    sides = [side] if side and entry is not None else \
        [s_ for s_ in ("long", "short") if plan.get(f"{s_}_trigger") is not None] or \
        ([bias] if bias in ("long", "short") else [])
    found = []
    for s_ in sides:
        trigger = plan.get(f"{s_}_trigger")
        if side and entry is not None:
            trigger = entry if plan.get("entry_type") == "stop" else None
        found += scenarios(sr, ref, atr, s_, trigger) if ref else []
    # the record is of daily bars (swing): a scalp · intraday · short-swing run gets none of it
    record = trade_map_record.lookup(regime, found, horizon=plan.get("horizon") or trade_map_record.HORIZON)
    return {"maps": maps, "reference_price": _round(ref) if ref else None, "atr": _round(atr),
            "volume_profile": bool(vp), "levels": bool((sr or {}).get("levels")),
            "scenarios": found, "regime": regime, "cautions": cautions(sr, sides), **record}


def cautions(sr: dict | None, sides: list[str]) -> list[dict]:
    """#695 what speaks against the map: the weekly Minsky-style stage when it says caution (mania, the first
    drop, the bull trap), and a head and shoulders against the side (a top against a long, a bottom against a
    short) that is forming or broke. Each with its own sentence and measured record - readings, not orders."""
    out = []
    b = (sr or {}).get("bubble")
    if b and b.get("caution"):
        out.append({"kind": "bubble", "stage": b["stage"], "label": b["label"], "text": b["explain"],
                    "reference_only": b.get("reference_only", True)})
    for p in (sr or {}).get("hs") or []:
        against = ("long" if p["kind"] == "head_shoulders" else "short") in sides
        if against and p["state"] in ("forming", "broken", "retest"):
            out.append({"kind": p["kind"], "state": p["state"], "label": p["label"], "text": p["explain"],
                        "reference_only": p.get("reference_only", True)})
    return out
