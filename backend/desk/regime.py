"""Read-only view of semibot's regime and trigger logs (D:/ProjectWorkSpace/stock).

semibot is the fast layer (1-minute Toss polling, regime x trigger); this desk is the
slow layer (a TradingAgents plan a few times a day). Showing both side by side is the
point: a plan says "buy near 182.5", semibot says whether the tape agrees right now.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def snapshot(db_path: str, trigger_limit: int = 30) -> dict:
    if not db_path:
        return {"enabled": False}
    path = Path(db_path)
    if not path.exists():
        return {"enabled": True, "error": f"semibot DB not found: {db_path}", "regimes": [], "triggers": []}
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        regimes = conn.execute(
            """SELECT r.* FROM regime_log r
               JOIN (SELECT market, MAX(ts) AS ts FROM regime_log GROUP BY market) last
                 ON r.market = last.market AND r.ts = last.ts"""
        ).fetchall()
        triggers = conn.execute(
            "SELECT * FROM trigger_log ORDER BY ts DESC LIMIT ?", (trigger_limit,)
        ).fetchall()
    except sqlite3.Error as exc:
        return {"enabled": True, "error": f"semibot DB unreadable: {exc}", "regimes": [], "triggers": []}
    finally:
        conn.close()
    return {
        "enabled": True,
        "regimes": [
            {"ts": r["ts"], "market": r["market"], "label": r["label"], "score": r["score"],
             "flags": json.loads(r["flags_json"] or "[]")}
            for r in regimes
        ],
        "triggers": [
            {"ts": t["ts"], "symbol": t["symbol"], "kind": t["kind"], "direction": t["direction"],
             "level": t["level"]}
            for t in triggers
        ],
    }
