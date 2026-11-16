"""Read-only view of semibot's regime and trigger logs (D:/ProjectWorkSpace/stock), and the desk's own
regime (#409) for where semibot does not run (the server).

semibot is the fast layer (1-minute Toss polling, regime x trigger); this desk is the
slow layer (a TradingAgents plan a few times a day). Showing both side by side is the
point: a plan says "buy near 182.5", semibot says whether the tape agrees right now.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

# #409 the market whose selling score stands for "the US / Korean tape": the score reads the market's
# indices (risk_regime picks them from the ticker's market), so any ticker of that market will do
DESK_MARKETS = (("us", "SPY"), ("kr", "005930.KS"))
RATE_KEYS = ("rates", "yield10", "yield_spike", "dollar", "sideways")


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


def desk_view(risk: Callable[[str], dict | None] | None, rates: Callable[[], dict | None]) -> dict:
    """#409 the desk's own regime: each market's selling score and phase (#146) and the rate / dollar
    regime (#330). A part that is not available is left out and named in ``error`` (Korean)."""
    markets, missing = [], []
    for market, ticker in DESK_MARKETS:
        name = "미장" if market == "us" else "국장"
        if risk is None:
            missing.append(f"{name} 위험 점수")
            continue
        try:
            s = risk(ticker)
        except Exception:  # one market failing leaves the rest
            log.exception("desk regime: risk score for %s failed", market)
            s = None
        if not s:
            missing.append(f"{name} 위험 점수")
            continue
        markets.append({"market": market, "score": s.get("score"), "peak": s.get("peak"),
                        "phase": s.get("phase"), "label": s.get("label"), "as_of": s.get("as_of")})
    try:
        g = rates()
    except Exception:  # the rest still shows
        log.exception("desk regime: rate / dollar regime failed")
        g = None
    if not g:
        missing.append("금리 · 달러 국면")
    rate_view = {**{k: g.get(k) for k in RATE_KEYS}, "as_of": g.get("as_of")} if g else None
    return {"markets": markets, "rates": rate_view,
            "error": f"{' · '.join(missing)}을(를) 아직 받지 못했습니다" if missing else None}
