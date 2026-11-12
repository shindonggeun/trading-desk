"""Pull the actionable plan (rating, levels) out of a finished run's state.

TradingAgents renders its structured outputs back to markdown with fixed English
labels ("**Entry Price**: 189.5"), whatever the output language, so the labels are
a stable contract. Every level is optional: the model omits what it cannot state.
"""

from __future__ import annotations

import re
from typing import Any

from . import verdict

_RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")

# How the desk reads a rating. "short" means "short or reduce": whether a real short
# is possible depends on the account and the instrument, which the rating cannot know.
BIAS = {"Buy": "long", "Overweight": "long", "Hold": "flat", "Underweight": "short", "Sell": "short"}

_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _field(text: str, label: str) -> str | None:
    match = re.search(rf"\*\*{re.escape(label)}\*\*\s*:\s*(.+)", text or "")
    if not match:
        return None
    value = match.group(1).strip()
    return None if not value or value.lower().startswith("not provided") else value


def _price(text: str, label: str) -> float | None:
    raw = _field(text, label)
    if raw is None:
        return None
    match = _NUMBER.search(raw)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _rating(state: dict) -> str | None:
    candidates = [state.get("final_rating"), _field(state.get("final_trade_decision", ""), "Rating")]
    for candidate in candidates:
        if not candidate:
            continue
        for rating in _RATINGS:
            if str(candidate).strip().lower().startswith(rating.lower()):
                return rating
    return None


def reward_risk(entry: float | None, stop: float | None, target: float | None) -> float | None:
    """Reward-to-risk of the levels, or None when they do not describe a trade."""
    if entry is None or stop is None or target is None:
        return None
    risk, reward = abs(entry - stop), abs(target - entry)
    if risk == 0:
        return None
    # Levels on the wrong side of entry (stop above a long's target...) are not a trade.
    if (target - entry) * (entry - stop) <= 0:
        return None
    return round(reward / risk, 2)


# The portfolio manager's schema has a Price Target but no entry or stop, so when the risk
# debate revises the trader's levels the final numbers live only in the Executive Summary
# prose ("진입가 $85.75, 손절가 $98.26" / "Entry Price: 85.75 / Stop Loss: 98.26").
_SUMMARY_KEYWORDS = {
    "entry_price": r"entry(?:\s+price)?|진입가|진입",
    "stop_loss": r"stop(?:[\s-]*loss)?|손절가|손절",
}
# The line profile.py asks the PM for: "Entry Price: 85.75 / Stop Loss: 98.26 / Price Target: 60.73".
_LABELED = r"(?:\*\*)?{label}(?:\*\*)?\s*:\s*(?:\*\*)?\s*[$₩]?\s*({num})"
# Prose fallback: keyword, an optional noun/particle, then the number right away. "손절 시 손실 1.46%",
# "목표 비중 50~75%" never match (other words sit in between); the lookahead drops numbers that are
# counts or units ("2.5 ATR", "2분할", "1차", "10만원", "50-day", "2:1", "2.5R") or a level being
# replaced ("110.77을 98.26으로" — the first number is the old one).
_PROSE = (r"(?:{kw})\s*(?:\*\*)?\s*(?:라인|선|기준|가격)?\s*(?:[:=]|은|는|이|가|at|of|around)?\s*"
          r"(?:약\s*)?[$₩]?\s*({num})"
          r"(?!\d|[,.]\d|-\w|\s*(?:%|주|배|[xX]\b|일|개|분할|차|회|만|ATR|R\b|day|DMA|:|을|를|에서|→|->))")
# A prose number this far from every known level is a misread, not a level.
_PLAUSIBLE = 0.5


def _number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _labeled_price(text: str, label: str) -> float | None:
    """The last ``Label: number`` in the text — the PM restates the final levels at the end."""
    hits = re.findall(_LABELED.format(label=re.escape(label), num=_NUMBER.pattern), text or "", re.IGNORECASE)
    return _number(hits[-1]) if hits else None


