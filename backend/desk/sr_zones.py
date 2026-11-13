"""Support and resistance as zones with a record (#321).

desk.sr finds where levels sit; this module says how wide each one is and what the price did
there before:

- ``zone``: the members' prices plus each swing bar's body edge (a swing low's wick and its
  body both belong to the reaction), at least ZONE_MIN_ATR wide.
- ``touches`` / ``held`` / ``hold_rate``: past visits to the zone that were resolved, and how
  many turned back by REACT_ATR x ATR within REACT_BARS bars before closing through the far
  edge. A visit from above tests it as support, from below as resistance.
- ``flipped``: the zone held from one side after it had held from the other (an old
  resistance that became support): the role change the chart shows as "저항→지지 전환".
- ``events``: sweep_reclaim - a bar in the last SWEEP_BARS that traded through a zone's far
  edge and closed back inside or beyond it (the stop hunt below a support, or above a resistance).
- ``buy_zone`` on a support: the zone, a stop below its low (wick included) by STOP_ATR x ATR,
  the first resistance above as the target, and whether the reward covers the risk and the
  record is long enough to lean on.

Only bars up to the last one are read: a visit whose REACT_BARS are not over yet is not counted.
"""

from __future__ import annotations

ZONE_MIN_ATR = 0.2      # a zone is at least this wide
ZONE_MAX_ATR = 0.75     # ...and reaches at most this far either side of its level (a chain of merged
                        # candidates must not make one zone of a whole leg)
BODY_ATR = 0.5          # a swing bar's body edge joins the zone only this close to its wick
SWEEP_LOOK = 5          # a sweep takes out the lows (highs) of this many bars before it
REACT_BARS = 10         # a visit is judged within this many bars
REACT_ATR = 1.0         # ...it held if the price went this far back out before closing through
THROUGH_ATR = 0.25      # a close this far past the far edge broke it
SWEEP_BARS = 3          # sweep_reclaim events among the last bars
STOP_ATR = 0.25         # a buy zone's stop under the zone's low
MIN_RR = 1.0
MIN_TOUCHES = 3


def _r(x: float) -> float:
    return float(round(x)) if abs(x) >= 1000 else round(x, 2)


def zone_of(members: list[tuple[float, float | None]], atr: float, level: float | None = None) -> tuple[float, float]:
    """``members``: (price, body edge or None). Their span - a body edge only when near its wick -
    within ZONE_MAX_ATR of ``level``, at least ZONE_MIN_ATR wide."""
    xs = [p for p, _ in members] + [b for p, b in members if b is not None and (not atr or abs(b - p) <= BODY_ATR * atr)]
    lo, hi = min(xs), max(xs)
    if atr and level is not None:
        lo, hi = max(lo, level - ZONE_MAX_ATR * atr), min(hi, level + ZONE_MAX_ATR * atr)
    if atr and hi - lo < ZONE_MIN_ATR * atr:
        mid = (lo + hi) / 2
        lo, hi = mid - ZONE_MIN_ATR * atr / 2, mid + ZONE_MIN_ATR * atr / 2
    return lo, hi


def reactions(bars: list[dict], lo: float, hi: float, atr: float) -> dict:
    """Resolved visits to [lo, hi] from above (support) and below (resistance), and which held."""
    out = {"support": [0, 0], "resistance": [0, 0]}
    order: list[tuple[str, bool]] = []
    if not atr or len(bars) < 2:
        return {**out, "order": order}
    i = 1
    while i < len(bars):
        prev, bar = bars[i - 1], bars[i]
        side = None
        if prev["close"] > hi and bar["low"] <= hi:
            side = "support"
        elif prev["close"] < lo and bar["high"] >= lo:
            side = "resistance"
        if side is None:
            i += 1
            continue
        held = None
        for j in range(i, min(len(bars), i + REACT_BARS)):
            b = bars[j]
            if side == "support":
                if b["close"] < lo - THROUGH_ATR * atr:
                    held = False
                    break
                if b["high"] >= hi + REACT_ATR * atr and j > i:
                    held = True
                    break
            else:
                if b["close"] > hi + THROUGH_ATR * atr:
                    held = False
                    break
                if b["low"] <= lo - REACT_ATR * atr and j > i:
                    held = True
                    break
        if held is None and i + REACT_BARS > len(bars):
            break                                   # still playing out: not judged yet
        held = bool(held)
        out[side][0] += 1
        out[side][1] += int(held)
        order.append((side, held))
        i = max(i + 1, j + 1)
    return {**out, "order": order}


