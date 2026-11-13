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

#286: the same cells over the last ``RECENT_DAYS`` only (``recent``: the backtest's last twelve
months up to its last plan, plus live runs traded in the last twelve months). ``look`` adds them
and ``drift``: "weaker" / "stronger" when the recent expected R is off the long-term one by more
than ``DRIFT_Z`` standard errors (both sides with at least MIN_N trades). A weaker cell answers with
its recent numbers first (``basis: "recent"``, the long-term ones under ``long_term``) - a market
that changed is not averaged away by years before it. A file written before #286 has no recent
cells: no ``recent``, no drift, the numbers as before.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, date, datetime, timedelta
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
RECENT_DAYS = 365                # #286: the recent cells - the last twelve months
DRIFT_Z = 1.645                  # recent vs long-term expected R: off by more than this many standard errors


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


def recent_since(as_of: str | date) -> str:
    """The first day of the recent window ending on ``as_of`` (both ends included)."""
    day = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
    return (day - timedelta(days=RECENT_DAYS - 1)).isoformat()


def from_trades(trades, horizon: str, since: str | None = None) -> dict:
    """Cells from desk.backtest.Trade rows (or dicts with the same fields): settled trades, net R.
    ``since`` (YYYY-MM-DD) keeps the plans dated on or after it - the recent cells."""
    cells: dict = {}
    for t in trades:
        g = t if isinstance(t, dict) else t.__dict__
        if g.get("status") not in SETTLED or g.get("r_net") is None:
            continue
        if since is not None and str(g.get("at") or "")[:10] < since:
            continue
        add(cells, horizon, g["side"], g["setup"], g.get("regime"), g.get("score"),
            bool(g.get("took_t1")), float(g["r_net"]))
    return cells


def from_runs(runs: list[dict], since: str | None = None) -> dict:
    """Cells from finished runs and their outcomes (desk.store.scored_since): the live record.
    ``since`` keeps the runs whose trade date is on or after it - the recent cells."""
    cells: dict = {}
    for run in runs:
        if since is not None and str(run.get("trade_date") or "")[:10] < since:
            continue
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

    def __init__(self, prior: dict | None = None, meta: dict | None = None, recent: dict | None = None) -> None:
        self.prior = prior or {}
        self.live: dict = {}
        self.meta = meta or {}
        self.recent_prior = recent or {}          # #286: the backtest's last twelve months
        self.recent_live: dict = {}

    @classmethod
    def load(cls, path: Path = PATH) -> Table:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("playbook calibration %s unreadable: %s", path, exc)
            return cls()
        meta = {k: v for k, v in data.items() if k not in ("cells", "recent_cells")}
        return cls(data.get("cells") or {}, meta, data.get("recent_cells") or {})

    def set_live(self, runs: list[dict], today: date | None = None) -> None:
        self.live = from_runs(runs)
        self.recent_live = from_runs(runs, since=recent_since(today or datetime.now(UTC).date()))

    def _cell(self, key: str, recent: bool = False) -> dict | None:
        p = (self.recent_prior if recent else self.prior).get(key)
        q = (self.recent_live if recent else self.live).get(key)
        if not p and not q:
            return None
        out = _empty()
        if p and p["n"]:
            # the recent backtest plans overlap the same way: they take the long-run cell's discount
            # (not a cap of their own), so their standard error is as honest as the long run's
            whole = self.prior.get(key) if recent else p
            scale = min(1.0, PRIOR_CAP / whole["n"]) if whole and whole["n"] else min(1.0, PRIOR_CAP / p["n"])
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
        for (name, c), key in zip(cells, keys(horizon, side, setup, regime or "unknown", score), strict=True):
            if c and c["n"] >= MIN_N:
                return self._with_recent({**_numbers(c, name), "negative": negative}, c, key)
        tried = next(((name, c) for name, c in cells if c), None)
        return {"p_t1": None, "exp_r": None, "n": round(tried[1]["n"]) if tried else 0,
                "level": tried[0] if tried else None, "thin": True, "p_range": None,
                "live_n": round(tried[1]["live"]) if tried else 0, "negative": False}

    def _with_recent(self, out: dict, c: dict, key: str) -> dict:
        """#286: the same cell over the last twelve months and whether it drifted from the long run.
        A table without recent backtest cells (written before #286) has no recent answer at all - a
        few live trades alone are not a twelve-month record. On a weaker cell the numbers and
        ``negative`` are the recent cell's (on its discounted count, as DISABLE_N needs)."""
        if not self.recent_prior:
            return {**out, "recent": None, "drift": None}
        r = self._cell(key, recent=True)
        if not r or not r["n"]:
            return {**out, "recent": None, "drift": None}
        recent = {"n": round(r["n"]), "p_t1": round(r["t1"] / r["n"], 3), "exp_r": round(r["r"] / r["n"], 3)}
        drift = _drift(c, r)
        if drift == "weaker":
            nums = _numbers(r, out["level"])
            fields = ("p_t1", "exp_r", "n", "p_range", "live_n")
            return {**out, **{k: nums[k] for k in fields}, "basis": "recent", "negative": _negative(r),
                    "long_term": {k: out[k] for k in fields}, "recent": recent, "drift": drift}
        return {**out, "recent": recent, "drift": drift}


