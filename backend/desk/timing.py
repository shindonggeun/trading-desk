"""Where an analysis spends its time (#171): the desk's steps around the graph and each agent's turn.

``Stopwatch`` marks the stages of ``RunManager._execute`` and records when each agent's message
arrived from the graph's worker thread. The result goes into ``plan["timing"]`` (seconds):

    {"total": 412.3, "stages": {"desk_pre": 6.1, "graph": 371.0, "desk_post": 9.4, "consensus": 25.8},
     "agents": [{"agent": "market_analyst", "at": 88.2, "took": 88.2, "parallel": true}, ...],
     "critical_path": ["news_analyst", "bull_researcher", ...]}

Analysts run in parallel from the graph's start, so an analyst's ``took`` is its arrival time; every
later agent waits for the one before it, so its ``took`` is the gap since the previous message.
Caveats when reading the numbers:
- the graph's "Memory Log" node (past decisions settled with prices and a reflection) runs beside the
  analysts and sends no message: when it is the slowest, its extra wait lands in the first serial
  agent's ``took`` (marked ``includes_join``);
- the graph's start is just before the worker thread: building the graph and the run state (a few
  seconds) is in every analyst's ``took`` and in the ``graph`` stage;
- ``turn`` numbers an agent's turns (debate rounds > 1 give bull/bear several);
- with the library's parallel first rounds on (``TRADINGAGENTS_*_PARALLEL_FIRST_ROUND``), a debate's
  openers arrive in one state update, milliseconds apart: serial messages within ``TOGETHER_GAP`` of
  each other are one step (``together``), each timed from before that step, and the step counts
  once in the critical path;
- the desk's own stages overlap where they can (#171 step 5): ``desk_pre`` waits for the slow notes
  (related instruments, filings, positioning) only up to the horizon's budget
  (``runner.NOTES_WAIT_SEC``) - the ones left out are listed in ``late_notes``; ``consensus`` is the
  closing summary with the product's playbooks and trade map built beside it, so ``desk_post`` no
  longer holds them; ``leverage`` has the underlying side's levels filled while its analyst thinks.
``summarize`` turns recent plans' timings into averages for ``python -m desk.cli timing``.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Iterable

# leverage: leveraged products only; horizons (#696): the four horizons' maps past the summary's overlap
STAGES = ("desk_pre", "graph", "leverage", "desk_post", "horizons", "consensus")
# The graph's analysts start together (TradingAgents graph/setup.py); everyone after them is in series.
PARALLEL = {"market_analyst", "sentiment_analyst", "news_analyst", "fundamentals_analyst"}
# A model turn takes seconds; serial messages closer than this came out of one graph step.
TOGETHER_GAP = 1.0


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
        agents, previous, base, turns, joined = [], start, start, {}, False
        steps: list[list[dict]] = []                 # serial steps: one speaker, or openers together
        for agent, at in sorted(self._arrivals, key=lambda a: a[1]):
            parallel = agent in PARALLEL
            turns[agent] = turns.get(agent, 0) + 1
            together = (not parallel and bool(steps) and agents[-1] is steps[-1][-1]
                        and at - previous < TOGETHER_GAP)
            if not parallel and not together:
                base = previous                      # a new serial step starts after the last message
            row = {"agent": agent, "turn": turns[agent], "at": round(at - start, 1),
                   "took": round(max(at - start if parallel else at - base, 0.0), 1), "parallel": parallel}
            if together:
                steps[-1][-1]["together"] = row["together"] = True
                steps[-1].append(row)
            elif not parallel:
                steps.append([row])
            if not parallel and not joined:
                row["includes_join"] = True     # waited for every parallel node (Memory Log too)
                joined = True
            agents.append(row)
            previous = at
        # the serial chain that set the graph's length: the slowest analyst, then each serial step
        # (for openers together, the last to finish)
        first = [a for a in agents if a["parallel"]]
        chain = ([max(first, key=lambda a: a["took"])["agent"]] if first else []) + \
            [step[-1]["agent"] for step in steps]
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
    per_turn: dict[str, list[float]] = {}            # one row per agent and turn ("bull_researcher#2")
    per_run: dict[str, list[float]] = {}             # an agent's turns added up within a run
    for t in timings:
        sums: dict[str, float] = {}
        for a in t.get("agents") or []:
            turn = a.get("turn", 1)
            per_turn.setdefault(a["agent"] if turn == 1 else f"{a['agent']}#{turn}", []).append(a["took"])
            sums[a["agent"]] = sums.get(a["agent"], 0.0) + a["took"]
        for agent, total in sums.items():
            per_run.setdefault(agent, []).append(total)

    def order(d: dict[str, list[float]]) -> list:
        return sorted(d.items(), key=lambda kv: -statistics.fmean(kv[1]))

    return {"runs": len(timings), "total": _stats([t["total"] for t in timings]), "stages": stages,
            "agents": {k: _stats(v) for k, v in order(per_turn)},
            "agents_per_run": {k: _stats(v) for k, v in order(per_run)}}
