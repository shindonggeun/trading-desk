"""One card for a run (#221): the verdict, the split buy, the one price to buy all at once, the stop,
the targets - so the page can lead with five numbers instead of everything the desk computed.

Every number comes from the desk (the plan's levels, the playbook's zone · ladder · stop · targets,
desk.calibration's record, desk.value_swing's risk budget); nothing here asks a model. ``build``
is pure: a plan (or None, the live playbook page) and the playbooks in, a dict out.

The single entry (buying it all at one price): of the zone's member levels, the price where the
most evidence overlaps (member weights summed within OVERLAP_ATR), as long as its reward-to-risk to
the first target is at least MIN_RR; the next best otherwise; none when no price clears it.
"""

from __future__ import annotations

VERSION = 1
OVERLAP_ATR = 0.25       # members this close (in the frame's ATR) count as the same price
MIN_RR = 1.5             # a single entry below this reward-to-risk to T1 is not offered
# #231: a ladder whose T1 pays less than this (from the average entry) is not a buy - the card says
# stand aside (and why) and keeps the numbers below for reference.
MIN_T1_RR = 1.0
# Swing basket 2019-, costs in, the same plans (desk.backtest.entry_style), train / held-out 2024-05-17:
# ladder fill 62%, T1 first 54%, -0.126R / -0.107R; all at the single price fill 50%, T1 first 23%,
# -0.252R / -0.250R. The deep, tight-stop single price fills less and loses more R per miss, so the
# card shows it (asked for) with this warning, and leads with the ladder.
SINGLE_NOTE = ("백테스트(스윙 6종목 2019~, 비용 포함): 한 번에 사면 체결 50% · T1 먼저 23% · 기대 -0.25R, "
               "분할 매수는 체결 62% · T1 먼저 54% · 기대 -0.11R — 분할이 낫다")
LONG_BIASES = ("long",)
SHORT_BIASES = ("short",)

VERDICT_TEXT = {
    "buy_now": "지금 구역 안 — 분할 매수 시작 가능",
    "wait_for_zone": "구역까지 기다렸다가 산다",
    "short_now": "지금 숏 구역 — 분할 매도(숏) 시작 가능",
    "wait_short": "반등이 구역에 닿을 때까지 기다렸다가 숏",
    "stand_aside": "관망 — 지금은 타점이 없다",
    "low_rr": "관망 — 손익비가 안 맞는다",
    # #231: the plan said Hold; the desk's better side is shown as "if you go in", not as a buy
    "watch_long": "관망 — 들어간다면 아래처럼(롱)",
    "watch_short": "관망 — 들어간다면 아래처럼(숏)",
}


def _round(price: float | None) -> float | None:
    if price is None:
        return None
    return float(round(price)) if price >= 1000 else round(price, 2)


def _pick_side(plan: dict | None, pb: dict | None) -> str | None:
    """The plan's direction when it has one, else the playbook side with a setup and the higher score."""
    if not pb:
        return None
    live = {s: pb.get(s) or {} for s in ("long", "short")}
    usable = {s: v for s, v in live.items() if v.get("setup") not in (None, "none") and v.get("entries")}
    bias = (plan or {}).get("bias")
    if bias in LONG_BIASES and "long" in usable:
        return "long"
    if bias in SHORT_BIASES and "short" in usable:
        return "short"
    if bias in LONG_BIASES + SHORT_BIASES:
        return None                     # the plan picked a side the desk has no setup for
    return max(usable, key=lambda s: usable[s].get("score") or 0) if usable else None


def _condition(entry: dict, up: bool) -> str:
    if entry.get("held"):
        return {"event": "경제 발표 지나간 뒤", "risk": "투매 끝물 신호 뒤"}.get(entry["held"], "보류")
    if entry.get("kind") == "stop":
        return "되찾기 확인 뒤(스톱 진입)" if up else "이탈 확인 뒤(스톱 진입)"
    return "지정가"


def single_entry(side: dict, up: bool, atr: float) -> dict | None:
    """The one price to buy (sell) it all at: the most evidence overlapping, MIN_RR to T1 or better."""
    zone, stop, targets = side.get("zone") or {}, side.get("stop"), side.get("targets") or []
    members = [m for m in zone.get("members") or [] if m.get("price") is not None]
    if not members or stop is None or not targets:
        return None
    t1 = targets[0]["price"]
    tol = OVERLAP_ATR * (atr or 0)
    scored, seen = [], set()
    for m in members:
        near = [x for x in members if abs(x["price"] - m["price"]) <= tol]
        key = tuple(sorted(id(x) for x in near))
        if key in seen:                  # the same cluster seen from another member
            continue
        seen.add(key)
        weight = sum(x.get("weight") or 1.0 for x in near)
        # the cluster's weighted centre: not one line of it, the middle of what overlaps
        price = sum(x["price"] * (x.get("weight") or 1.0) for x in near) / weight
        risk = (price - stop) if up else (stop - price)
        reward = (t1 - price) if up else (price - t1)
        if risk <= 0:
            continue
        scored.append((weight, reward / risk, price, near))
    # most evidence first; on a tie, the better reward-to-risk
    for weight, rr, price, near in sorted(scored, key=lambda x: (-x[0], -x[1])):
        if rr >= MIN_RR:
            why = list(dict.fromkeys(x.get("basis") or x.get("source") for x in near))
            return {"price": _round(price), "rr": round(rr, 2), "weight": round(weight, 2), "why": why}
    return None


