"""#572 today's direction call: stages, the cutoff (no future bar), the model and the backtest."""

import json
import math
import random
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from desk import day_call as dc
from desk import day_call_train as dct

UTC = ZoneInfo("UTC")
LONDON = ZoneInfo("Europe/London")
ZONES = {"^KS11": dc.KST, "KRW=X": LONDON}


def _bar(symbol: str, day: date, o: float, h: float, lo: float, c: float) -> SimpleNamespace:
    """Dated as Yahoo dates a daily bar: local midnight of its session, in UTC."""
    zone = ZONES.get(symbol, dc.ET)
    at = datetime.combine(day, time(0), zone).astimezone(UTC).isoformat()
    return SimpleNamespace(at=at, open=o, high=h, low=lo, close=c, volume=0)


def _series(days: int = 700, seed: int = 7) -> dict[str, list]:
    """A random walk per symbol on weekdays from 2023-01-02 (Korean bars carry a gentle pull from the
    US day before, so a model has something to find)."""
    rng = random.Random(seed)
    start = date(2023, 1, 2)
    weekdays = [d for d in (start + timedelta(i) for i in range(days * 7 // 5 + 10)) if d.weekday() < 5][:days]
    level = {"^KS11": 2500.0, "^GSPC": 4000.0, "^IXIC": 12000.0, "^VIX": 20.0, "KRW=X": 1300.0,
             "^TNX": 4.0, "DX-Y.NYB": 100.0}
    vol = {"^KS11": 0.01, "^GSPC": 0.01, "^IXIC": 0.012, "^VIX": 0.05, "KRW=X": 0.004, "^TNX": 0.02,
           "DX-Y.NYB": 0.003}
    out = {s: [] for s in level}
    us_prev = 0.0
    for d in weekdays:
        for s, now in list(level.items()):
            drift = 0.4 * us_prev if s == "^KS11" else 0.0
            o = now * (1 + rng.gauss(0, vol[s] / 3))
            c = o * (1 + drift * vol[s] / 0.01 * 0.01 + rng.gauss(0, vol[s]))
            h, lo = max(o, c) * (1 + abs(rng.gauss(0, vol[s] / 2))), min(o, c) * (1 - abs(rng.gauss(0, vol[s] / 2)))
            out[s].append(_bar(s, d, o, h, lo, c))
            if s == "^GSPC":
                us_prev = math.log(c / now) / vol[s]
            level[s] = c
    return out


SERIES = _series()


def test_stages_by_the_atr_edges():
    assert [dc.stage_of(z) for z in (1.01, 1.0, 0.31, 0.3, 0.0, -0.3, -0.31, -1.0, -1.01)] == [
        "strong_long", "weak_long", "weak_long", "flat", "flat", "flat", "weak_short", "weak_short", "strong_short"]
    assert dc.stage_of(0.8, (0.25, 0.75)) == "strong_long"


def test_percent_sums_to_exactly_100():
    for p in ([0.2] * 5, [1 / 3, 1 / 3, 1 / 3, 0, 0], [0.12345, 0.23456, 0.3456, 0.1, 0.19639], [0.001] * 5):
        got = dc.percent(p)
        assert round(sum(got.values()), 1) == 100.0 and all(round(v, 1) == v for v in got.values())
        assert list(got) == list(dc.STAGES)


def test_the_cutoff_reads_the_us_session_that_closed_before_the_korean_morning():
    day = date(2024, 3, 13)                             # a Wednesday; 07:30 KST is 18:30 ET on the 12th
    kr = dc.cutoff_of("kr", day, 1)
    gspc = dc.cut("^GSPC", SERIES["^GSPC"], kr)
    assert dc.session_day("^GSPC", gspc[-1]) == date(2024, 3, 12)
    ks = dc.cut("^KS11", SERIES["^KS11"], kr)
    assert dc.session_day("^KS11", ks[-1]) == date(2024, 3, 12)
    us = dc.cutoff_of("us", day, 1)                     # 09:00 ET: Seoul closed at 02:30 ET the same day
    assert dc.session_day("^KS11", dc.cut("^KS11", SERIES["^KS11"], us)[-1]) == day
    assert dc.session_day("^GSPC", dc.cut("^GSPC", SERIES["^GSPC"], us)[-1]) == date(2024, 3, 12)
    # the FX bar dated the 12th (London) is over at midnight London: after 07:30 KST on the 13th
    krw = dc.cut("KRW=X", SERIES["KRW=X"], kr)
    assert dc.session_day("KRW=X", krw[-1]) == date(2024, 3, 11)


@pytest.mark.parametrize(("market", "round_"), [("kr", 1), ("kr", 2), ("us", 1), ("us", 2)])
def test_no_feature_reads_a_bar_that_was_not_over_by_the_cutoff(market, round_):
    day = date(2024, 6, 12)
    full, _meta = dc.features(market, round_, day, SERIES)
    cutoff = dc.cutoff_of(market, day, round_)
    target = dc.MARKETS[market].target
    seen = {}
    for symbol, bars in SERIES.items():
        kept = [b for b in bars if dc.closed_at(symbol, b) <= cutoff]
        later = [SimpleNamespace(**{**vars(b), "high": b.high * 3, "low": b.low / 3, "close": b.close * 2})
                 for b in bars if dc.closed_at(symbol, b) > cutoff]
        if symbol == target and round_ == 1:
            later = [SimpleNamespace(**{**vars(b), "open": b.open * 5}) for b in later]   # nor the open yet
        seen[symbol] = kept + later
    assert dc.features(market, round_, day, seen)[0] == full
    assert ("gap" in full) == (round_ == 2)


def test_the_answer_is_open_to_close_in_the_atr_of_the_days_before():
    day = date(2024, 6, 12)
    a = dc.label("kr", day, SERIES)
    bars = SERIES["^KS11"]
    i = next(i for i, b in enumerate(bars) if dc.session_day("^KS11", b) == day)
    assert a["atr"] == dc.atr(bars[:i]) and a["z"] == (bars[i].close - bars[i].open) / a["atr"]
    assert a["stage"] == dc.stage_of(a["z"])
    assert dc.label("kr", date(2024, 6, 15), SERIES) is None           # a Saturday


def test_the_score_of_a_call():
    probs = {"strong_long": 10.0, "weak_long": 40.0, "flat": 30.0, "weak_short": 15.0, "strong_short": 5.0}
    up = {"open": 100.0, "close": 101.0, "z": 0.5, "stage": "weak_long", "return_pct": 1.0}
    s = dc.score(probs, up)
    assert s["hit_direction"] is True and s["hit_stage"] is True
    assert s["brier"] == round(0.1 ** 2 + 0.6 ** 2 + 0.3 ** 2 + 0.15 ** 2 + 0.05 ** 2, 4)
    down = {**up, "close": 99.0, "z": -1.2, "stage": "strong_short", "return_pct": -1.0}
    assert dc.score(probs, down)["hit_direction"] is False
    assert dc.score(probs, {**up, "close": 100.0, "z": 0.0, "stage": "flat"})["hit_direction"] is None


def test_the_analytic_gradient_matches_a_numeric_one():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(60, 3))
    z = X[:, 0] * 0.6 + rng.normal(scale=0.5, size=60)
    y = dct._class_index([dc.stage_of(v) for v in z])
    theta = rng.normal(scale=0.3, size=8)
    _loss, g = dct._loss_grad(theta, X, y, dc.EDGES, 0.05)
    num = np.zeros_like(theta)
    for i in range(len(theta)):
        d = np.zeros_like(theta)
        d[i] = 1e-6
        num[i] = (dct._loss_grad(theta + d, X, y, dc.EDGES, 0.05)[0] - dct._loss_grad(theta - d, X, y, dc.EDGES, 0.05)[0]) / 2e-6
    assert np.allclose(g, num, atol=1e-6)


def test_the_fit_finds_the_signal_and_is_the_same_twice():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(400, 2))
    z = 0.7 * X[:, 0] + rng.normal(scale=0.4, size=400) * np.exp(0.4 * X[:, 1])
    y = dct._class_index([dc.stage_of(v) for v in z])
    a = dct.fit(X, y, z, dc.EDGES, 0.01)
    assert a[1] > 0.3 and abs(a[2]) < 0.15 and a[5] > 0.15        # centre follows x0, spread follows x1
    assert np.array_equal(a, dct.fit(X, y, z, dc.EDGES, 0.01))
    P = dct._probs(a, X, dc.EDGES)
    assert np.allclose(P.sum(axis=1), 1) and (P >= 0).all()


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(dct, "STEPS", 150)
    monkeypatch.setattr(dct, "L2_GRID", (0.01, 0.1))
    monkeypatch.setattr(dct, "LAMBDA_GRID", (0.0, 0.3))
    monkeypatch.setattr(dct, "CV_YEARS", 1)


def test_the_backtest_holds_out_the_last_90_days_and_the_server_reads_the_same_numbers(quick):
    model, report = dct.backtest("kr", SERIES)
    r1 = report["rounds"]["1"]
    hold = r1["holdout"]
    assert hold["n"] > 50 and r1["train"]["to"] < hold["from"]
    assert set(hold["baselines"]) == {"always_long", "follow_yesterday", "climatology"}
    assert sum(s["actual"] for s in hold["per_stage"]) == hold["n"] and hold["calibration"]
    assert [s["stage"] for s in hold["per_stage"]] == list(dc.DISPLAY_ORDER)
    m = model["rounds"]["1"]
    assert m["trained_through"] == r1["train"]["to"] and model["version"].startswith("dc-")
    assert "gap" in model["rounds"]["2"]["features"] and "gap" not in m["features"]
    # the pure-Python reader gives the numpy model's probabilities
    rows = dct.rows("kr", 1, SERIES)[-5:]
    clim = np.array(m["climatology"])
    theta = np.array([m["beta0"], *m["beta"], m["gamma0"], *m["gamma"]])
    X = (dct._matrix(rows, m["features"]) - np.array(m["mean"])) / np.array(m["std"])
    expect = (1 - m["lambda"]) * dct._probs(theta, X, dc.EDGES) + m["lambda"] * clim
    assert np.allclose(dct._predict(m, rows), expect, atol=1e-12)
    assert json.loads(json.dumps(model)) == model                         # plain JSON


def test_a_call_from_the_committed_model_has_the_contract_shape(quick, tmp_path):
    model, _report = dct.backtest("us", SERIES)
    (tmp_path / "model_us.json").write_text(json.dumps(model), encoding="utf-8")
    loaded = dc.load_model("us", tmp_path)
    c = dc.call(loaded, "us", 2, date(2025, 9, 3), SERIES)
    assert list(c["probs"]) == list(dc.DISPLAY_ORDER) and round(sum(c["probs"].values()), 1) == 100.0
    assert c["label"] == dc.LABEL_KO[c["stage"]] and c["round_label"] == "2차" and c["direction"] in ("long", "short")
    assert len(c["reasons"]) == 5 and {r["effect"] for r in c["reasons"]} <= {"long", "short", "volatile", "calm"}
    assert c["edges_pct"]["strong"] > c["edges_pct"]["flat"] > 0
    assert dc.load_model("kr", tmp_path) is None and dc.today_call() is None


def test_the_cli_writes_the_model_and_the_report(quick, tmp_path, monkeypatch, capsys):
    from desk import cli

    monkeypatch.setattr(dct, "fetch", lambda candles=None: SERIES)
    assert cli.main(["day-call", "backtest", "--market", "kr", "--out-dir", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "model_kr.json").read_text(encoding="utf-8"))["market"] == "kr"
    report = json.loads((tmp_path / "report_kr.json").read_text(encoding="utf-8"))
    assert report["code"] == dct.code_hash() and "flow" in report["not_run"]
    assert "kr 1차" in capsys.readouterr().out
