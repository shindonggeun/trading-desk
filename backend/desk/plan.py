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


def parse_plan(state: dict[str, Any]) -> dict[str, Any]:
    trader = state.get("trader_investment_plan") or ""
    decision = state.get("final_trade_decision") or ""
    rating = _rating(state)
    entry = _price(trader, "Entry Price")
    stop = _price(trader, "Stop Loss")
    target = _price(decision, "Price Target")
    return {
        "rating": rating or "REVIEW",
        "bias": BIAS.get(rating, "review"),
        "trader_action": _field(trader, "Action"),
        "entry_price": entry,
        "stop_loss": stop,
        "price_target": target,
        "reward_risk": reward_risk(entry, stop, target),
        "time_horizon": _field(decision, "Time Horizon"),
        "position_sizing": _field(trader, "Position Sizing"),
        "executive_summary": _field(decision, "Executive Summary"),
    }
