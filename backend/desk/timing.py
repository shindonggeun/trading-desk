"""Where an analysis spends its time (#171): the desk's steps around the graph and each agent's turn.

``Stopwatch`` marks the stages of ``RunManager._execute`` and records when each agent's message
arrived from the graph's worker thread. The result goes into ``plan["timing"]`` (seconds):

    {"total": 412.3, "stages": {"desk_pre": 6.1, "graph": 371.0, "desk_post": 9.4, "consensus": 25.8},
     "agents": [{"agent": "market_analyst", "at": 88.2, "took": 88.2, "parallel": true}, ...],
     "critical_path": ["news_analyst", "bull_researcher", ...]}

Analysts run in parallel from the graph's start, so an analyst's ``took`` is its arrival time; every
later agent waits for the one before it, so its ``took`` is the gap since the previous message.
``summarize`` turns recent plans' timings into averages for ``python -m desk.cli timing``.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Iterable

STAGES = ("desk_pre", "graph", "desk_post", "consensus")
# The graph's analysts start together (TradingAgents graph/setup.py); everyone after them is in series.
PARALLEL = {"market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst"}


class Stopwatch:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self._last = self._start
        self._stages: dict[str, float] = {}
        self._graph_start: float | None = None
        self._arrivals: list[tuple[str, float]] = []

    def mark(self, stage: str) -> None:
        """The stage that just ended (time since the previous mark)."""
        now = self._clock()
        self._stages[stage] = self._stages.get(stage, 0.0) + (now - self._last)
        self._last = now
        if stage == "desk_pre":
            self._graph_start = now

    def message(self, event: dict) -> None:
        """An event from the graph (any thread): agent messages are the turns."""
        if event.get("type") == "message" and event.get("agent"):
            self._arrivals.append((event["agent"], self._clock()))

    def result(self) -> dict:
        start = self._graph_start if self._graph_start is not None else self._start
        agents, previous = [], start
        for agent, at in sorted(self._arrivals, key=lambda a: a[1]):
            parallel = agent in PARALLEL
            took = at - start if parallel else at - previous
            agents.append({"agent": agent, "at": round(at - start, 1), "took": round(max(took, 0.0), 1),
                           "parallel": parallel})
            previous = at
        # the serial chain that set the graph's length: the slowest analyst, then everyone after
        first = [a for a in agents if a["parallel"]]
        chain = ([max(first, key=lambda a: a["took"])["agent"]] if first else []) + \
            [a["agent"] for a in agents if not a["parallel"]]
        return {"total": round(self._last - self._start, 1),
                "stages": {k: round(v, 1) for k, v in self._stages.items()},
                "agents": agents, "critical_path": chain}

    def line(self) -> str:
        """One log line: total and stages."""
        r = self.result()
        return f"total {r['total']}s · " + " · ".join(f"{k} {v}s" for k, v in r["stages"].items())


def _stats(values: list[float]) -> dict:
    return {"n": len(values), "mean": round(statistics.fmean(values), 1), "median": round(statistics.median(values), 1),
            "max": round(max(values), 1)} if values else {"n": 0, "mean": None, "median": None, "max": None}


def summarize(timings: Iterable[dict]) -> dict:
    """Averages over plans' ``timing``: the whole run, each stage and each agent's turn."""
    timings = [t for t in timings if t and t.get("total") is not None]
    stages = {s: _stats([t["stages"][s] for t in timings if s in (t.get("stages") or {})]) for s in STAGES}
    per_agent: dict[str, list[float]] = {}
    for t in timings:
        for a in t.get("agents") or []:
            per_agent.setdefault(a["agent"], []).append(a["took"])
    return {"runs": len(timings), "total": _stats([t["total"] for t in timings]), "stages": stages,
            "agents": {k: _stats(v) for k, v in sorted(per_agent.items(), key=lambda kv: -statistics.fmean(kv[1]))}}
