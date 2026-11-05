"""The desk's closing summary (issue #94): one LLM call that reads the whole debate and answers in
one card — who voted long, short or hold and why, the long case, the short case, how the team
settled, and a one-line conclusion.

The numbers stay the plan's: the summariser is shown the final entry, stop and target but is
asked for no levels, so the card can never disagree with the plan it sits on. Three votes are
not left to the model at all: the research manager, the trader and the portfolio manager state
their call with a fixed label, so those are read from their text.

Like the leverage analyst (#82) the summariser is injected into RunManager: the default calls the
TradingAgents provider and model, a scripted one answers for DESK_FAKE_RUNS and the tests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from .plan import BIAS

CONSENSUS_TIMEOUT_SEC = 120.0
SPEECH_CHARS = 1500       # the tail of each speaker's text the prompt carries
MAX_POINTS = 5            # bullets per side
POINT_CHARS = 200
REASON_CHARS = 160
TEXT_CHARS = 600

STANCES = ("long", "short", "hold")

# (agent id, state key, debate history key or None) in pipeline order
SPEAKERS: list[tuple[str, str, str | None]] = [
    ("market_analyst", "market_report", None),
    ("sentiment_analyst", "sentiment_report", None),
    ("news_analyst", "news_report", None),
    ("fundamentals_analyst", "fundamentals_report", None),
    ("bull_researcher", "investment_debate_state", "bull_history"),
    ("bear_researcher", "investment_debate_state", "bear_history"),
    ("research_manager", "investment_plan", None),
    ("trader", "trader_investment_plan", None),
    ("aggressive_analyst", "risk_debate_state", "aggressive_history"),
    ("conservative_analyst", "risk_debate_state", "conservative_history"),
    ("neutral_analyst", "risk_debate_state", "neutral_history"),
    ("portfolio_manager", "final_trade_decision", None),
]
_IDS = {agent for agent, _, _ in SPEAKERS}

_RATING_WORD = r"(Buy|Overweight|Hold|Underweight|Sell)"
_FIXED = {
    "research_manager": re.compile(r"\*\*Recommendation\*\*\s*:\s*\**\s*" + _RATING_WORD, re.IGNORECASE),
    "trader": re.compile(r"FINAL TRANSACTION PROPOSAL\s*:\s*\**\s*(BUY|HOLD|SELL)", re.IGNORECASE),
    "portfolio_manager": re.compile(r"\*\*Rating\*\*\s*:\s*\**\s*" + _RATING_WORD, re.IGNORECASE),
}


def stance_of(rating: str | None) -> str | None:
    """Buy/Overweight → long, Underweight/Sell → short, Hold → hold."""
    bias = BIAS.get((rating or "").capitalize())
    return {"long": "long", "short": "short", "flat": "hold"}.get(bias or "")


@dataclass
class ConsensusContext:
    ticker: str                 # what the owner typed (SOXL)
    subject: str                # what the team debated (SOXX for SOXL, else the ticker)
    final_state: dict
    plan: dict                  # the finished plan (rating, opinion, levels)
    language: str | None = None


def speeches(final_state: dict) -> dict[str, str]:
    """Each speaker's text: a report, or that side's whole debate history."""
    out = {}
    for agent, key, sub in SPEAKERS:
        value = final_state.get(key)
        if sub is not None:
            value = value.get(sub) if isinstance(value, dict) else None
        if isinstance(value, str) and value.strip():
            out[agent] = value.strip()
    return out


def fixed_votes(final_state: dict) -> dict[str, str]:
    """The stances stated with a fixed label: research manager, trader, portfolio manager."""
    said = speeches(final_state)
    out = {}
    for agent, pattern in _FIXED.items():
        hits = pattern.findall(said.get(agent, ""))
        stance = stance_of(hits[-1]) if hits else None
        if stance:
            out[agent] = stance
    return out


def _levels_line(plan: dict) -> str:
    parts = [f"{label} {plan[key]}" for key, label in (
        ("entry_price", "entry"), ("stop_loss", "stop"), ("price_target", "target"),
        ("long_trigger", "long trigger"), ("short_trigger", "short trigger")) if plan.get(key) is not None]
    return ", ".join(parts) or "none given"


def build_prompt(ctx: ConsensusContext) -> str:
    said = speeches(ctx.final_state)
    plan = ctx.plan
    on = (f"The team analysed {ctx.subject}; the final call below is on {ctx.ticker}, a leveraged/inverse "
          f"product of it. Votes are about {ctx.subject}." if ctx.subject != ctx.ticker else
          f"The team analysed {ctx.ticker}.")
    lines = [
        ("You are the desk's summariser. A multi-agent trading team has finished its analysis and debate. "
        "Summarise it for a trader who will read nothing else."),
        on,
        "",
        "## Final decision (already fixed; do not change it, do not add price levels)",
        (f"- Rating {plan.get('rating')}, opinion {plan.get('opinion') or 'n/a'}, "
        f"win probability {plan.get('win_probability') if plan.get('win_probability') is not None else 'n/a'}%"),
        f"- Levels: {_levels_line(plan)}",
        "",
        "## What each agent said (latest part)",
    ]
    for agent, text in said.items():
        lines += [f"### {agent}", text[-SPEECH_CHARS:], ""]
    lines += [
        "## Task",
        ("- For every agent above, classify where its argument leaned on the next move: long, short or hold "
        "(no edge / wait). A researcher arguing one side still counts for that side."),
        ("- long_case / short_case: the strongest concrete points for each side (price structure, catalysts, "
        f"numbers), at most {MAX_POINTS} each, one sentence each."),
        "- debate: 2-3 sentences on how the trader and the risk team argued and what they settled on.",
        "- conclusion: one sentence — the final call and how to act on it, consistent with the final decision.",
        f"- Write the text in {ctx.language or 'English'}. Keep the JSON keys and stance values in English.",
        "",
        "Answer with JSON only, no prose around it:",
        ('{"votes": [{"agent": "<agent id>", "stance": "long|short|hold", "reason": "<one short sentence>"}], '
        '"long_case": ["..."], "short_case": ["..."], "debate": "...", "conclusion": "..."}'),
    ]
    return "\n".join(lines)


def _clip(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _points(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clip(p, POINT_CHARS) for p in value if str(p or "").strip()][:MAX_POINTS]


def parse(text: str, final_state: dict, subject: str) -> dict:
    """The summariser's JSON, checked: unknown agents and stances dropped, text clipped, the fixed
    votes put over the model's. Raises ValueError when there is no JSON object to read."""
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the summary")
    data = json.loads(text[start:end + 1])   # "{...}" decodes to a dict or raises (a ValueError)
    spoke = speeches(final_state)
    by_agent: dict[str, dict] = {}
    for vote in data.get("votes") or []:
        if not isinstance(vote, dict):
            continue
        agent, stance = vote.get("agent"), str(vote.get("stance") or "").lower()
        if agent in _IDS and agent in spoke and stance in STANCES and agent not in by_agent:
            by_agent[agent] = {"agent": agent, "stance": stance, "reason": _clip(vote.get("reason"), REASON_CHARS),
                               "fixed": False}
    for agent, stance in fixed_votes(final_state).items():
        reason = by_agent.get(agent, {}).get("reason", "")
        by_agent[agent] = {"agent": agent, "stance": stance, "reason": reason, "fixed": True}
    votes = [by_agent[a] for a, _, _ in SPEAKERS if a in by_agent]
    tally = {s: sum(1 for v in votes if v["stance"] == s) for s in STANCES}
    top = max(tally.values()) if votes else 0
    leaders = [s for s in STANCES if tally[s] == top and top]
    return {
        "subject": subject,
        "votes": votes,
        "tally": tally,
        "majority": leaders[0] if len(leaders) == 1 else None,   # None: a tie or no votes
        "long_case": _points(data.get("long_case")),
        "short_case": _points(data.get("short_case")),
        "debate": _clip(data.get("debate"), TEXT_CHARS),
        "conclusion": _clip(data.get("conclusion"), TEXT_CHARS),
    }


