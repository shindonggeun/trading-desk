"""#619 the intraday round model: fitted on the sessions before the hold-out, scored on the hold-out per
round (numpy; run locally, the server only reads the committed model - desk.day_rounds).

One model per market for every round: the time left is a feature, and the rows of a round are too few
for a model of their own (about two years of Yahoo's hourly bars). The fit, the L2 / lambda choice by
expanding-window folds and the scores are desk.day_call_train's. The baselines on the same rows: always
long, always short, and following the session so far (the sign of the move from the open to the round,
``follow_trend``). A row is one session and round; the rounds of a day are not independent, so the
95% interval of a pooled hit rate is narrower than it should be - the per-round tables are the honest one.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import day_call as dc
from . import day_call_train as dct
from . import day_rounds as dr

DAILY_BARS = 1000            # the features' 120-bar ATR window before the first hourly session
INDEX_HOURS = 5200           # ~730 sessions of 6-7 bars: all Yahoo keeps
NQ_HOURS = 20000             # ~23 bars a day: Yahoo's two years
HOLDOUT_DAYS = dct.HOLDOUT_DAYS
VERSION = 1


def fetch(candles: Callable | None = None) -> dict[str, list]:
    """Daily bars for the day call's symbols, the two indices' and the Nasdaq futures' hourly bars."""
    if candles is None:
        from .prices import yahoo_candles as candles
    out = {s: [b for b in candles(s, "1d", DAILY_BARS) if b.close] for s in dc.SYMBOLS}
    for key in dr.HOURLY.values():
        out[key] = [b for b in candles(key.split("@")[0], "60m", INDEX_HOURS) if b.close]
    out[dr.NQ] = [b for b in candles(dr.NQ.split("@")[0], "60m", NQ_HOURS) if b.close]
    return out


def rows(market: str, series: dict[str, Sequence], now: datetime | None = None) -> list[dict]:
    """One row per finished session (at ``now``) and round with its features and answer; ``prev_dir``
    is the session so far (follow-the-trend baseline)."""
    out = []
    for day in sorted(dr._days(market, series.get(dr.HOURLY[market]) or [])):
        if dc.label(market, day, series, "oc", now) is None:
            continue
        try:
            base = dc.features(market, 2, day, series)
        except dc.NoData:
            continue
        for round_ in dr.rounds_of(market, day):
            try:
                x, meta = dr.features(market, round_, day, series, base)
            except dc.NoData:
                continue
            answer = dr.label(market, day, round_, series, meta["anchor"], now)
            if answer is None:
                continue
            out.append({"day": day, "round": round_, "x": x, "z": answer["z"], "stage": answer["stage"],
                        "answer": answer, "prev_dir": math.copysign(1, x["i_move"]) if x["i_move"] else None})
    return out


def _metrics(P: np.ndarray, rs: Sequence[dict], clim: np.ndarray) -> dict:
    got = dct.metrics(P, rs, clim)
    if "baselines" in got:
        got["baselines"]["follow_trend"] = got["baselines"].pop("follow_yesterday")
    return got


def _version(model: dict) -> str:
    def rounded(v):
        if isinstance(v, float):
            return round(v, 9)
        if isinstance(v, list):
            return [rounded(x) for x in v]
        if isinstance(v, dict):
            return {k: rounded(x) for k, x in v.items()}
        return v
    blob = json.dumps(rounded(model), sort_keys=True).encode()
    return f"dr-{model['trained_through'].replace('-', '')}-{hashlib.sha256(blob).hexdigest()[:8]}"


def code_hash() -> str:
    """This model's code (desk.day_rounds and this module): a report from other code is stale."""
    here = Path(__file__).parent
    blob = b"".join((here / f).read_bytes().replace(b"\r\n", b"\n") for f in ("day_rounds.py", "day_rounds_train.py"))
    return hashlib.sha256(blob).hexdigest()[:12]


def backtest(market: str, series: dict[str, Sequence], holdout_days: int = HOLDOUT_DAYS,
             progress: Callable[[str], None] | None = None, now: datetime | None = None) -> tuple[dict, dict]:
    """The model file and the report of ``market``: fitted on the rows before the hold-out (its last
    ``holdout_days`` calendar days), scored on the hold-out pooled and per round."""
    say = progress or (lambda _m: None)
    rs = rows(market, series, now)
    if not rs:
        raise ValueError(f"{market}: no round rows")
    holdout_from = dct.holdout_start(market, series, holdout_days)
    train = [r for r in rs if r["day"] < holdout_from]
    hold = [r for r in rs if r["day"] >= holdout_from]
    if len({r["day"] for r in train}) < 100:
        raise ValueError(f"{market}: {len(train)} training rows")
    say(f"{market}: {len(train)} training rows ({train[0]['day']} ~ {train[-1]['day']}), {len(hold)} held out")
    names = [n for n in (*dr.DAILY, *dr.FEATURES) if n in rs[0]["x"]]
    l2, lam, cv = dct.select(train, names, dc.EDGES)
    model = dct._fit_model(train, names, dc.EDGES, l2, lam, dr.CALL)
    clim = np.array(model["climatology"])
    P = dct._predict(model, hold)
    by_round = {}
    for round_ in sorted(dr.ROUNDS[market]):
        mask = np.array([r["round"] == round_ for r in hold])
        part = [r for r in hold if r["round"] == round_]
        by_round[str(round_)] = {"time": f"{dr.round_time(market, round_):%H:%M}", "label": dr.round_label(market, round_),
                                 "holdout": _metrics(P[mask], part, clim) if part else {"n": 0}}
    share = np.bincount(dct._class_index([r["stage"] for r in train]), minlength=5) / len(train)
    version = _version(model)
    pooled = {**_metrics(P, hold, clim), "sessions": len({r["day"] for r in hold})} if hold else {"n": 0}
    verdict = dr.verdict_of(pooled)
    for part in by_round.values():
        part["verdict"] = dr.verdict_of(part["holdout"])
    model_file = {"version": version, "market": market, "call": dr.CALL, "rounds": sorted(dr.ROUNDS[market]),
                  "holdout_from": holdout_from.isoformat(), "verdict": verdict, "model": model}
    report = {
        "version": version, "market": market, "call": dr.CALL, "code": code_hash(), "format": VERSION,
        "made_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"), "holdout_from": holdout_from.isoformat(),
        "verdict": verdict, "edges": list(dc.EDGES), "features": names, "l2": l2, "lambda": lam, "cv": cv,
        "train": {"n": len(train), "sessions": len({r["day"] for r in train}), "from": train[0]["day"].isoformat(),
                  "to": train[-1]["day"].isoformat(), "stage_share": [round(float(v), 3) for v in share][::-1]},
        "holdout": pooled,
        "rounds": by_round,
        "coefficients": [{"feature": n, "label": dr._label_ko(n), "beta": round(b, 4), "gamma": round(g, 4)}
                         for n, b, g in zip(names, model["beta"], model["gamma"], strict=True)],
        "notes": [
            "한 세션의 회차들은 서로 독립이 아니다. 묶음 적중률의 95% 구간은 실제보다 좁다 — 회차별 표를 본다",
            "단계 경계는 남은 시간에 맞춘 ATR(ATR × √남은 비율)이다. 마감 가까운 회차가 '약보합' 으로 쉬워 보이지 않게",
            "장중 외국인 · 기관 수급(#579)은 과거 기록이 없어 확률에 넣지 않았다",
        ],
    }
    return model_file, report
