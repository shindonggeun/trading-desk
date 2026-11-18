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
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from . import day_call as dc

HISTORY_BARS = 3100           # daily bars back to 2014 (#604): the ATR's 120 sessions before TRAIN_FROM
TRAIN_FROM = date(2015, 1, 1)  # the first session a model is fitted on (``start`` overrides)
HOLDOUT_DAYS = 90
CV_YEARS = 3
L2_GRID = (0.003, 0.01, 0.03, 0.1)
LAMBDA_GRID = (0.0, 0.1, 0.2, 0.35, 0.5)
STEPS = 1500
LEARNING_RATE = 0.05
FALLBACK = (0.1, 0.35)        # L2 and lambda when no fold fits: the most shrunk the folds ever chose
ALT_EDGES = (0.25, 0.75)
VERSION = 1


# ------------------------------------------------------------------ data

HOURLY_BARS = 12000           # Yahoo's 60-minute bars reach back about two years
MARGINS = (0.0, 0.05, 0.10, 0.15, 0.20)   # |P(long) - P(short)| a call needs not to be "판단 보류"
BALANCED = (0.2, 0.7)         # |z| quantiles for edges fitted to the training spread: 20% flat, 30% strong


def fetch(candles: Callable | None = None, flow_dir: str | None = None) -> dict[str, list]:
    """Daily bars for every symbol the calls read, hourly futures bars (desk.prices.yahoo_candles) and,
    with ``flow_dir``, the KOSPI investor flow file (desk.flow_history, collected on the server)."""
    if candles is None:
        from .prices import yahoo_candles as candles
    out = {s: [b for b in candles(s, "1d", HISTORY_BARS) if b.close] for s in dc.SYMBOLS}
    for key in dc.HOURLY:
        out[key] = [b for b in candles(key.split("@")[0], "60m", HOURLY_BARS) if b.close]
    if flow_dir:
        from . import flow_history

        data = flow_history.load(flow_history.path_for(flow_dir, "KOSPI"))
        out[dc.FLOW] = (data or {}).get("rows") or {}
    return out


def rows(market: str, round_: int, series: dict[str, Sequence], edges: tuple[float, float] = dc.EDGES,
         kind: str = "oc", extras: Sequence[str] = (), now: datetime | None = None) -> list[dict]:
    """One row per finished target session (at ``now``) with its features (as of the round's cutoff)
    and its answer (``kind``: open or previous close to close)."""
    m = dc.MARKETS[market]
    days = sorted({dc.session_day(m.target, b) for b in dc.answer_bars(market, series)})
    out, prev_dir = [], None
    for day in days:
        answer = dc.label(market, day, series, kind, now)
        if answer is None:
            prev_dir = None
            continue
        try:
            x, _meta = dc.features(market, round_, day, series, extras)
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