def _prose_price(summary: str | None, key: str, references: list[float]) -> float | None:
    if not summary or not references:
        return None   # nothing to sanity-check a prose number against
    summary = _ENTRY_LINE.sub("", summary)   # 'Entry: stop on a break of 186.5' names the entry, not a stop
    pattern = _PROSE.format(kw=_SUMMARY_KEYWORDS[key], num=_NUMBER.pattern)
    for match in re.finditer(pattern, summary, re.IGNORECASE):
        value = _number(match.group(1))
        if value is not None and any(abs(value - ref) <= _PLAUSIBLE * abs(ref) for ref in references):
            return value
    return None


# A leveraged product's plan also carries levels on its underlying (desk.underlying):
# "Underlying Entry: 251 / Underlying Stop: 243 / Underlying Target: 270". They are read first and
# cut out, so "Underlying Entry Price: 251" never reads as the product's Entry Price.
# "Underlying", optionally the underlying's ticker ("Underlying SOXX Entry Price").
_UNDER = r"Underlying\s+(?:[A-Z0-9.^=-]{1,12}\s+)?"
_UNDERLYING = {
    "entry_price": _UNDER + r"Entry(?:\s+Price)?",
    "stop_loss": _UNDER + r"Stop(?:[\s-]*Loss)?",
    "price_target": _UNDER + r"(?:Price\s+)?Target(?:\s+Price)?",
}


def _split_underlying(text: str) -> tuple[dict[str, float], str]:
    found: dict[str, float] = {}
    for key, label in _UNDERLYING.items():
        pattern = _LABELED.format(label=label, num=_NUMBER.pattern)
        hits = re.findall(pattern, text, re.IGNORECASE)
        if hits and (value := _number(hits[-1])) is not None:
            found[key] = value
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return found, text


def parse_plan(state: dict[str, Any]) -> dict[str, Any]:
    trader = state.get("trader_investment_plan") or ""
    original = state.get("final_trade_decision") or ""
    underlying, decision = _split_underlying(original)   # the stripped copy is only for parsing
    summary = _field(decision, "Executive Summary")
    rating = _rating(state)
    levels: dict[str, float | None] = {}
    origin: dict[str, str] = {}
    trader_levels: dict[str, float] = {}
    target = _price(decision, "Price Target")
    drafts = {key: _price(trader, label) for key, label in (("entry_price", "Entry Price"), ("stop_loss", "Stop Loss"))}
    for key, label in (("entry_price", "Entry Price"), ("stop_loss", "Stop Loss")):
        drafted = drafts[key]
        final = _price(decision, label)
        if final is None:
            final = _labeled_price(summary or "", label)
        if final is None:
            refs = [v for v in (drafted, target, *drafts.values()) if v]
            final = _prose_price(summary, key, refs)
        if final is not None:
            levels[key], origin[key] = final, "pm"
            if drafted is not None and drafted != final:
                trader_levels[key] = drafted   # the trader's draft the risk debate overruled
        elif drafted is not None:
            levels[key], origin[key] = drafted, "trader"
        else:
            levels[key] = None
    if target is not None:
        origin["price_target"] = "pm"
    entry, stop = levels["entry_price"], levels["stop_loss"]
    plan = {
        "rating": rating or "REVIEW",
        "bias": BIAS.get(rating, "review"),
        "trader_action": _field(trader, "Action"),
        "entry_price": entry,
        "stop_loss": stop,
        "price_target": target,
        "reward_risk": reward_risk(entry, stop, target),
        "time_horizon": _field(decision, "Time Horizon"),
        "position_sizing": _field(trader, "Position Sizing"),
        "executive_summary": _field(original, "Executive Summary"),   # as the PM wrote it
        "levels_origin": origin,
    }
    if trader_levels:
        plan["trader_levels"] = trader_levels
    if underlying:
        plan["underlying_levels"] = underlying
    plan.update(_entry_terms(decision))
    plan.update(verdict.parse(decision))   # #64: win probability, checklist, triggers
    return plan


_ACTION_SIDE = {"buy": "long", "overweight": "long", "sell": "short", "underweight": "short"}
_ACTION_KO = {"long": "매수안", "short": "매도안"}