def build(plan: dict | None, pb: dict | None) -> dict | None:
    """The card. None without playbooks."""
    if not pb:
        return None
    ref = (pb.get("frame") or {}).get("reference_price")
    atr = (pb.get("frame") or {}).get("atr_zone") or (pb.get("frame") or {}).get("atr") or 0.0
    name = _pick_side(plan, pb)
    out: dict = {"version": VERSION, "price": ref, "valid_until": (plan or {}).get("valid_until"),
                 "rating": (plan or {}).get("rating")}
    if name is None:
        why = None
        if (plan or {}).get("bias") in LONG_BIASES + SHORT_BIASES:
            why = "계획 방향의 데스크 타점이 없다(구역 · 손절을 세울 근거 부족)"
        else:
            reasons = [(pb.get(s) or {}).get("reason") for s in ("long", "short")]
            why = next((r for r in reasons if r), None)
        return {**out, "verdict": "stand_aside", "text": VERDICT_TEXT["stand_aside"], "why": why, "side": None}
    side = pb[name]
    up = name == "long"
    if side.get("state") in ("invalid", "closed"):     # the live page: the price broke through the stop side
        return {**out, "verdict": "stand_aside", "text": VERDICT_TEXT["stand_aside"], "side": name,
                "why": "가격이 구역을 뚫고 나가 이 타점은 무효"}
    zone = side.get("zone") or {}
    lo, hi = zone.get("low"), zone.get("high")
    inside = ref is not None and lo is not None and hi is not None and (ref <= hi if up else ref >= lo)
    verdict = ("buy_now" if inside else "wait_for_zone") if up else ("short_now" if inside else "wait_short")
    text = VERDICT_TEXT[verdict]
    why = None
    ladder = [{"step": e["step"], "price": e["price"], "share": e.get("share"), "kind": e.get("kind"),
               "when": _condition(e, up)} for e in side.get("entries") or []]
    stop = side.get("stop")
    avg = side.get("avg_entry")
    targets = [{"role": t["role"], "price": t["price"], "rr": t.get("rr")} for t in side.get("targets") or []]
    t1_rr = targets[0]["rr"] if targets else None
    if plan is not None and plan.get("bias") not in LONG_BIASES + SHORT_BIASES:
        verdict, text = "watch", VERDICT_TEXT["watch_long" if up else "watch_short"]
        why = "계획은 관망 — 데스크 타점 중 점수가 높은 쪽을 조건부로 보여 준다"
    if t1_rr is not None and t1_rr < MIN_T1_RR:
        verdict, text = "stand_aside", VERDICT_TEXT["low_rr"]
        why = f"1차 목표까지 손익비 {t1_rr} — 손절 폭에 비해 먹을 폭이 없다. 아래 숫자는 참고만"
    one = single_entry(side, up, atr)
    first = one["price"] if one else (ladder[0]["price"] if ladder else None)
    cal = side.get("calibration") or {}
    budget = side.get("risk_budget") or {}
    return {
        **out,
        "side": name, "setup": side.get("setup"), "verdict": verdict, "text": text, "why": why,
        "zone": {"low": lo, "high": hi},
        "ladder": ladder, "avg_entry": avg,
        "single_entry": ({**one, "p_t1": cal.get("p_t1"), "thin": cal.get("thin", True), "note": SINGLE_NOTE}
                         if one else None),
        "stop": stop,
        "stop_pct": round((stop / avg - 1) * 100, 2) if stop and avg else None,
        "targets": targets,
        "distance_pct": round((first / ref - 1) * 100, 2) if first and ref else None,
        "record": {"p_t1": cal.get("p_t1"), "exp_r": cal.get("exp_r"), "n": cal.get("n"),
                   "thin": cal.get("thin", True), "negative": cal.get("negative", False)} if cal else None,
        "risk_pct": budget.get("account_pct"), "position_pct": budget.get("position_pct"),
        "conviction": (side.get("conviction") or {}).get("active", False),
        "time_stop": side.get("time_stop"),
        # why the ladder is shorter or waits: the market's selling rule (#146) · an economic release (#148)
        "notes": [n for n in ((side.get("gate") or {}).get("text"),
                              f"{pb['event_hold']['label']} 발표 전후 — 1차만" if pb.get("event_hold") else None) if n],
    }


def as_single(side: dict, up: bool, atr: float) -> dict | None:
    """The same side bought (sold) all at the single price - the backtest's other arm (#221)."""
    one = single_entry(side, up, atr)
    if not one:
        return None
    price = one["price"]
    return {**side, "entries": [{"step": 1, "price": price, "share": 1.0, "kind": "limit"}],
            "avg_entry": price, "risk": _round(abs(price - side["stop"]))}
