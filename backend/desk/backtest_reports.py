"""Basket backtest reports for the page (#185): snapshots kept in the repository, read as they are.

A basket backtest (desk.backtest, #144) fetches years of bars from Yahoo and takes minutes, so the
server never runs one. The report a run writes locally::

    python -m desk.cli backtest --basket --horizon swing --from 2019-06-01 --out desk/data/backtest/swing.json

is committed under ``desk/data/backtest/<horizon>.json`` and ships in the image (package data).
This module lists those snapshots and hands one out. A file that is missing, unreadable or not a
report for its own horizon is left out of the list (logged) and reads as missing.

#388: a snapshot records the commit it was made from and a fingerprint of the rule code - the
desk modules desk.backtest imports, followed through their own relative imports, less the modules
that only fetch or file the inputs (``DATA_MODULES``, #424: prices, flow and news history). The list and the
report say ``stale`` when the code shipped now has another fingerprint: the plan rules changed
after the snapshot was made, so its numbers may not be the current rules' (rebuild it with the
command above). A snapshot older than the fingerprint reads ``stale: None`` (unknown).
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import logging
import subprocess
from pathlib import Path

from . import horizons

log = logging.getLogger(__name__)

PKG = Path(__file__).parent
DIR = PKG / "data" / "backtest"
RULES_ROOT = "backtest"          # the module whose import closure is the rule code
# #424: modules that fetch or file the inputs (bars, investor flow, news), not rules: the walk stops
# at them and they are left out, so a change in how data is fetched does not mark the snapshots stale
DATA_MODULES = frozenset({"prices", "flow_history", "news_history", "macro_news", "news_digest", "sector_news"})


def rule_modules(root: str = RULES_ROOT, pkg: Path = PKG, data: frozenset[str] = DATA_MODULES) -> list[str]:
    """``root`` and every desk module it reaches through relative imports (``from . import x``,
    ``from .x import y``), sorted. Imports inside functions count too; ``data`` modules are neither
    counted nor followed (#424)."""
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        path = pkg / f"{name}.py"
        if name in seen or name in data or not path.is_file():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module:
                    todo.append(node.module.split(".")[0])
                else:
                    todo.extend(alias.name for alias in node.names)
    return sorted(seen)


@functools.cache
def rules_fingerprint(pkg: Path = PKG) -> str:
    """12 hex of a SHA-256 over the rule modules' source (line endings normalised)."""
    digest = hashlib.sha256()
    for name in rule_modules(pkg=pkg):
        if name == __name__.rsplit(".", 1)[-1]:
            continue                 # this file only reads snapshots - editing it changes no rule
        digest.update(name.encode() + b"\0")
        digest.update((pkg / f"{name}.py").read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return digest.hexdigest()[:12]


def source_commit(cwd: Path = PKG) -> str | None:
    """The develop commit the snapshot's code comes from: the merge base with origin/develop (a
    branch's own commits get new ids when rebased onto develop), else HEAD; None without git."""
    for args in (["git", "merge-base", "HEAD", "origin/develop"], ["git", "rev-parse", "HEAD"]):
        try:
            out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.stdout.strip():
            return out.stdout.strip()[:12]
    return None


def _stale(report: dict) -> bool | None:
    rules = report.get("rules")
    return None if not rules else rules != rules_fingerprint()


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
    report = _load(horizon, root or DIR)
    return None if report is None else {**report, "stale": _stale(report)}


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
                    "commit": report.get("commit"), "stale": _stale(report),
                    "plans": base.get("plans"), "filled": base.get("filled"),
                    "expectancy_r": base.get("expectancy_r")})
    return out
