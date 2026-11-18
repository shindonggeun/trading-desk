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


def _hourly(seed: int, start: datetime, hours: int, level: float) -> list[SimpleNamespace]:
    rng = random.Random(seed)
    out = []
    for h in range(hours):
        at = start + timedelta(hours=h)
        o = level
        level *= 1 + rng.gauss(0, 0.002)
        out.append(SimpleNamespace(at=at.isoformat(), open=o, high=max(o, level), low=min(o, level), close=level,
                                   volume=0))
    return out


def _flow_rows(days: list[date], seed: int = 3) -> dict:
    rng = random.Random(seed)
    return {d.isoformat(): {"foreigner": rng.gauss(0, 5e11), "institution": rng.gauss(0, 3e11)} for d in days}


SERIES = _series()
_FIRST = datetime(2023, 1, 1, tzinfo=UTC)
SERIES["ES=F@60m"] = _hourly(11, _FIRST, 24 * 1000, 4000.0)
SERIES["NQ=F@60m"] = _hourly(12, _FIRST, 24 * 1000, 12000.0)
SERIES[dc.FLOW] = _flow_rows([dc.session_day("^KS11", b) for b in SERIES["^KS11"]])


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
    extras = ("flow", "night")
    full, _meta = dc.features(market, round_, day, SERIES, extras)
    assert {"es_night", "nq_night"} <= set(full) and (("frn1" in full) == (market == "kr"))
    cutoff = dc.cutoff_of(market, day, round_)
    target = dc.MARKETS[market].target
    # a flow row is final the evening of its day: the call's own day and later are not read
    seen = {dc.FLOW: {d: ({k: v * 7 for k, v in row.items()} if d >= day.isoformat() else row)
                      for d, row in SERIES[dc.FLOW].items()}}
    for symbol, bars in SERIES.items():
        if symbol == dc.FLOW:
            continue
        kept = [b for b in bars if dc.closed_at(symbol, b) <= cutoff]
        later = [SimpleNamespace(**{**vars(b), "high": b.high * 3, "low": b.low / 3, "close": b.close * 2})
                 for b in bars if dc.closed_at(symbol, b) > cutoff]
        if symbol == target and round_ == 1:
            later = [SimpleNamespace(**{**vars(b), "open": b.open * 5}) for b in later]   # nor the open yet
        seen[symbol] = kept + later
    assert dc.features(market, round_, day, seen, extras)[0] == full
    assert ("gap" in full) == (round_ == 2)


def test_the_night_move_runs_from_the_last_session_close_to_the_cutoff():
    day = date(2024, 6, 12)                              # KR: since 15:30 KST on the 11th, to 07:30 KST
    x, meta = dc.features("kr", 1, day, SERIES, ("night",))
    es = SERIES["ES=F@60m"]
    since = datetime(2024, 6, 11, 15, 30, tzinfo=dc.KST)
    then = [b for b in es if dc.closed_at("ES=F@60m", b) <= since][-1]
    now = [b for b in es if dc.closed_at("ES=F@60m", b) <= meta["cutoff"]][-1]
    assert x["es_night"] == pytest.approx(math.log(now.close / then.close) / (meta["atr_pct"] / 100))
    with pytest.raises(dc.NoData):                       # no hourly bars that far back
        dc.features("kr", 1, date(2023, 1, 2) + timedelta(days=40), {**SERIES, "ES=F@60m": es[-100:]}, ("night",))