def levels_side(entry: float | None, stop: float | None, target: float | None) -> str | None:
    """long when stop < entry < target, short when target < entry < stop; without a target the
    stop alone decides; None when the levels contradict each other or are missing."""
    if entry is None or stop is None or stop == entry:
        return None
    side = "long" if stop < entry else "short"
    if target is not None and (target <= entry if side == "long" else target >= entry):
        return None
    return side


def sides(plan: dict | None) -> dict:
    """#311: which way the plan's entry / stop / target point, against its rating and triggers.

    ``levels_side`` long | short | None (contradictory levels), ``levels_note`` (where the numbers
    come from, e.g. the trader's sell plan), ``side_conflict`` (the rating's bias - Hold is flat -
    differs from the levels' side) and ``trigger_warnings`` (a trigger already met at the price the
    agents saw, or a short threshold above the long one). Pure: a stored plan is read again with it."""
    plan = plan or {}
    side = levels_side(plan.get("entry_price"), plan.get("stop_loss"), plan.get("price_target"))
    action = str(plan.get("trader_action") or "").strip().lower().split()[:1]
    acted = _ACTION_SIDE.get(action[0]) if action else None
    if side is None:
        note = ("진입 · 손절 · 목표가 서로 맞지 않음 — 롱 · 숏 어느 쪽으로도 읽히지 않음"
                if plan.get("entry_price") is not None and plan.get("stop_loss") is not None else None)
    elif acted == side:
        note = f"트레이더 {_ACTION_KO[side]}의 숫자({'롱' if side == 'long' else '숏'} 관점)"
    else:
        note = f"레벨 모양으로 {'롱' if side == 'long' else '숏'} 관점(손절이 진입 {'아래' if side == 'long' else '위'})"
    bias = plan.get("bias")
    conflict = bool(side and bias in ("long", "short", "flat") and bias != side)

    warnings: list[str] = []
    long_t, short_t = plan.get("long_trigger"), plan.get("short_trigger")
    price = (plan.get("live_price") or {}).get("price") or (plan.get("reference_price") or {}).get("price")
    if long_t is not None and short_t is not None and short_t > long_t:
        warnings.append(f"숏 · 축소 기준({short_t})이 롱 진입 기준({long_t})보다 위 — 두 트리거가 엇갈림")
    if price:
        if short_t is not None and price < short_t:
            warnings.append(f"숏 · 축소 조건({short_t} 이탈)은 분석 때 가격 {price} 에서 이미 충족")
        if long_t is not None and price > long_t:
            warnings.append(f"롱 진입 조건({long_t} 회복 · 돌파)은 분석 때 가격 {price} 에서 이미 충족")
    return {"levels_side": side, "levels_note": note, "side_conflict": conflict, "trigger_warnings": warnings}


def with_sides(plan: dict | None) -> dict | None:
    """The plan with ``sides`` added (None and non-dict plans pass through)."""
    return {**plan, **sides(plan)} if isinstance(plan, dict) else plan


_ENTRY_LINE = re.compile(r"\bEntry\s*\**\s*:\s*\**\s*(?:now|limit|stop)\b.*$", re.IGNORECASE | re.MULTILINE)
_ENTRY_TYPE = re.compile(r"\bEntry\s*\**\s*:\s*\**\s*(now|limit|stop)\b", re.IGNORECASE)
_INVALIDATION = re.compile(r"\bInvalidation\s*\**\s*:\s*\**\s*(.+?)\s*(?:/\s*\**\s*Exit by\b|$)",
                           re.IGNORECASE | re.MULTILINE)
_EXIT_BY = re.compile(r"\bExit by\s*\**\s*:\s*\**\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _entry_terms(decision: str) -> dict[str, str | None]:
    """The horizon mandate's closing line (desk.horizons): 'Entry: <now|limit|stop> /
    Invalidation: <text> / Exit by: <text>'. The last occurrence wins; missing parts are None."""
    def last(pattern: re.Pattern) -> str | None:
        found = pattern.findall(decision or "")
        if not found:
            return None
        return found[-1].strip(" *") or None
    entry_type = last(_ENTRY_TYPE)
    return {"entry_type": entry_type.lower() if entry_type else None,
            "invalidation": last(_INVALIDATION), "exit_by": last(_EXIT_BY)}
