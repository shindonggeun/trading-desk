"""Historical net wins for exact horizon, side, setup and playbook regime cohorts."""

from __future__ import annotations

import json
import math
from datetime import date
from functools import lru_cache

from . import backtest, backtest_reports, calibration

VERSION = 1
WIN_RULE = "체결 후 청산된 거래 중 진입·청산 비용 차감 후 순손익이 0R보다 큰 거래"


def summarize(rows: list[dict]) -> dict:
    """Count settled replay trades in the held-out set, at most one per ticker/day/cohort."""
    cells: dict = {}
    seen = set()
    for row in sorted(rows, key=lambda r: r["day"]):
        for trade in row["trades"].values():
            if (trade.regime in ("unknown", None) or trade.status not in backtest.SETTLED
                    or not trade.filled_shares or trade.r_net is None or not math.isfinite(trade.r_net)):
                continue
            key = f"{trade.side}|{trade.setup}|{trade.regime}"
            day = row["day"]
            identity = (key, trade.ticker, day)
            if identity in seen:
                continue
            seen.add(identity)
            cell = cells.setdefault(key, {"n": 0, "wins": 0, "start": day, "end": day})
            cell["n"] += 1
            cell["wins"] += int(trade.r_net > 0)
            cell["end"] = day
    return {"version": VERSION, "rules": backtest_reports.rules_fingerprint(), "cells": cells}


@lru_cache
def _report(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def lookup(report_dir, horizon: str, side: str, setup: str | None, regime: str | None) -> dict:
    empty = {"status": "unavailable", "n": 0, "win_rate": None, "start": None, "end": None,
             "win_rule": None, "source": None, "costs_bps_per_side": None}
    if not setup or not regime or regime == "unknown":
        return empty
    report = _report(report_dir / f"entry_now_{horizon}.json")
    stats = report.get("direction_stats")
    if (report.get("horizon") != horizon or not isinstance(stats, dict) or stats.get("version") != VERSION
            or stats.get("rules") != backtest_reports.rules_fingerprint()):
        return empty
    cells = stats.get("cells")
    cell = cells.get(f"{side}|{setup}|{regime}") if isinstance(cells, dict) else None
    if not isinstance(cell, dict):
        return empty
    n, wins, cost = cell.get("n"), cell.get("wins"), report.get("costs_bps")
    if (type(n) is not int or type(wins) is not int or n <= 0 or not 0 <= wins <= n
            or type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
        return empty
    try:
        cut, start, end = (date.fromisoformat(x) for x in (report.get("cut"), cell.get("start"), cell.get("end")))
    except (ValueError, TypeError):
        return empty
    if not cut <= start <= end:
        return empty
    enough = n >= calibration.MIN_N
    return {"status": "available" if enough else "insufficient", "n": n,
            "win_rate": round(wins / n, 4) if enough else None, "start": start.isoformat(), "end": end.isoformat(),
            "win_rule": WIN_RULE, "source": "held_out_basket_replay", "costs_bps_per_side": cost}