def _mean_se(c: dict) -> tuple[float, float]:
    n = c["n"]
    mean = c["r"] / n
    var = max(0.0, c["r2"] / n - mean * mean)
    return mean, math.sqrt(var / n)


def _drift(long: dict, recent: dict) -> str | None:
    """weaker / stronger when the recent expected R is off the long-term one by more than DRIFT_Z
    standard errors of the difference; None on fewer than MIN_N trades on either side."""
    if long["n"] < MIN_N or recent["n"] < MIN_N:
        return None
    m_long, se_long = _mean_se(long)
    m_recent, se_recent = _mean_se(recent)
    se = math.sqrt(se_long ** 2 + se_recent ** 2)
    if se == 0 or abs(m_recent - m_long) <= DRIFT_Z * se:
        return None
    return "weaker" if m_recent < m_long else "stronger"


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


def write(cells: dict, meta: dict, path: Path = PATH, recent: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def rounded(cs: dict) -> dict:
        return {k: {f: round(v, 4) for f, v in c.items()} for k, c in sorted(cs.items())}

    body = {"version": VERSION, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), **meta,
            "cells": rounded(cells)}
    if recent is not None:
        body["recent_cells"] = rounded(recent)
    with path.open("w", encoding="utf-8", newline="\n") as f:      # LF on Windows too (.gitattributes)
        f.write(json.dumps(body, ensure_ascii=False, indent=1) + "\n")


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
            + (" - negative." if cal["negative"] else ".")
            + (f" Recent 12 months weaker than its long run ({cal['long_term']['exp_r']:+.2f}R): "
               "the numbers above are the recent ones." if cal.get("drift") == "weaker" and cal.get("long_term") else "")
            + (f" Recent 12 months stronger ({cal['recent']['exp_r']:+.2f}R on {cal['recent']['n']})."
               if cal.get("drift") == "stronger" and cal.get("recent") else ""))


def drifted(cells: dict, recent: dict) -> list[dict]:
    """#286: the (setup, regime) and setup cells whose last twelve months drifted from their long run
    (as ``look`` sees them, the prior capped), weaker first."""
    table = Table(cells, recent=recent)
    rows = []
    for key in sorted(k for k in cells if k.endswith("|*")):
        c, r = table._cell(key), table._cell(key, recent=True)
        drift = _drift(c, r) if c and r else None
        if drift:
            rows.append({"key": key, "drift": drift, "long_n": round(c["n"]), "long_exp_r": round(c["r"] / c["n"], 3),
                         "recent_n": round(r["n"]), "recent_exp_r": round(r["r"] / r["n"], 3)})
    return sorted(rows, key=lambda x: (x["drift"] != "weaker", x["key"]))
