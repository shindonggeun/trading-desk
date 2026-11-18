"""Turn TradingAgents' stream into chat events.

``TradingAgentsGraph.stream_run`` yields ``(messages, state)`` pairs where ``state``
is either the whole run state (top-level step), one analyst's freshly filed report,
or None. Each agent's contribution lands in a known state field, so diffing those
fields gives "who said what, in order" without depending on LangGraph node names.
Debate histories only ever grow, so the new part of a history is the new turn.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from . import language as lang

log = logging.getLogger(__name__)

# (state key, agent id) for single-shot outputs, and (debate key, [(history key, agent id)]).
# Listed in pipeline order so one state carrying several fields still reads in order.
_SLOTS: list[tuple[str, Any]] = [
    ("market_report", "market_analyst"),
    ("sentiment_report", "sentiment_analyst"),
    ("news_report", "news_analyst"),
    ("fundamentals_report", "fundamentals_analyst"),
    ("investment_debate_state", [("bull_history", "bull_researcher"), ("bear_history", "bear_researcher")]),
    ("investment_plan", "research_manager"),
    ("trader_investment_plan", "trader"),
    ("risk_debate_state", [
        ("aggressive_history", "aggressive_analyst"),
        ("conservative_history", "conservative_analyst"),
        ("neutral_history", "neutral_analyst"),
    ]),
    ("final_trade_decision", "portfolio_manager"),
]

# Debate turns are stored as "Bull Analyst: ..." and the like; the UI already names the speaker.
_SPEAKER_PREFIX = re.compile(
    r"^\s*(?:Bull|Bear|Aggressive|Conservative|Neutral|Risky|Safe)\s*(?:Analyst|Researcher)?\s*:\s*",
    re.IGNORECASE,
)


def _message_key(message: Any) -> Any:
    msg_id = getattr(message, "id", None)
    if msg_id:
        return msg_id
    return (type(message).__name__, str(getattr(message, "content", "")), str(getattr(message, "tool_calls", "")))


def _tool_calls(message: Any) -> list[tuple[str, dict]]:
    calls = []
    for call in getattr(message, "tool_calls", None) or []:
        if isinstance(call, dict):
            calls.append((call.get("name", "?"), call.get("args") or {}))
        else:
            calls.append((getattr(call, "name", "?"), getattr(call, "args", {}) or {}))
    return calls


class StateDiffer:
    """Feed it every streamed pair; it returns only what is new since the last call."""

    def __init__(self, language: str | None = None) -> None:
        self._seen: dict[str, str] = {}
        self._messages: set[Any] = set()
        # #567: a Korean run's turn in another language is logged (live turns are not translated)
        self._korean = lang.korean(language)

    def seed(self, key: str, value: str) -> None:
        """A state field the run starts with (#171: a reused report) - already said, not new."""
        self._seen[key] = value

    def feed(self, messages: Iterable[Any] | None, state: dict | None) -> list[dict]:
        events: list[dict] = []
        for message in messages or []:
            key = _message_key(message)
            if key in self._messages:
                continue
            self._messages.add(key)
            for name, args in _tool_calls(message):
                events.append({"type": "tool", "agent": "tool", "content": name, "data": {"args": args}})

        if not isinstance(state, dict):
            return events

        for key, target in _SLOTS:
            value = state.get(key)
            if isinstance(target, str):
                if isinstance(value, str) and value.strip() and value != self._seen.get(key):
                    self._seen[key] = value
                    events.append(_message(target, value))
            elif isinstance(value, dict):
                for history_key, agent in target:
                    events.extend(self._debate_turn(f"{key}.{history_key}", value.get(history_key), agent))
        if self._korean:
            for event in events:
                if event["type"] == "message" and (kind := lang.foreign(event["content"])):
                    log.warning("language: %s wrote %s in a Korean run", event["agent"], kind)
        return events

    def _debate_turn(self, seen_key: str, history: Any, agent: str) -> list[dict]:
        if not isinstance(history, str):
            return []
        old = self._seen.get(seen_key, "")
        if history == old:
            return []
        self._seen[seen_key] = history
        turn = history.removeprefix(old)
        turn = _SPEAKER_PREFIX.sub("", turn.strip())
        return [_message(agent, turn)] if turn else []


def _message(agent: str, content: str) -> dict:
    return {"type": "message", "agent": agent, "content": content.strip(), "data": None}