_STANCE_KO = {"long": "롱", "short": "숏", "hold": "관망"}


def render(summary: dict) -> str:
    """The chat message for the summary (the card renders the same data on its own)."""
    t = summary["tally"]
    lines = [f"**결론**: {summary['conclusion']}"] if summary["conclusion"] else []
    lines.append(f"**투표**: 롱 {t['long']} · 숏 {t['short']} · 관망 {t['hold']}")
    if summary["long_case"]:
        lines += ["", "**롱 관점**", *[f"- {p}" for p in summary["long_case"]]]
    if summary["short_case"]:
        lines += ["", "**숏 관점**", *[f"- {p}" for p in summary["short_case"]]]
    if summary["debate"]:
        lines += ["", f"**토론 요지**: {summary['debate']}"]
    return "\n".join(lines)


def llm_summariser(ctx: ConsensusContext) -> str:
    """The TradingAgents provider and quick model. Blocking: run it in a thread."""
    from tradingagents.default_config import build_default_config
    from tradingagents.llm_clients import create_tier_client

    config = build_default_config()
    if not ctx.language:
        ctx = replace(ctx, language=config.get("output_language"))
    # one attempt: the SDK's retries would multiply the timeout past the runner's backstop
    llm = create_tier_client(config, "quick", timeout=CONSENSUS_TIMEOUT_SEC, max_retries=0).get_llm()
    return llm.invoke(build_prompt(ctx)).content


def fake_summariser(ctx: ConsensusContext) -> str:
    """A scripted answer for DESK_FAKE_RUNS: bulls long, bears short, the rest with the plan."""
    lean = stance_of(ctx.plan.get("rating")) or "hold"
    stance = {"bull_researcher": "long", "bear_researcher": "short", "conservative_analyst": "hold"}
    votes = [{"agent": a, "stance": stance.get(a, lean), "reason": f"{a} 의 발언 요지"}
             for a in speeches(ctx.final_state)]
    return json.dumps({
        "votes": votes,
        "long_case": ["20일선 위 추세 유지", "수주 모멘텀과 이익 성장"],
        "short_case": ["PER 34배로 기대 선반영", "실적 발표 전 변동성"],
        "debate": "공격형은 비중 확대, 보수형은 3% 시작을 주장했고 5%로 시작해 손절 이탈 시 정리하기로 했습니다.",
        "conclusion": f"{ctx.ticker} {ctx.plan.get('rating')} — 계획의 진입 · 손절 · 목표를 따릅니다.",
    }, ensure_ascii=False)