def test_the_close_to_close_answer_includes_the_gap():
    day = date(2024, 6, 12)
    oc, cc = dc.label("kr", day, SERIES, "oc"), dc.label("kr", day, SERIES, "cc")
    bars = SERIES["^KS11"]
    i = next(i for i, b in enumerate(bars) if dc.session_day("^KS11", b) == day)
    assert cc["open"] == bars[i - 1].close and oc["open"] == bars[i].open and cc["close"] == oc["close"]
    assert cc["z"] == pytest.approx((bars[i].close - bars[i - 1].close) / cc["atr"])


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
    assert set(model["calls"]) == set(dc.CALLS) == set(report["calls"]) and set(report["notes"]) >= {"selection", "night"}
    assert model["calls"]["prev_close"]["label"] == "cc" and model["calls"]["prev_close"]["extras"] == ["night"]
    r1 = report["calls"]["open"]["rounds"]["1"]
    hold = r1["holdout"]
    assert hold["n"] > 50 and r1["train"]["to"] < report["holdout_from"] <= hold["from"]
    assert set(hold["baselines"]) == {"always_long", "always_short", "follow_yesterday", "climatology"}
    moved = hold["baselines"]["always_long"]["direction_hit_rate"] + hold["baselines"]["always_short"]["direction_hit_rate"]
    assert moved == pytest.approx(1, abs=0.002)
    # the wider the margin, the fewer calls made (the rest is "판단 보류")
    cover = [m["coverage"] for m in hold["margins"]]
    assert cover[0] == 1.0 and cover == sorted(cover, reverse=True)
    # every variant on the same held-out sessions: both answers, the flow and night groups, other edges
    table = report["variants"]
    assert {(v["label"], tuple(v["extras"])) for v in table} >= {
        ("oc", ()), ("cc", ()), ("oc", ("night",)), ("oc", ("flow",)), ("cc", ("flow", "night"))}
    assert all(v["from"] >= report["holdout_from"] for v in table)
    balanced = [v for v in table if v["edges"] not in (list(dc.EDGES), list(dct.ALT_EDGES))]
    assert balanced and balanced[0]["stage_share"][2] == pytest.approx(0.2, abs=0.03)   # flat ~20% of training
    assert sum(s["actual"] for s in hold["per_stage"]) == hold["n"] and hold["calibration"]
    assert [s["stage"] for s in hold["per_stage"]] == list(dc.DISPLAY_ORDER)
    m = model["calls"]["open"]["rounds"]["1"]
    assert m["trained_through"] == r1["train"]["to"] and model["version"].startswith("dc-")
    assert "gap" in model["calls"]["open"]["rounds"]["2"]["features"] and "gap" not in m["features"]
    assert "es_night" in model["calls"]["prev_close"]["rounds"]["1"]["features"]
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
    assert c["call"] == "prev_close" and c["measured_from"] == "prev_close" and not c["fallback"]
    assert c["verdict"] == c["direction"]                                # no hold on the prev-close call
    o = dc.call(loaded, "us", 2, date(2025, 9, 3), SERIES, "open")
    up = o["probs"]["strong_long"] + o["probs"]["weak_long"]
    down = o["probs"]["strong_short"] + o["probs"]["weak_short"]
    assert o["measured_from"] == "open" and o["verdict"] == ("hold" if abs(up - down) < dc.HOLD_MARGIN else o["direction"])
    # no hourly futures (a Korean Monday morning): the plain model stands in and says so
    bare = dc.call(loaded, "us", 2, date(2025, 9, 3), {**SERIES, "ES=F@60m": []})
    assert bare["fallback"] and "es_night" not in bare["features"]
    assert list(c["probs"]) == list(dc.DISPLAY_ORDER) and round(sum(c["probs"].values()), 1) == 100.0
    assert c["label"] == dc.LABEL_KO[c["stage"]] and c["round_label"] == "2차" and c["direction"] in ("long", "short")
    assert len(c["reasons"]) == 5 and {r["effect"] for r in c["reasons"]} <= {"long", "short", "volatile", "calm"}
    assert c["edges_pct"]["strong"] > c["edges_pct"]["flat"] > 0
    assert dc.load_model("kr", tmp_path) is None and dc.today_call() is None


def test_the_cli_writes_the_model_and_the_report(quick, tmp_path, monkeypatch, capsys):
    from desk import cli

    monkeypatch.setattr(dct, "fetch", lambda candles=None, flow_dir=None: SERIES)
    assert cli.main(["day-call", "backtest", "--market", "kr", "--out-dir", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "model_kr.json").read_text(encoding="utf-8"))["market"] == "kr"
    report = json.loads((tmp_path / "report_kr.json").read_text(encoding="utf-8"))
    assert report["code"] == dct.code_hash() and "news" in report["not_run"] and "flow" not in report["not_run"]
    assert "kr 1차" in capsys.readouterr().out


