"""The judgement layer (#64): a symmetric checklist and a high-probability filter.

Every run's deciding agents score the long and the short case on the same six factors,
estimate the chance the target comes before the stop, and name the levels that would turn
them long or short. The desk reads that back (``parse``) and decides an ``opinion``
(buy | sell | hold | review) with the filter (``apply``) - without touching the model's own
``rating``/``bias``, which levels, the trade review and TradingAgents' memory log rely on.
Keeping both is what lets the scoring (desk.outcome) show whether the filter helps.

TradingAgents' PM output has five fields; the new lines live inside its free text and are
read wherever they last appear, like the horizon line (desk.plan._entry_terms).
"""

from __future__ import annotations

import re

from . import horizons

FACTORS = ("trend", "location", "momentum", "volume", "catalyst", "regime")
MIN_PROBABILITY = 0.70      # the owner's target is a high hit rate: below this the desk says hold
MIN_AGREEING = 3            # factors that must point with the call

# The swing horizon's holding is in English for the agents already (desk.horizons).


def mandate(horizon: horizons.Horizon) -> str:
    """Instructions for every run, swing included. The investor mandate still decides how a
    bearish call is acted on (short/inverse or reduce)."""
    return (
        "Judgement checklist, every run and every rating: score the long case and the short case on this "
        "horizon with the same six factors - trend (structure, moving averages), location (the entry against "
        "the listed support/resistance), momentum (RSI/MACD, the last bars), volume (participation confirming "
        "the move), catalyst (news, earnings, flows) and regime (index and sector tape). Each factor is long, "
        "short or neutral; bullish and bearish views are judged by the same six, and within what the investor "
        "mandate allows it decides how a bearish call is acted on (short/inverse or reduce). Estimate the win "
        f"probability: the chance the price target is reached before the stop within {horizon.holding_en}, as a "
        "whole percent. High-probability filter: rate Buy/Overweight or Sell/Underweight only when the win "
        f"probability is at least {MIN_PROBABILITY:.0%}, reward:risk is at least {horizon.min_rr:g}, at least "
        f"{MIN_AGREEING} factors point with the call, and the entry can be reached soon (within about 2 ATR "
        "of the current price, or now). Otherwise rate Hold - still with levels and a conditional plan. Every "
        "call, Hold included, names a Long Trigger (the price whose reclaim or break would make you buy) and a "
        "Short Trigger (the price whose loss would make you short or reduce), or 'none'. Write two lines with "
        "English labels in any output language: at the end of the Investment Thesis 'Checklist: trend=<long|"
        "short|neutral>, location=<...>, momentum=<...>, volume=<...>, catalyst=<...>, regime=<...>'; and as "
        "the last line of the Executive Summary, after the final levels line (and after the Entry line when the "
        "horizon asks for one), 'Win Probability: <0-100>% / Long Trigger: <price|none> / Short Trigger: "
        "<price|none>'."
    )


_NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?"
_APPROX = r"(?:~|약|about|approx\.?|≈)?\s*"
_PROBABILITY = re.compile(rf"\bWin\s*Probability\s*\**\s*:\s*\**\s*{_APPROX}({_NUMBER})\s*(%?)", re.IGNORECASE)
_TRIGGER = r"\b{side}\s*Trigger\s*\**\s*:\s*\**\s*(?:[$₩]\s*)?(none|n/a|{num})"
_LONG = re.compile(_TRIGGER.format(side="Long", num=_NUMBER), re.IGNORECASE)
_SHORT = re.compile(_TRIGGER.format(side="Short", num=_NUMBER), re.IGNORECASE)
_CHECKLIST = re.compile(r"\bChecklist\s*\**\s*:\s*\**\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_FACTOR = re.compile(r"\b(" + "|".join(FACTORS) + r")\s*[=:]\s*\**\s*(long|short|neutral)\b", re.IGNORECASE)


def _num(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse(text: str) -> dict:
    """win_probability (0~1), checklist ({factor: long|short|neutral}), score ({long, short}),
    long_trigger, short_trigger - each None when the model left it out. Last occurrence wins."""
    text = text or ""
    probability = None
    found = _PROBABILITY.findall(text)
    if found:
        raw, percent = found[-1]
        value = _num(raw)
        if value is not None:
            # "72%" and "72" are percents, "0.72" a fraction; a bare "1" or "0" could be either
            if percent or value > 1:
                probability = value / 100
            elif "." in raw:
                probability = value
            probability = round(probability, 4) if probability is not None and 0 <= probability <= 1 else None

    def trigger(pattern: re.Pattern) -> float | None:
        hits = pattern.findall(text)
        return _num(hits[-1]) if hits and hits[-1].lower() not in ("none", "n/a") else None

    checklist = None
    lines = _CHECKLIST.findall(text)
    if lines:
        factors = {name.lower(): side.lower() for name, side in _FACTOR.findall(lines[-1])}
        checklist = {name: factors[name] for name in FACTORS if name in factors} or None
    score = ({"long": sum(v == "long" for v in checklist.values()),
              "short": sum(v == "short" for v in checklist.values())} if checklist else None)
    return {"win_probability": probability, "checklist": checklist, "score": score,
            "long_trigger": trigger(_LONG), "short_trigger": trigger(_SHORT)}


_OPINION = {"long": "buy", "short": "sell", "flat": "hold"}


def apply(plan: dict, horizon: horizons.Horizon) -> dict:
    """``opinion`` (buy | sell | hold | review) and ``filter`` {passed, reasons, thresholds}.
    A directional call that fails the filter becomes hold here; rating, bias and levels stay."""
    plan = dict(plan)
    thresholds = {"win_probability": MIN_PROBABILITY, "reward_risk": horizon.min_rr, "agreeing": MIN_AGREEING}
    bias = plan.get("bias")
    if bias not in ("long", "short"):
        plan["opinion"] = _OPINION.get(bias, "review")
        plan["filter"] = {"passed": None, "reasons": [], "thresholds": thresholds}
        return plan
    missing = [name for name in ("win_probability", "checklist") if plan.get(name) is None]
    if missing:
        # a line the desk could not read is not a deliberate hold: say so instead of demoting the call
        plan["opinion"] = "review"
        plan["filter"] = {"passed": None, "reasons": [f"no {' / '.join(missing)} in the decision"],
                          "thresholds": thresholds}
        return plan
    reasons = []
    probability = plan.get("win_probability")
    if probability is None or probability < MIN_PROBABILITY:
        reasons.append(f"win probability {probability if probability is not None else '—'} < {MIN_PROBABILITY:.0%}")
    rr = plan.get("reward_risk")
    if rr is None or rr < horizon.min_rr:
        reasons.append(f"reward:risk {rr if rr is not None else '—'} < {horizon.min_rr:g}")
    agreeing = (plan.get("score") or {}).get(bias)
    if agreeing is None or agreeing < MIN_AGREEING:
        reasons.append(f"{agreeing if agreeing is not None else 0} factors agree < {MIN_AGREEING}")
    reach = plan.get("entry_vs_live") or {}
    if reach.get("far"):
        reasons.append("entry too far from the current price to fill in time")
    passed = not reasons
    plan["opinion"] = _OPINION[bias] if passed else "hold"
    plan["filter"] = {"passed": passed, "reasons": reasons, "thresholds": thresholds}
    return plan


# Two lines a scripted run appends to its decision (desk.runner._fake_decision).
FAKE_THESIS_LINE = ("Checklist: trend=long, location=long, momentum=long, volume=neutral, catalyst=long, "
                    "regime=short")
FAKE_SUMMARY_LINE = "Win Probability: 72% / Long Trigger: 185 / Short Trigger: 170"
