"""Pull the actionable plan (rating, levels) out of a finished run's state.

TradingAgents renders its structured outputs back to markdown with fixed English
labels ("**Entry Price**: 189.5"), whatever the output language, so the labels are
a stable contract. Every level is optional: the model omits what it cannot state.
"""

from __future__ import annotations

import re
from typing import Any

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
    return plan
