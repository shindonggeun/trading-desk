"""Fit and backtest desk.day_call's model (#572) - offline, like desk.backtest: a local run fetches years
of Yahoo daily bars, fits a model per market and round on every session before the hold-out, and
scores the last HOLDOUT_DAYS calendar days once (the hold-out is never used to choose anything).

    python -m desk.cli day-call backtest --market all --out-dir desk/data/day_call

writes ``model_<market>.json`` (read by the server, plain Python) and ``report_<market>.json`` (the
numbers the PR and the stats page show). The model is the one fitted before the hold-out, so the
hold-out days can be backfilled as honest out-of-sample calls.

Fit: standardized features, L2, zero start, a fixed number of full-batch Adam steps in float64 - the
same bars give the same coefficients. The L2 weight and the blend with the training stage frequencies
(lambda) are chosen on expanding-window folds inside the training years (CV_YEARS, one year each).
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from . import day_call as dc

HISTORY_BARS = 2400           # ~9.5 years of daily bars
HOLDOUT_DAYS = 90
CV_YEARS = 3
L2_GRID = (0.003, 0.01, 0.03, 0.1)
LAMBDA_GRID = (0.0, 0.1, 0.2, 0.35, 0.5)
STEPS = 1500
LEARNING_RATE = 0.05
ALT_EDGES = (0.25, 0.75)
VERSION = 1


# ------------------------------------------------------------------ data

def fetch(candles: Callable | None = None) -> dict[str, list]:
    """Daily bars for every symbol the calls read (desk.prices.yahoo_candles)."""
    if candles is None:
        from .prices import yahoo_candles as candles
    return {s: [b for b in candles(s, "1d", HISTORY_BARS) if b.close] for s in dc.SYMBOLS}


def rows(market: str, round_: int, series: dict[str, Sequence], edges: tuple[float, float] = dc.EDGES) -> list[dict]:
    """One row per target session with its features (as of the round's cutoff) and its answer."""
    m = dc.MARKETS[market]
    days = sorted({dc.session_day(m.target, b) for b in series[m.target]})
    out, prev_dir = [], None
    for day in days:
        answer = dc.label(market, day, series)
        if answer is None:
            continue
        try:
            x, _meta = dc.features(market, round_, day, series)
        except dc.NoData:
            prev_dir = math.copysign(1, answer["z"]) if answer["z"] else None
            continue
        out.append({"day": day, "x": x, "z": answer["z"], "stage": dc.stage_of(answer["z"], edges),
                    "answer": answer, "prev_dir": prev_dir})
        prev_dir = math.copysign(1, answer["z"]) if answer["z"] else None
    return out


# ------------------------------------------------------------------ the ordinal model

def _class_index(stages: Sequence[str]) -> np.ndarray:
    return np.array([dc.STAGES.index(s) for s in stages])


def _probs(theta: np.ndarray, X: np.ndarray, edges: tuple[float, float]) -> np.ndarray:
    k = X.shape[1]
    mu = theta[0] + X @ theta[1:k + 1]
    s = np.exp(theta[k + 1] + X @ theta[k + 2:])
    e = np.array([-edges[1], -edges[0], edges[0], edges[1]])
    f = 1 / (1 + np.exp(-(e[None, :] - mu[:, None]) / s[:, None]))
    return np.column_stack([f[:, 0], f[:, 1] - f[:, 0], f[:, 2] - f[:, 1], f[:, 3] - f[:, 2], 1 - f[:, 3]])


def _loss_grad(theta: np.ndarray, X: np.ndarray, y: np.ndarray, edges: tuple[float, float], l2: float):
    n, k = X.shape
    mu = theta[0] + X @ theta[1:k + 1]
    logs = theta[k + 1] + X @ theta[k + 2:]
    s = np.exp(logs)
    e = np.array([-np.inf, -edges[1], -edges[0], edges[0], edges[1], np.inf])
    lo, hi = e[y], e[y + 1]                      # class y spans (lo, hi]

    def parts(edge):
        t = np.where(np.isfinite(edge), (edge - mu) / s, 0.0)
        f = np.where(np.isfinite(edge), 1 / (1 + np.exp(-t)), np.where(edge > 0, 1.0, 0.0))
        d = np.where(np.isfinite(edge), f * (1 - f), 0.0)
        return t, f, d

    t_hi, f_hi, d_hi = parts(hi)
    t_lo, f_lo, d_lo = parts(lo)
    p = np.clip(f_hi - f_lo, 1e-12, None)
    dp_dmu = (-d_hi + d_lo) / s
    dp_dlogs = -(d_hi * t_hi - d_lo * t_lo)
    w_mu, w_s = -dp_dmu / p / n, -dp_dlogs / p / n
    grad = np.concatenate([[w_mu.sum()], X.T @ w_mu, [w_s.sum()], X.T @ w_s])
    reg = np.concatenate([[0.0], theta[1:k + 1], [0.0], theta[k + 2:]])
    loss = -np.log(p).mean() + l2 * float(reg @ reg)
    return loss, grad + 2 * l2 * reg


def fit(X: np.ndarray, y: np.ndarray, z: np.ndarray, edges: tuple[float, float], l2: float) -> np.ndarray:
    """Adam on the ordinal log-loss; starts at the centre 0 and the spread of the training z."""
    k = X.shape[1]
    theta = np.zeros(2 * k + 2)
    theta[k + 1] = math.log(max(float(np.std(z)), 1e-3) * math.sqrt(3) / math.pi)
    m1, m2 = np.zeros_like(theta), np.zeros_like(theta)
    b1, b2 = 0.9, 0.999
    for step in range(1, STEPS + 1):
        _loss, g = _loss_grad(theta, X, y, edges, l2)
        m1 = b1 * m1 + (1 - b1) * g
        m2 = b2 * m2 + (1 - b2) * g * g
        theta -= LEARNING_RATE * (m1 / (1 - b1 ** step)) / (np.sqrt(m2 / (1 - b2 ** step)) + 1e-8)
    return theta


def _matrix(rs: Sequence[dict], names: Sequence[str]) -> np.ndarray:
    return np.array([[r["x"][n] for n in names] for r in rs], dtype=np.float64)


def _standardize(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean, std = X.mean(axis=0), X.std(axis=0)
    return mean, np.where(std > 1e-9, std, 1.0)


def _climatology(y: np.ndarray) -> np.ndarray:
    counts = np.bincount(y, minlength=5).astype(np.float64) + 0.5    # a half count: no stage is impossible
    return counts / counts.sum()


def _brier(P: np.ndarray, y: np.ndarray) -> float:
    onehot = np.eye(5)[y]
    return float(((P - onehot) ** 2).sum(axis=1).mean())


def _fit_model(train: Sequence[dict], names: Sequence[str], edges, l2: float, lam: float) -> dict:
    X = _matrix(train, names)
    mean, std = _standardize(X)
    y = _class_index([r["stage"] for r in train])
    theta = fit((X - mean) / std, y, np.array([r["z"] for r in train]), edges, l2)
    k = len(names)
    return {"features": list(names), "mean": mean.tolist(), "std": std.tolist(), "beta0": float(theta[0]),
            "beta": theta[1:k + 1].tolist(), "gamma0": float(theta[k + 1]), "gamma": theta[k + 2:].tolist(),
            "edges": list(edges), "lambda": lam, "l2": l2, "climatology": _climatology(y).tolist(),
            "trained_from": train[0]["day"].isoformat(), "trained_through": train[-1]["day"].isoformat(),
            "n_train": len(train)}


def _predict(model: dict, rs: Sequence[dict]) -> np.ndarray:
    return np.array([dc.raw_probs(model, r["x"]) for r in rs])


def select(train: Sequence[dict], names: Sequence[str], edges) -> tuple[float, float, dict]:
    """L2 and lambda by the mean Brier of expanding-window folds: each of the last CV_YEARS training
    years scored by a fit on the years before it."""
    last_year = train[-1]["day"].year
    folds = []
    for year in range(last_year - CV_YEARS + 1, last_year + 1):
        fit_rows = [r for r in train if r["day"].year < year]
        test_rows = [r for r in train if r["day"].year == year]
        if len(fit_rows) > 250 and test_rows:
            folds.append((fit_rows, test_rows))
    scores: dict[tuple[float, float], list[float]] = {}
    for l2 in L2_GRID:
        for fit_rows, test_rows in folds:
            model = _fit_model(fit_rows, names, edges, l2, 0.0)
            P = _predict(model, test_rows)
            clim = np.array(model["climatology"])
            y = _class_index([r["stage"] for r in test_rows])
            for lam in LAMBDA_GRID:
                scores.setdefault((l2, lam), []).append(_brier((1 - lam) * P + lam * clim, y))
    mean = {key: float(np.mean(v)) for key, v in scores.items()}
    l2, lam = min(mean, key=lambda key: (round(mean[key], 6), -key[1], -key[0]))
    return l2, lam, {"folds": [f[1][0]["day"].year for f in folds], "brier": round(mean[(l2, lam)], 4)}


# ------------------------------------------------------------------ scoring

def metrics(P: np.ndarray, rs: Sequence[dict], clim: np.ndarray) -> dict:
    """Hit rates (direction and exact stage) with a 95% interval for the direction, Brier against
    climatology, the calibration table, per-stage counts and the baselines on the same sessions."""
    n = len(rs)
    if n == 0:
        return {"n": 0}
    y = _class_index([r["stage"] for r in rs])
    up = P[:, 3] + P[:, 4]
    down = P[:, 0] + P[:, 1]
    actual = np.array([math.copysign(1, r["z"]) if r["z"] else 0 for r in rs])
    called = np.where(up >= down, 1, -1)
    moved = actual != 0
    dir_hit = float((called[moved] == actual[moved]).mean())
    se = math.sqrt(dir_hit * (1 - dir_hit) / max(moved.sum(), 1))
    top = P.argmax(axis=1)
    clim_P = np.tile(clim, (n, 1))
    prev = np.array([r["prev_dir"] or 0 for r in rs])
    follow = moved & (prev != 0)
    calib = []
    flat_p, flat_y = P.ravel() * 100, np.eye(5)[y].ravel()
    for lo in range(0, 100, 10):
        mask = (flat_p >= lo) & ((flat_p < lo + 10) if lo < 90 else (flat_p <= 100))
        if mask.any():
            calib.append({"lo": lo, "hi": lo + 10, "n": int(mask.sum()), "mean_prob": round(float(flat_p[mask].mean()), 1),
                          "actual_rate": round(float(flat_y[mask].mean() * 100), 1)})
    return {
        "n": n, "from": rs[0]["day"].isoformat(), "to": rs[-1]["day"].isoformat(),
        "direction_hit_rate": round(dir_hit, 3),
        "direction_ci95": [round(dir_hit - 1.96 * se, 3), round(dir_hit + 1.96 * se, 3)],
        "stage_hit_rate": round(float((top == y).mean()), 3),
        "brier": round(_brier(P, y), 4),
        "log_loss": round(float(-np.log(np.clip(P[np.arange(n), y], 1e-12, None)).mean()), 4),
        "baselines": {
            "always_long": {"direction_hit_rate": round(float((actual[moved] > 0).mean()), 3)},
            "follow_yesterday": {"direction_hit_rate": round(float((prev[follow] == actual[follow]).mean()), 3)
                                 if follow.any() else None},
            "climatology": {"brier": round(_brier(clim_P, y), 4),
                            "stage_hit_rate": round(float((clim.argmax() == y).mean()), 3)},
        },
        "brier_skill": round(1 - _brier(P, y) / _brier(clim_P, y), 4),
        "per_stage": [{"stage": s, "label": dc.LABEL_KO[s], "called": int((top == i).sum()),
                       "called_hit": int(((top == i) & (y == i)).sum()), "actual": int((y == i).sum())}
                      for i, s in enumerate(dc.STAGES)][::-1],
        "calibration": calib,
    }


def _version(models: dict) -> str:
    blob = json.dumps(models, sort_keys=True).encode()
    through = min(m["trained_through"] for m in models.values())
    return f"dc-{through.replace('-', '')}-{hashlib.sha256(blob).hexdigest()[:8]}"


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True,
                              check=True, cwd=Path(__file__).parent).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def code_hash() -> str:
    """The model code's fingerprint (this module and desk.day_call): a report from other code is stale."""
    here = Path(__file__).parent
    blob = b"".join((here / f).read_bytes().replace(b"\r\n", b"\n") for f in ("day_call.py", "day_call_train.py"))
    return hashlib.sha256(blob).hexdigest()[:12]


def backtest(market: str, series: dict[str, Sequence], holdout_days: int = HOLDOUT_DAYS,
             start: date | None = None, progress: Callable[[str], None] | None = None) -> tuple[dict, dict]:
    """The model file (both rounds, fitted before the hold-out) and the report for ``market``."""
    say = progress or (lambda _msg: None)
    models, report_rounds = {}, {}
    for round_ in sorted(dc.MARKETS[market].rounds):
        rs = [r for r in rows(market, round_, series) if start is None or r["day"] >= start]
        if not rs:
            raise ValueError(f"{market} round {round_}: no rows")
        holdout_from = rs[-1]["day"] - timedelta(days=holdout_days)
        train = [r for r in rs if r["day"] <= holdout_from]
        hold = [r for r in rs if r["day"] > holdout_from]
        names = [n for n in dc.FEATURES if n in rs[0]["x"]]
        say(f"{market} round {round_}: {len(train)} training sessions, {len(hold)} held out")
        l2, lam, cv = select(train, names, dc.EDGES)
        model = _fit_model(train, names, dc.EDGES, l2, lam)
        clim = np.array(model["climatology"])
        P = _predict(model, hold)
        # the same choice of L2 and lambda on the other edges, refitted: how the stage counts move
        alt_rows = [{**r, "stage": dc.stage_of(r["z"], ALT_EDGES)} for r in rs]
        alt_train = [r for r in alt_rows if r["day"] <= holdout_from]
        alt_hold = [r for r in alt_rows if r["day"] > holdout_from]
        alt = _fit_model(alt_train, names, ALT_EDGES, l2, lam)
        models[str(round_)] = model
        report_rounds[str(round_)] = {
            "features": names, "l2": l2, "lambda": lam, "cv": cv,
            "train": {"n": len(train), "from": train[0]["day"].isoformat(), "to": train[-1]["day"].isoformat(),
                      "stage_share": [round(v, 3) for v in np.bincount(_class_index([r["stage"] for r in train]),
                                                                       minlength=5) / len(train)][::-1]},
            "holdout": metrics(P, hold, clim),
            "holdout_alt_edges": {"edges": list(ALT_EDGES),
                                  **metrics(_predict(alt, alt_hold), alt_hold, np.array(alt["climatology"]))},
            "coefficients": [{"feature": n, "label": dc.FEATURES[n][0], "beta": round(b, 4), "gamma": round(g, 4)}
                             for n, b, g in zip(names, model["beta"], model["gamma"], strict=True)],
        }
    version = _version(models)
    model_file = {"version": version, "market": market, "format": VERSION, "code": code_hash(), "rounds": models}
    report = {"version": version, "market": market, "target": dc.MARKETS[market].target, "commit": _commit(),
              "code": code_hash(), "holdout_days": holdout_days, "edges": list(dc.EDGES), "rounds": report_rounds,
              "not_run": {"flow": "투자자별 수급 기록(flow-history)은 서버에만 있어 이 실행에 넣지 않음",
                          "news": "GDELT 뉴스 톤 기록이 이 PC 에 거의 없어(2개 파일) 넣지 않음",
                          "live_only": "야간선물 · 선물 수급 · VKOSPI · 커뮤니티는 과거 기록이 없어 모델 밖(context)"}}
    return model_file, report
