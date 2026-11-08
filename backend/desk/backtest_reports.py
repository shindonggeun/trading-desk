"""Basket backtest reports for the page (#185): snapshots kept in the repository, read as they are.

A basket backtest (desk.backtest, #144) fetches years of bars from Yahoo and takes minutes, so the
server never runs one. The report a run writes locally::

    python -m desk.cli backtest --basket --horizon swing --from 2019-06-01 --out desk/data/backtest/swing.json

is committed under ``desk/data/backtest/<horizon>.json`` and ships in the image (package data).
This module lists those snapshots and hands one out. A file that is missing, unreadable or not a
report for its own horizon is left out of the list (logged) and reads as missing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import horizons

log = logging.getLogger(__name__)

DIR = Path(__file__).parent / "data" / "backtest"


def _load(horizon: str, root: Path) -> dict | None:
    path = root / f"{horizon}.json"
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("backtest report %s unreadable: %s", path.name, exc)
        return None
    if not isinstance(report, dict) or report.get("horizon") != horizon or not isinstance(report.get("baseline"), dict):
        log.warning("backtest report %s is not a %s report", path.name, horizon)
        return None
    return report


def get(horizon: str, root: Path | None = None) -> dict | None:
    """The report of one horizon; None when there is no usable snapshot. ``horizon`` must be one of
    desk.horizons.HORIZONS (ValueError otherwise) - it names a file."""
    if horizon not in horizons.HORIZONS:
        raise ValueError(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
    return _load(horizon, root or DIR)


def summaries(root: Path | None = None) -> list[dict]:
    """One line per horizon with a snapshot, in the horizons' own order."""
    out = []
    for h in horizons.HORIZONS.values():
        report = _load(h.id, root or DIR)
        if report is None:
            continue
        base = report["baseline"]
        out.append({"horizon": h.id, "label": h.label, "generated_at": report.get("generated_at"),
                    "start": report.get("start"), "end": report.get("end"), "tickers": report.get("tickers") or [],
                    "plans": base.get("plans"), "filled": base.get("filled"),
                    "expectancy_r": base.get("expectancy_r")})
    return out