def test_a_session_still_in_progress_is_no_answer():
    bars = SERIES["^KS11"]
    last = bars[-1]
    day = dc.session_day("^KS11", last)
    during = datetime.combine(day, time(11, 0), dc.KST)          # Yahoo already has the day's bar at 11:00
    assert dc.label("kr", day, SERIES, now=during) is None
    assert dc.label("kr", day, SERIES, now=during + timedelta(hours=5)) is not None
    assert all(r["day"] < day for r in dct.rows("kr", 1, SERIES, now=during))


def test_the_held_out_sessions_choose_nothing(quick):
    model, report = dct.backtest("us", SERIES, compare=False)
    start = date.fromisoformat(report["holdout_from"])
    moved = {**SERIES, "^GSPC": [SimpleNamespace(**{**vars(b), "close": b.close * (1.03 if i % 2 else 0.97)})
                                  if dc.session_day("^GSPC", b) >= start else b
                                  for i, b in enumerate(SERIES["^GSPC"])]}
    again, other = dct.backtest("us", moved, compare=False)
    assert again["calls"] == model["calls"]
    assert other["calls"]["open"]["rounds"]["1"]["holdout"] != report["calls"]["open"]["rounds"]["1"]["holdout"]


def test_no_second_call_on_a_ten_oclock_opening():
    assert dc.kr_late_open(date(2026, 11, 19)) and dc.kr_late_open(date(2026, 1, 2))
    assert not dc.kr_late_open(date(2026, 1, 5)) and not dc.kr_late_open(date(2026, 11, 18))
    day = date(2024, 11, 14)                                     # the 2024 CSAT, inside the test series
    with pytest.raises(dc.NoData):
        dc.features("kr", 2, day, SERIES)
    assert dc.features("kr", 1, day, SERIES)[0]                  # the 07:30 call does not need the open


def test_daylight_saving_moves_what_the_korean_morning_reads():
    # summer (BST, EDT): the London FX bar of the day before is over at 08:00 KST - read at 09:30, not at 07:30
    summer = date(2024, 6, 12)
    assert dc.session_day("KRW=X", dc.cut("KRW=X", SERIES["KRW=X"], dc.cutoff_of("kr", summer, 2))[-1]) == date(2024, 6, 11)
    assert dc.session_day("KRW=X", dc.cut("KRW=X", SERIES["KRW=X"], dc.cutoff_of("kr", summer, 1))[-1]) == date(2024, 6, 10)
    # winter (EST): the 10-year yield's 17:00 ET close is 07:00 KST - in time for 07:30
    winter = date(2024, 1, 10)
    assert dc.session_day("^TNX", dc.cut("^TNX", SERIES["^TNX"], dc.cutoff_of("kr", winter, 1))[-1]) == date(2024, 1, 9)


def test_ties_go_to_the_long_side_in_the_service_and_the_backtest():
    even = {"strong_long": 10.0, "weak_long": 30.0, "flat": 20.0, "weak_short": 30.0, "strong_short": 10.0}
    assert dc.direction(even) == "long" and dc.top_stage(even) == "weak_long"
    P = np.array([[0.1, 0.3, 0.2, 0.3, 0.1]])                    # STAGES order
    rs = [{"day": date(2024, 1, 2), "stage": "weak_long", "z": 0.5, "prev_dir": 1}]
    m = dct.metrics(P, rs, np.full(5, 0.2))
    assert m["direction_hit_rate"] == 1.0 and m["stage_hit_rate"] == 1.0 and m["called_long_share"] == 1.0
    assert dct.metrics(P, [{**rs[0], "z": 0.0}], np.full(5, 0.2)) == {"n": 1}   # nothing moved: no rates


def test_a_close_to_close_answer_needs_yesterdays_bar():
    day = date(2024, 6, 12)
    gap = {**SERIES, "^KS11": [b for b in SERIES["^KS11"] if dc.session_day("^KS11", b) != date(2024, 6, 11)]}
    assert dc.label("kr", day, gap, "cc") is None                    # the close before is two days old
    assert dc.label("kr", day, gap, "oc") is not None
    assert dc.previous_session("kr", date(2024, 6, 7)) == date(2024, 6, 5)   # 현충일 (6/6) is skipped