def flipped(kind: str, held_as: set[str]) -> str | None:
    """Polarity: a zone that ever held as resistance and has the price above it now is a support
    by role change (and the other way round) - however often it held as support since."""
    if kind == "support" and "resistance" in held_as:
        return "resistance_to_support"
    if kind == "resistance" and "support" in held_as:
        return "support_to_resistance"
    return None


def sweeps(bars: list[dict], levels: list[dict]) -> list[dict]:
    """sweep_reclaim among the last SWEEP_BARS bars: a bar that takes out the lows (highs) of the
    SWEEP_LOOK bars before it - where the stops sat - reaching into or through a support (resistance)
    zone, and closes back above the zone's high (below its low): the stops were run and the zone
    bought (sold). ``through`` says whether it also went past the zone's far edge. The zone is the one
    without the last sessions' own lows and highs (``_core``): the swept bar must not shape the zone."""
    out = []
    for i in range(max(SWEEP_LOOK, len(bars) - SWEEP_BARS), len(bars)):
        bar, before = bars[i], bars[i - SWEEP_LOOK:i]
        for lv in levels:
            lo, hi = lv.get("_core") or lv["zone"]
            if (lv["kind"] == "support" and bar["low"] <= hi and bar["low"] < min(b["low"] for b in before)
                    and bar["close"] > hi):
                out.append({"kind": "sweep_reclaim", "at": bar["at"], "level": lv["price"], "side": "support",
                            "low": _r(bar["low"]), "close": _r(bar["close"]), "through": bar["low"] < lo})
            elif (lv["kind"] == "resistance" and bar["high"] >= lo and bar["high"] > max(b["high"] for b in before)
                    and bar["close"] < lo):
                out.append({"kind": "sweep_reclaim", "at": bar["at"], "level": lv["price"], "side": "resistance",
                            "high": _r(bar["high"]), "close": _r(bar["close"]), "through": bar["high"] > hi})
    return out


def buy_zone(lv: dict, above: list[dict], atr: float) -> dict:
    lo, hi = lv["zone"]
    stop = lo - STOP_ATR * atr
    entry = (lo + hi) / 2
    target = min((x["zone"][0] for x in above if x["zone"][0] > hi), default=None)
    rr = (target - entry) / (entry - stop) if target and entry > stop else None
    why = []
    if lv.get("flipped") == "resistance_to_support":
        why.append("저항→지지 전환")
    if lv.get("touches"):
        why.append(f"과거 {lv['touches']}번 중 {lv['held']}번 반등")
    ok = rr is not None and rr >= MIN_RR and (lv.get("touches") or 0) >= MIN_TOUCHES
    note = None if ok else ("근거 부족: 반응 기록이 적음" if (lv.get("touches") or 0) < MIN_TOUCHES
                            else "근거 부족: 위 저항까지 손익비 1 미만")
    return {"lo": _r(lo), "hi": _r(hi), "stop": _r(stop), "target": _r(target) if target else None,
            "rr": round(rr, 2) if rr is not None else None, "why": why, "hold_rate": lv.get("hold_rate"),
            "n": lv.get("touches") or 0, "ok": ok, "note": note}


def enrich(levels: list[dict], bars: list[dict], atr: float, weekly: list[dict] | None = None,
           weekly_atr: float | None = None) -> list[dict]:
    """Adds touches / held / hold_rate / flipped to each level (with ``zone`` already set) - the visits counted on the primary bars and, before them, on the
    secondary ones (a level from two years ago was tested on weekly bars). Returns the
    sweep_reclaim events."""
    start = bars[0]["at"] if bars else None
    older = [b for b in weekly or [] if start is None or b["at"] < start]
    for lv in levels:
        lo, hi = lv["zone"]
        recs = [reactions(bars, lo, hi, atr)]
        if older and weekly_atr:
            recs.append(reactions(older, lo, hi, weekly_atr))
        side = lv["kind"]
        n, held = sum(r[side][0] for r in recs), sum(r[side][1] for r in recs)
        lv["touches"], lv["held"] = n, held
        lv["hold_rate"] = round(held / n, 2) if n else None
        lv["flipped"] = flipped(side, {s for r in recs for s, ok in r["order"] if ok})
        lv["zone"] = [_r(lo), _r(hi)]
    return sweeps(bars, levels)


def add_buy_zones(levels: list[dict], atr: float) -> None:
    """A buy_zone on each support, its target among the same (picked) resistances."""
    for lv in levels:
        if lv["kind"] == "support":
            lv["buy_zone"] = buy_zone(lv, [x for x in levels if x["kind"] == "resistance"], atr)
