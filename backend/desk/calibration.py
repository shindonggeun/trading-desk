"""Playbook score -> probability and expected R from the desk's own record (#147).

A playbook's score (0-100) is a sum of rules: it does not say how often such a plan reached its
first target. This module keeps the record per (horizon, side, setup, regime, score band) - the
basket backtest as the prior (``desk/data/playbook_calibration.json``, written by
``python -m desk.cli calibrate``), the live outcomes (desk.outcome) added on top as they settle -
and answers with the T1 rate, the expected R and the sample size.

A thin cell falls back to (setup, regime) and then to the setup alone; with fewer than ``MIN_N``
trades even there it answers "thin" and gives no probability. Only filled, settled trades count
(the same as desk.backtest.aggregate): ``p_t1`` is over the trades that were entered.

Backtest plans overlap (one per bar on a moving window), so their count overstates the evidence:
a cell's prior is scaled down to at most ``PRIOR_CAP`` trades, and the live record weighs fully.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

VERSION = 1
PATH = Path(__file__).parent / "data" / "playbook_calibration.json"
SETTLED = ("t2", "t1_be", "stop", "time_stop", "flat")
BANDS = (0, 40, 60, 80)          # score bands: 0-39, 40-59, 60-79, 80+
MIN_N = 30                       # fewer settled trades than this: "thin", no probability
PRIOR_CAP = 200.0                # a backtest cell counts as at most this many trades
LIVE_DAYS = 365                  # the live record: runs created this far back (desk.api score loop)
DISABLE = False                  # True: a "negative" setup becomes setup none (off: every setup is below 0 after costs, #147)
DISABLE_N = 100                  # "negative" needs at least this many trades...
DISABLE_Z = 1.645                # ...and only when even the upper 90% bound of its expected R is below 0
Z_P = 1.645                      # the p_t1 interval (90%, Wilson)


def band(score: float | None) -> int:
    s = score or 0
    return max(b for b in BANDS if s >= b)


def keys(horizon: str, side: str, setup: str, regime: str, score) -> list[str]:
    """Most specific first: the score band, then (setup, regime), then the setup."""
    return [f"{horizon}|{side}|{setup}|{regime}|{band(score)}", f"{horizon}|{side}|{setup}|{regime}|*",
            f"{horizon}|{side}|{setup}|*|*"]


def _empty() -> dict:
    return {"n": 0.0, "t1": 0.0, "r": 0.0, "r2": 0.0}


def add(cells: dict, horizon: str, side: str, setup: str, regime: str, score, took_t1: bool, r: float) -> None:
    for k in keys(horizon, side, setup, regime or "unknown", score):
        c = cells.setdefault(k, _empty())
        c["n"] += 1
        c["t1"] += 1 if took_t1 else 0
        c["r"] += r
        c["r2"] += r * r


def from_trades(trades, horizon: str) -> dict:
    """Cells from desk.backtest.Trade rows (or dicts with the same fields): settled trades, net R."""
    cells: dict = {}
    for t in trades:
        g = t if isinstance(t, dict) else t.__dict__
        if g.get("status") not in SETTLED or g.get("r_net") is None:
            continue
        add(cells, horizon, g["side"], g["setup"], g.get("regime"), g.get("score"),
            bool(g.get("took_t1")), float(g["r_net"]))
    return cells


def from_runs(runs: list[dict]) -> dict:
    """Cells from finished runs and their outcomes (desk.store.scored_since): the live record."""
    cells: dict = {}
    for run in runs:
        plan = run.get("plan") or {}
        pb = plan.get("playbooks") or {}
        scored = (run.get("outcome") or {}).get("playbooks") or {}
        regime = (pb.get("regime") or {}).get("id") or "unknown"
        for side in ("long", "short"):
            s, o = pb.get(side) or {}, scored.get(side) or {}
            if s.get("setup") in (None, "none") or o.get("status") not in SETTLED or o.get("r_size") is None:
                continue
            add(cells, plan.get("horizon") or "swing", side, s["setup"], regime, s.get("score"),
                bool(o.get("took_t1")), float(o["r_size"]))
    return cells


class Table:
    """The prior (backtest) and the live record; ``look`` merges them per cell."""

    def __init__(self, prior: dict | None = None, meta: dict | None = None) -> None:
        self.prior = prior or {}
        self.live: dict = {}
        self.meta = meta or {}

    @classmethod
    def load(cls, path: Path = PATH) -> Table:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("playbook calibration %s unreadable: %s", path, exc)
            return cls()
        return cls(data.get("cells") or {}, {k: v for k, v in data.items() if k != "cells"})

    def set_live(self, runs: list[dict]) -> None:
        self.live = from_runs(runs)

    def _cell(self, key: str) -> dict | None:
        p, q = self.prior.get(key), self.live.get(key)
        if not p and not q:
            return None
        out = _empty()
        if p and p["n"]:
            scale = min(1.0, PRIOR_CAP / p["n"])
            for f in out:
                out[f] += p[f] * scale
        if q:
            for f in out:
                out[f] += q[f]
        out["live"] = q["n"] if q else 0
        return out

    def look(self, horizon: str, side: str, setup: str, regime: str | None, score) -> dict:
        """{"p_t1", "exp_r", "n", "level", "thin", "p_range", "live_n", "negative"}; thin = no numbers."""
        names = ("band", "regime", "setup")
        cells = [(name, self._cell(key))
                 for name, key in zip(names, keys(horizon, side, setup, regime or "unknown", score), strict=True)]
        # "negative" is the setup's verdict in this regime (or overall), whatever band the score is in
        negative = next((_negative(c) for _name, c in cells[1:] if c and c["n"] >= DISABLE_N), False)
        for name, c in cells:
            if c and c["n"] >= MIN_N:
                return {**_numbers(c, name), "negative": negative}
        tried = next(((name, c) for name, c in cells if c), None)
        return {"p_t1": None, "exp_r": None, "n": round(tried[1]["n"]) if tried else 0,
                "level": tried[0] if tried else None, "thin": True, "p_range": None,
                "live_n": round(tried[1]["live"]) if tried else 0, "negative": False}


def _negative(c: dict) -> bool:
    """Negative expected R with even the upper bound below zero, on DISABLE_N trades or more."""
    n = c["n"]
    mean = c["r"] / n
    var = max(0.0, c["r2"] / n - mean * mean)
    return n >= DISABLE_N and mean + DISABLE_Z * math.sqrt(var / n) < 0


def _numbers(c: dict, level: str) -> dict:
    n, p = c["n"], c["t1"] / c["n"]
    mean = c["r"] / n
    # Wilson interval: honest at the small counts a cell starts with
    centre = (p + Z_P ** 2 / (2 * n)) / (1 + Z_P ** 2 / n)
    half = Z_P * math.sqrt(p * (1 - p) / n + Z_P ** 2 / (4 * n * n)) / (1 + Z_P ** 2 / n)
    return {"p_t1": round(p, 3), "exp_r": round(mean, 3), "n": round(n), "level": level, "thin": False,
            "p_range": [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)],
            "live_n": round(c["live"]), "negative": False}


def write(cells: dict, meta: dict, path: Path = PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rounded = {k: {f: round(v, 4) for f, v in c.items()} for k, c in sorted(cells.items())}
    body = {"version": VERSION, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), **meta,
            "cells": rounded}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def reliability(cells_train: dict, trades_valid, horizon: str) -> list[dict]:
    """Per score band of the held-out trades: the probability the training table gave against the
    T1 rate that happened (the #147 done-when: within 10 points per band)."""
    table = Table(cells_train)
    rows: dict[int, dict] = {}
    for t in trades_valid:
        g = t if isinstance(t, dict) else t.__dict__
        if g.get("status") not in SETTLED or g.get("r_net") is None:
            continue
        got = table.look(horizon, g["side"], g["setup"], g.get("regime"), g.get("score"))
        if got["thin"]:
            continue
        row = rows.setdefault(band(g.get("score")), {"n": 0, "p": 0.0, "hit": 0, "r": 0.0, "exp": 0.0})
        row["n"] += 1
        row["p"] += got["p_t1"]
        row["hit"] += 1 if g.get("took_t1") else 0
        row["r"] += g["r_net"]
        row["exp"] += got["exp_r"]
    return [{"band": b, "n": r["n"], "predicted": round(r["p"] / r["n"], 3), "actual": round(r["hit"] / r["n"], 3),
             "gap": round(r["p"] / r["n"] - r["hit"] / r["n"], 3),
             "exp_r_predicted": round(r["exp"] / r["n"], 3), "r_actual": round(r["r"] / r["n"], 3)}
            for b, r in sorted(rows.items())]


TABLE = Table.load()


PROMPT_RULE = ("Desk record = this setup's past trades (backtest + live, after costs): base any win "
               "probability on it; a thin sample is no edge, a negative one needs an extra reason and a smaller size.")


def prompt_bit(cal: dict | None) -> str | None:
    """A side line's short tail for the deciding agents (desk.playbook.prompt_note is capped)."""
    if not cal:
        return None
    if cal["thin"]:
        return f"Desk record: thin sample ({cal['n']}), no measured edge."
    return (f"Desk record: {cal['n']} trades, T1 {cal['p_t1'] * 100:.0f}% "
            f"({cal['p_range'][0] * 100:.0f}-{cal['p_range'][1] * 100:.0f}), {cal['exp_r']:+.2f}R"
            + (" - negative." if cal["negative"] else "."))
