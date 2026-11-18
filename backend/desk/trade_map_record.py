"""The trade map scenarios' past record by regime (#652), read from desk.trade_map_bt's report.

``desk/data/backtest/trade_map_scenarios.json`` holds, per side, entry kind (breakout · support) and
regime (desk.playbook.detect_regime: uptrend · range · downtrend · crash), the filled plans' count, win
rate, mean R and the worst run of R. ``lookup`` puts each scenario's row on it and says in one line which
entry did better in the plan's regime - with its sample, and as it is when neither did. Past rates, not a
forecast.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).parent / "data" / "backtest" / "trade_map_scenarios.json"
MIN_N = 30                 # fewer filled plans than this: said, not compared
KIND_KO = {"breakout": "돌파 확인 진입", "support": "지지 근처 진입"}
SIDE_KO = {"long": "롱", "short": "숏"}
REGIME_KO = {"uptrend": "상승 추세", "range": "횡보", "downtrend": "하락 추세", "crash": "급락"}


@lru_cache(maxsize=1)
def _table(path: str = str(PATH)) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def row(side: str, kind: str, regime: str | None, table: dict | None = None) -> dict | None:
    got = (table if table is not None else _table()).get("rows") or {}
    return got.get(f"{side}:{kind}:{regime}") if regime else None


def lookup(regime: str | None, scenarios: list[dict], table: dict | None = None) -> dict:
    """Each scenario's ``record`` in ``regime`` (in place), and the map's ``regime_note``."""
    table = table if table is not None else _table()
    notes = []
    for side in dict.fromkeys(s["side"] for s in scenarios):
        rows = {}
        for s in scenarios:
            if s["side"] != side:
                continue
            r = row(side, s["kind"], regime, table)
            s["record"] = r
            if r:
                rows[s["kind"]] = r
        if not regime or not rows:
            continue
        where = REGIME_KO.get(regime, regime)
        if len({s["kind"] for s in scenarios if s["side"] == side}) < 2:
            continue                # one scenario only (no strong level near): nothing to compare, not a thin sample
        ok = {k: r for k, r in rows.items() if r.get("n", 0) >= MIN_N and r.get("avg_r") is not None}
        if len(ok) < 2:
            notes.append(f"{SIDE_KO[side]}: {where}에서 비교할 만큼 과거 표본이 쌓이지 않았어요")
            continue
        best = max(ok, key=lambda k: ok[k]["avg_r"])
        other = next(k for k in ok if k != best)
        b, o = ok[best], ok[other]
        if b["avg_r"] <= 0:
            notes.append(f"{SIDE_KO[side]}: {where}에서는 두 진입 모두 평균 R 이 0 이하였어요 "
                         f"({KIND_KO[best]} {b['avg_r']:+.2f}R · {KIND_KO[other]} {o['avg_r']:+.2f}R)")
            continue
        notes.append(f"{SIDE_KO[side]}: {where}에서는 과거 {KIND_KO[best]}이 나았어요 — 평균 {b['avg_r']:+.2f}R · "
                     f"승률 {round(b['win_rate'] * 100)}% ({b['n']}번) vs {KIND_KO[other]} {o['avg_r']:+.2f}R · "
                     f"승률 {round(o['win_rate'] * 100)}% ({o['n']}번)")
    return {"regime_note": " / ".join(notes) or None,
            "record_meta": {k: table.get(k) for k in ("generated_at", "tickers", "from", "to")} if table else None}