def _fit_model(train: Sequence[dict], names: Sequence[str], edges, l2: float, lam: float,
               kind: str = "oc", extras: Sequence[str] = ()) -> dict:
    X = _matrix(train, names)
    mean, std = _standardize(X)
    y = _class_index([r["stage"] for r in train])
    theta = fit((X - mean) / std, y, np.array([r["z"] for r in train]), edges, l2)
    k = len(names)
    return {"features": list(names), "mean": mean.tolist(), "std": std.tolist(), "beta0": float(theta[0]),
            "beta": theta[1:k + 1].tolist(), "gamma0": float(theta[k + 1]), "gamma": theta[k + 2:].tolist(),
            "edges": list(edges), "lambda": lam, "l2": l2, "climatology": _climatology(y).tolist(),
            "trained_from": train[0]["day"].isoformat(), "trained_through": train[-1]["day"].isoformat(),
            "n_train": len(train), "label": kind, "extras": list(extras)}


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
    if not scores:                               # too short a history for a fold (the hourly futures)
        return FALLBACK[0], FALLBACK[1], {"folds": [], "brier": None}
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
    # scored as the service shows and scores them: % with one decimal (dc.percent), ties to the long side;
    # the log loss reads the unrounded ones (a shown 0.0% is not a zero probability)
    raw, P = P, np.array([list(dc.percent(p).values()) for p in P]) / 100
    y = _class_index([r["stage"] for r in rs])
    up = P[:, 3] + P[:, 4]
    down = P[:, 0] + P[:, 1]
    actual = np.array([math.copysign(1, r["z"]) if r["z"] else 0 for r in rs])
    called = np.where(up >= down, 1, -1)
    moved = actual != 0
    if not moved.any():
        return {"n": n}
    dir_hit = float((called[moved] == actual[moved]).mean())
    se = math.sqrt(dir_hit * (1 - dir_hit) / moved.sum())
    top = 4 - P[:, ::-1].argmax(axis=1)              # a tie goes to the long side, as dc.top_stage
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
        "called_long_share": round(float((called[moved] > 0).mean()), 3),
        "direction_ci95": [round(dir_hit - 1.96 * se, 3), round(dir_hit + 1.96 * se, 3)],
        "stage_hit_rate": round(float((top == y).mean()), 3),
        "brier": round(_brier(P, y), 4),
        "log_loss": round(float(-np.log(np.clip(raw[np.arange(n), y], 1e-12, None)).mean()), 4),
        "margins": [{"margin": m, "coverage": round(float((cover := moved & (np.abs(up - down) >= m)).sum()
                                                             / max(moved.sum(), 1)), 3),
                     "direction_hit_rate": round(float((called[cover] == actual[cover]).mean()), 3) if cover.any() else None}
                    for m in MARGINS],
        "baselines": {
            "always_long": {"direction_hit_rate": round(float((actual[moved] > 0).mean()), 3)},
            "always_short": {"direction_hit_rate": round(float((actual[moved] < 0).mean()), 3)},
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
    """From the coefficients rounded to 9 digits: another machine's BLAS may differ in the last bits."""
    def rounded(v):
        if isinstance(v, float):
            return round(v, 9)
        if isinstance(v, list):
            return [rounded(x) for x in v]
        if isinstance(v, dict):
            return {k: rounded(x) for k, x in v.items()}
        return v

    blob = json.dumps(rounded(models), sort_keys=True).encode()
    through = min(r["trained_through"] for c in models.values() for r in c["rounds"].values())
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
    blob = b"".join((here / f).read_bytes().replace(b"\r\n", b"\n")
                    for f in ("day_call.py", "day_call_train.py", "day_call_krx.py"))
    return hashlib.sha256(blob).hexdigest()[:12]


def holdout_start(market: str, series: dict[str, Sequence], holdout_days: int = HOLDOUT_DAYS) -> date:
    """The first held-out day: the same for every variant, so their scores are on the same sessions."""
    m = dc.MARKETS[market]
    last = max(dc.session_day(m.target, b) for b in series[m.target])
    return last - timedelta(days=holdout_days) + timedelta(days=1)


def fit_round(market: str, round_: int, series: dict[str, Sequence], holdout_from: date, *, kind: str = "oc",
              extras: Sequence[str] = (), edges: tuple[float, float] | None = None,
              start: date | None = None) -> tuple[dict, dict]:
    """One market and round: L2 and lambda from the training folds, the model fitted on every session
    from ``start`` (default TRAIN_FROM) before ``holdout_from``, and its scores on the sessions from it.
    ``edges`` None keeps dc.EDGES; "balanced" fits them to the training |z| (BALANCED quantiles). A
    feature group with a shorter history (night: Yahoo's 60-minute bars, about two years) trains on
    the sessions it has."""
    start = start or TRAIN_FROM
    rs = [r for r in rows(market, round_, series, dc.EDGES, kind, extras) if start is None or r["day"] >= start]
    train_z = np.abs([r["z"] for r in rs if r["day"] < holdout_from])
    if len(train_z) < 100:
        raise ValueError(f"{market} round {round_} {kind} {list(extras)}: {len(train_z)} training sessions")
    if edges == "balanced":
        edges = tuple(round(float(np.quantile(train_z, q)), 3) for q in BALANCED)
    edges = edges or dc.EDGES
    rs = [{**r, "stage": dc.stage_of(r["z"], edges)} for r in rs]
    train = [r for r in rs if r["day"] < holdout_from]
    hold = [r for r in rs if r["day"] >= holdout_from]
    names = [n for n in dc.FEATURES if n in rs[0]["x"]]
    l2, lam, cv = select(train, names, edges)
    model = _fit_model(train, names, edges, l2, lam, kind, extras)
    clim = np.array(model["climatology"])
    share = np.bincount(_class_index([r["stage"] for r in train]), minlength=5) / len(train)
    return model, {
        "label": kind, "extras": list(extras), "edges": list(edges), "features": names, "l2": l2, "lambda": lam,
        "cv": cv,
        "train": {"n": len(train), "from": train[0]["day"].isoformat(), "to": train[-1]["day"].isoformat(),
                  "stage_share": [round(float(v), 3) for v in share][::-1]},
        "holdout": metrics(_predict(model, hold), hold, clim),
        "coefficients": [{"feature": n, "label": dc.FEATURES[n][0], "beta": round(b, 4), "gamma": round(g, 4)}
                         for n, b, g in zip(names, model["beta"], model["gamma"], strict=True)],
    }


def variants(market: str) -> list[dict]:
    """The label × feature-group combinations the report compares (flow only for the KOSPI)."""
    groups: list[tuple[str, ...]] = [(), ("night",)]
    if market == "kr":
        groups += [("flow",), ("flow", "night")]
    out = [{"kind": k, "extras": g, "edges": None} for k in dc.LABELS for g in groups]
    return out + [{"kind": "oc", "extras": (), "edges": ALT_EDGES}, {"kind": "oc", "extras": (), "edges": "balanced"}]


def _compact(rep: dict) -> dict:
    h = rep["holdout"]
    keep = ("n", "from", "to", "direction_hit_rate", "direction_ci95", "stage_hit_rate", "brier", "brier_skill",
            "margins", "baselines", "per_stage")
    return {"label": rep["label"], "extras": rep["extras"], "edges": rep["edges"], "l2": rep["l2"],
            "lambda": rep["lambda"], "train_n": rep["train"]["n"], "train_from": rep["train"]["from"],
            "stage_share": rep["train"]["stage_share"], **{k: h[k] for k in keep if k in h}}


NOTES = {
    "selection": "판정 정의(전일 종가 대비 + 야간 선물)는 같은 검증 90일의 변형 비교를 보고 골랐다 — 숫자가 조금 낙관적일 수 있다",
    "night": "야간 선물 특징은 야후 60분봉이 약 2년뿐이라 학습이 1.4~1.8년이다",
    "gap": "2차의 전일 종가 대비 적중에는 이미 찍힌 시가 갭 몫이 크다",
}


def backtest(market: str, series: dict[str, Sequence], holdout_days: int = HOLDOUT_DAYS,
             start: date | None = None, progress: Callable[[str], None] | None = None, *,
             calls: dict[str, tuple[str, tuple[str, ...]]] | None = None,
             compare: bool = True, krx_check: dict | None = None) -> tuple[dict, dict]:
    """The model file and the report for ``market``: every call in ``calls`` (default dc.CALLS: name ->
    answer kind and feature groups) for both rounds, fitted before the hold-out and scored on it;
    ``compare`` adds every variant scored on the same hold-out. ``krx_check`` (desk.day_call_krx.apply on
    the series) says whose KOSPI bars the answers are."""
    say = progress or (lambda _msg: None)
    calls = calls or dc.CALLS
    holdout_from = holdout_start(market, series, holdout_days)
    files, reports, table = {}, {}, []
    for name, (kind, extras) in calls.items():
        files[name] = {"label": kind, "extras": list(extras), "rounds": {}}
        reports[name] = {"label": kind, "extras": list(extras), "rounds": {}}
        for round_ in sorted(dc.MARKETS[market].rounds):
            say(f"{market} {name} round {round_}: {kind} {list(extras)}")
            model, rep = fit_round(market, round_, series, holdout_from, kind=kind, extras=extras, start=start)
            files[name]["rounds"][str(round_)] = model
            reports[name]["rounds"][str(round_)] = rep
    for round_ in sorted(dc.MARKETS[market].rounds):
        for v in variants(market) if compare else ():
            if "flow" in v["extras"] and dc.FLOW not in series:
                continue
            try:
                _m, other = fit_round(market, round_, series, holdout_from, kind=v["kind"], extras=v["extras"],
                                      edges=v["edges"], start=start)
            except ValueError as exc:
                say(f"  skip {v}: {exc}")
                continue
            say(f"  {v['kind']} {list(v['extras'])} edges={other['edges']}: dir "
                f"{other['holdout'].get('direction_hit_rate')} brier skill {other['holdout'].get('brier_skill')}")
            table.append({"round": round_, **_compact(other)})
    version = _version(files)
    model_file = {"version": version, "market": market, "format": VERSION, "code": code_hash(), "calls": files}
    not_run = {"news": "GDELT 뉴스 톤 기록이 이 PC 에 거의 없어(2개 파일) 넣지 않음",
               "live_only": "KIS 야간선물 · 선물 수급 · VKOSPI · 커뮤니티는 과거 기록이 없어 모델 밖(context)"}
    if dc.FLOW not in series and market == "kr":
        not_run["flow"] = "투자자별 수급 파일(--flow-dir)을 주지 않아 넣지 않음"
    report = {"version": version, "market": market, "target": dc.MARKETS[market].target, "commit": _commit(),
              "code": code_hash(), "holdout_days": holdout_days, "holdout_from": holdout_from.isoformat(),
              "edges": list(dc.EDGES), "hold_margin": dc.HOLD_MARGIN, "calls": reports, "variants": table,
              "notes": NOTES, "not_run": not_run, "answers": answers(krx_check)}
    return model_file, report


def answers(check: dict | None, keep: int = 40) -> dict:
    """#604 the report's word on the KOSPI answers: KRX's bars (how many days, where Yahoo differs) or
    Yahoo's only."""
    if not check or check.get("source") != "krx":
        return {"kospi": "yahoo", "note": "KRX 일봉(krx_daily)을 주지 않아 국장 정답 · 특징은 야후 ^KS11 이다"}
    return {"kospi": "krx", "used_for": "정답만(특징은 야후 ^KS11)", "krx_days": check["krx_days"], "yahoo_only_days": check["yahoo_only_days"],
            "from": check["from"], "to": check["to"], "diff_pct": check["diff_pct"],
            "diff_days": len(check["diffs"]), "diffs": check["diffs"][:keep],
            "only_yahoo": check["only_yahoo"][:keep], "only_krx": check["only_krx"][:keep]}
