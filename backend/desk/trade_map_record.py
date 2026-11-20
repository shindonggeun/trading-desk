"""The trade map scenarios' past record by regime (#652), read from desk.trade_map_bt's report.

``desk/data/backtest/trade_map_scenarios.json`` holds, per side, entry kind (breakout · support) and
regime (desk.playbook.detect_regime: uptrend · range · downtrend · crash), the filled plans' count, win
rate, mean R and the worst run of R. ``lookup`` puts each scenario's row on it and says in one line which
entry did better in the plan's regime - with its sample, and as it is when neither did or the gap is too
small to tell (DIFF_R). The table is of daily bars (``HORIZON``): other horizons get none of it. The trades
overlap (a plan every 2 bars, held up to 25), so the sample said and compared is ``n_indep`` - the ones
that do not overlap. Past rates, not a forecast.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).parent / "data" / "backtest" / "trade_map_scenarios.json"
MIN_N = 30                 # fewer (non-overlapping) filled plans than this: said, not compared
DIFF_R = 0.1               # mean R closer than this: no better one
HORIZON = "swing"          # the backtest's bars (desk.trade_map_bt): the record is for this horizon only
KIND_KO = {"breakout": "돌파 확인 진입", "support": "지지 근처 진입"}
KIND_SHORT_KO = {"breakout": "이탈 확인 진입", "support": "저항 근처 진입"}   # as the screen names a short's
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


def _r(v: float) -> str:
    """Mean R with its sign; within half a cent of 0 it is 0 (no "-0.00R")."""
    return f"{0.0 if abs(v) < 0.005 else v:+.2f}R"


def _n(r: dict) -> int:
    return r.get("n_indep", r.get("n", 0))


def lookup(regime: str | None, scenarios: list[dict], table: dict | None = None,
           horizon: str | None = HORIZON) -> dict:
    """Each scenario's ``record`` in ``regime`` (in place), and the map's ``regime_note``. A ``horizon`` other
    than HORIZON gets no record (None everywhere)."""
    if horizon != HORIZON:
        for s in scenarios:
            s["record"] = None
        return {"regime_note": None, "record_meta": None}
    table = table if table is not None else _table()
    notes = []
    for side in dict.fromkeys(s["side"] for s in scenarios):
        kinds = KIND_KO if side == "long" else KIND_SHORT_KO
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
        if len({s["kind"] for s in scenarios if s["side"] == side}) < 2:
            continue                # one scenario only (no strong level near): nothing to compare, not a thin sample
        where = REGIME_KO.get(regime, regime)
        ok = {k: r for k, r in rows.items() if _n(r) >= MIN_N and r.get("avg_r") is not None}
        if len(ok) < 2:
            notes.append(f"{SIDE_KO[side]}: {where}에서 비교할 만큼 과거 표본이 쌓이지 않았어요")
            continue
        best = max(ok, key=lambda k: ok[k]["avg_r"])
        other = next(k for k in ok if k != best)
        b, o = ok[best], ok[other]
        both = f"{kinds[best]} {_r(b['avg_r'])} · {kinds[other]} {_r(o['avg_r'])}"
        if b["avg_r"] <= 0:
            notes.append(f"{SIDE_KO[side]}: {where}에서는 두 진입 모두 평균 R 이 0 이하였어요 ({both})")
        elif b["avg_r"] - o["avg_r"] < DIFF_R:
            notes.append(f"{SIDE_KO[side]}: {where}에서는 두 진입의 차이가 없었어요 ({both}, 겹치지 않는 표본 "
                         f"{_n(b)} · {_n(o)}번)")
        else:
            notes.append(f"{SIDE_KO[side]}: {where}에서는 과거 {kinds[best]}이 나았어요 — 평균 {_r(b['avg_r'])} · "
                         f"승률 {round(b['win_rate'] * 100)}% ({_n(b)}번) vs {kinds[other]} {_r(o['avg_r'])} · "
                         f"승률 {round(o['win_rate'] * 100)}% ({_n(o)}번, 겹치지 않는 표본)")
    meta = {k: table.get(k) for k in ("generated_at", "tickers", "from", "to")} if table else None
    if meta is not None:
        meta["horizon"] = HORIZON
    return {"regime_note": " / ".join(notes) or None, "record_meta": meta}
