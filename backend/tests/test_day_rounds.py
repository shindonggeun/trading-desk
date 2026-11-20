"""#619 the day call again during the session: round times (daylight saving, half days, a late open), no
bar after the round read, the answer in the ATR left, the backtest, and the rounds stored, scored, served
and pushed only on a two-stage jump."""

import math
import random
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import insert

from desk import day_call as dc
from desk import day_call_train as dct
from desk import day_calls, market_calendar
from desk import day_rounds as dr
from desk import day_rounds_train as drt
from desk.db import day_calls as t
from desk.db import make_engine, migrate
from tests.test_day_call import SERIES

CLIM = [0.06, 0.24, 0.40, 0.24, 0.06]
DAY = date(2024, 6, 12)                         # a Wednesday, open in both markets


def _index_hours(market: str, seed: int) -> list[SimpleNamespace]:
    """Hourly bars of the target on Yahoo's grid (KRX 09:00 to the bar ending 15:00, NYSE 09:30 to the
    close): the price walks from the day's open toward its close with noise, so the move so far says
    something about the move left."""
    rng = random.Random(seed)
    m = dc.MARKETS[market]
    out = []
    for b in SERIES[m.target]:
        day = dc.session_day(m.target, b)
        opens, closes = market_calendar.session_bounds(m.calendar, day)
        last = datetime.combine(day, time(15), m.zone) if market == "kr" else closes
        start, px = opens, b.open
        while start < last:
            end = min(start + timedelta(hours=1), closes)
            share = (end - opens) / (closes - opens)
            c = (b.open + (b.close - b.open) * share) * (1 + rng.gauss(0, 0.002))
            out.append(SimpleNamespace(at=start.astimezone(UTC).isoformat(), open=px, high=max(px, c) * 1.001,
                                       low=min(px, c) * 0.999, close=c, volume=0))
            start, px = end, c
    return out


S = {**SERIES, dr.HOURLY["kr"]: _index_hours("kr", 21), dr.HOURLY["us"]: _index_hours("us", 22)}


def test_the_round_times_follow_new_york_and_the_session():
    # 10:30 ET is 00:30 KST in winter and 23:30 KST the day before in summer (2024-03-10 the switch)
    assert dr.cutoff_of("us", date(2024, 3, 8), 3).astimezone(dc.KST).strftime("%m-%d %H:%M") == "03-09 00:30"
    assert dr.cutoff_of("us", date(2024, 3, 11), 3).astimezone(dc.KST).strftime("%m-%d %H:%M") == "03-11 23:30"
    assert dr.rounds_of("kr", DAY) == [3, 4, 5, 6, 7, 8] and dr.rounds_of("us", DAY) == [3, 4, 5, 6, 7, 8]
    assert dr.rounds_of("us", date(2024, 11, 29)) == [3, 4, 5]          # the half day after Thanksgiving: 13:00
    assert dr.rounds_of("kr", date(2024, 11, 14)) == []                 # the CSAT opens at 10:00: no rounds
    assert dr.rounds_of("kr", date(2024, 6, 15)) == [] and dr.rounds_of("kr", date(2024, 6, 6)) == []
    assert dr.left("kr", DAY, 8) == pytest.approx(30 / 390) and dr.left("us", date(2024, 11, 29), 5) == pytest.approx(30 / 210)
    assert dr.round_label("us", 3) == "3차 10:30" and dr.round_label("kr", 8) == "8차 15:00"


@pytest.mark.parametrize("market", ["kr", "us"])
def test_no_bar_after_the_round_is_read(market):
    x, meta = dr.features(market, 5, DAY, S)
    cutoff = dr.cutoff_of(market, DAY, 5)
    today = dr.session_bars(market, DAY, S[dr.HOURLY[market]], cutoff)
    assert meta["anchor"] == today[-1].close and dr._bar_end(market, today[-1]) == cutoff

    def wild(b):
        return SimpleNamespace(**{**vars(b), "high": b.high * 3, "low": b.low / 3, "close": b.close * 2})
    # the bar running across the round (it ends after it) and every later one changed: the same features
    moved = {**S, dr.HOURLY[market]: [b if dr._bar_end(market, b) <= cutoff else wild(b) for b in S[dr.HOURLY[market]]],
             dr.NQ: [b if datetime.fromisoformat(b.at) + timedelta(hours=1) <= cutoff else wild(b) for b in S[dr.NQ]]}
    got, meta2 = dr.features(market, 5, DAY, moved)
    assert got == x and meta2["anchor"] == meta["anchor"]
    assert set(x) >= {"i_move", "i_last", "i_range", "i_pos", "i_left", "gap_open", "gap"}
    assert ("nq_day" in x) == (market == "kr")


def test_the_answer_is_the_close_against_the_anchor_in_the_atr_left():
    _x, meta = dr.features("kr", 7, DAY, S)
    ans = dr.label("kr", DAY, 7, S, meta["anchor"], now=datetime(2025, 1, 1, tzinfo=UTC))
    day_bar = dc.on_day("^KS11", S["^KS11"], DAY)
    assert ans["close"] == day_bar.close and ans["open"] == meta["anchor"]
    assert ans["z"] == pytest.approx((day_bar.close - meta["anchor"]) / (meta["atr"] * math.sqrt(meta["left"])))
    assert ans["stage"] == dc.stage_of(ans["z"])
    # a session still trading has no answer
    assert dr.label("kr", DAY, 7, S, meta["anchor"], now=dr.cutoff_of("kr", DAY, 7)) is None


def test_an_open_gap_counts_until_the_price_trades_back_to_the_last_close():
    _x0, meta0 = dc.features("kr", 2, DAY, S)
    prev = meta0["last_close"]
    m = dc.MARKETS["kr"]
    opens = datetime.combine(DAY, time(9), m.zone)

    def bars(lows):
        return [SimpleNamespace(at=(opens + timedelta(hours=i)).astimezone(UTC).isoformat(), open=prev * 1.01,
                                high=prev * 1.02, low=prev * lo, close=prev * 1.012, volume=0) for i, lo in enumerate(lows)]
    up = {**S, dr.HOURLY["kr"]: bars([1.005, 1.004, 1.006])}
    assert dr.features("kr", 5, DAY, up)[0]["gap_open"] == 1.0
    filled = {**S, dr.HOURLY["kr"]: bars([1.005, 0.999, 1.006])}
    assert dr.features("kr", 5, DAY, filled)[0]["gap_open"] == 0.0
    with pytest.raises(dc.NoData):                    # no bar of the day by 10:00 yet
        dr.features("kr", 3, DAY, {**S, dr.HOURLY["kr"]: []})


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(dct, "STEPS", 150)
    monkeypatch.setattr(dct, "L2_GRID", (0.01, 0.1))
    monkeypatch.setattr(dct, "LAMBDA_GRID", (0.0, 0.3))
    monkeypatch.setattr(dct, "CV_YEARS", 1)


def test_the_backtest_holds_out_the_last_90_days_per_round_and_the_server_reads_the_same(quick):
    model_file, report = drt.backtest("kr", S, now=datetime(2026, 1, 1, tzinfo=UTC))
    model = model_file["model"]
    assert model_file["version"].startswith("dr-") and model_file["call"] == "to_close"
    assert model["trained_through"] < report["holdout_from"] == model_file["holdout_from"]
    assert set(report["rounds"]) == {str(r) for r in dr.ROUNDS["kr"]} and report["rounds"]["3"]["label"] == "3차 10:00"
    hold = report["rounds"]["5"]["holdout"]
    assert hold["n"] > 30 and set(hold["baselines"]) == {"always_long", "always_short", "follow_trend", "climatology"}
    assert report["holdout"]["n"] == sum(r["holdout"]["n"] for r in report["rounds"].values())
    assert {"i_move", "nq_day", "gap"} <= set(report["features"]) and report["code"] == drt.code_hash()
    # the walk toward the close makes the move so far a signal: following it beats always long
    assert report["holdout"]["direction_hit_rate"] > report["holdout"]["baselines"]["always_long"]["direction_hit_rate"]
    # the service's call on a held-out session gives the backtest's probabilities
    day = date.fromisoformat(report["holdout_from"]) + timedelta(days=7)
    while not dr.rounds_of("kr", day):
        day += timedelta(days=1)
    rows = [r for r in drt.rows("kr", S, datetime(2026, 1, 1, tzinfo=UTC)) if r["day"] == day and r["round"] == 6]
    c = dr.call(model_file, "kr", 6, day, S)
    assert list(c["probs"].values()) == list(dc.percent(dc.raw_probs(model, rows[0]["x"])).values())[::-1]
    assert c["features"]["anchor"] and c["round_label"] == "6차 13:00" and c["reasons"]
    assert model_file["verdict"] == report["verdict"] in ("edge", "no_edge")
    assert c["reference_only"] == (report["verdict"] != "edge")
    assert c["verdict"] == dc.verdict(c["probs"], dc.HOLD_MARGIN)


# ------------------------------------------------------------------ the service

def _round_model(market: str) -> dict:
    names = list(dr.features(market, 3, DAY, S)[0])
    k = len(names)
    return {"version": f"dr-test-{market}", "market": market, "call": "to_close", "rounds": sorted(dr.ROUNDS[market]),
            "holdout_from": "2024-06-01", "verdict": "edge",
            "model": {"features": names, "mean": [0.0] * k, "std": [1.0] * k, "beta0": 0.0, "beta": [0.0] * k,
                      "gamma0": -0.9, "gamma": [0.0] * k, "edges": list(dc.EDGES), "lambda": 0.5,
                      "climatology": CLIM, "label": "to_close", "trained_through": "2024-05-31"}}


def _candles(symbol, interval, count):
    return S[symbol] if interval == "1d" else S[f"{symbol}@60m"]


@pytest.fixture
def desk(db_url):
    from tests.test_day_calls import _model

    migrate(db_url)
    engine = make_engine(db_url)
    yield day_calls.DayCalls(engine, _candles, models={"kr": _model("kr"), "us": _model("us")}, reports={},
                             round_models={"kr": _round_model("kr"), "us": _round_model("us")},
                             round_reports={"kr": {"holdout_from": "2024-06-01", "code": "x",
                                                   "rounds": {"3": {"holdout": {"n": 40, "direction_hit_rate": 0.6}}}}})
    engine.dispose()


def _kst(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), dc.KST)


def test_a_round_is_due_inside_its_window_and_stored_with_its_anchor(desk):
    # KR rounds wait 25 minutes for Yahoo's late KRX bars: 3차 (10:00) is called from 10:25 to 11:05
    assert desk.due(_kst(DAY, 10, 5)) == [("kr", 2, DAY)]
    assert desk.due(_kst(DAY, 10, 30)) == [("kr", 2, DAY), ("kr", 3, DAY)]
    assert desk.due(_kst(DAY, 11, 6)) == []                                # 2차 and 3차 windows over
    assert ("kr", 8, DAY) in desk.due(_kst(DAY, 15, 30)) and desk.due(_kst(DAY, 16, 6)) == []
    assert desk.due(datetime.combine(date(2024, 11, 14), time(11, 30), dc.KST)) == []   # the CSAT: no rounds
    us = datetime.combine(date(2024, 11, 29), time(13, 35), dc.ET)         # a half day: no 13:30 round
    assert desk.due(us) == []
    assert desk.due(datetime.combine(DAY, time(10, 31), dc.ET)) == [("us", 3, DAY)]
    made = desk.run(_kst(DAY, 10, 30))
    mine = [r for r in made if r["call"] == "to_close"]
    assert len(mine) == 1 and mine[0]["round"] == 3 and mine[0]["features"]["anchor"] > 0
    assert mine[0]["cutoff_at"] == dr.cutoff_of("kr", DAY, 3)              # the round's time, not the call's
    assert desk.due(_kst(DAY, 10, 45)) == []                               # every call of both rounds stored
    assert desk.run(_kst(DAY, 10, 50)) == []


def test_a_missing_opening_bar_makes_no_round():
    hours = [b for b in S[dr.HOURLY["kr"]] if not b.at.startswith(
        datetime.combine(DAY, time(9), dc.KST).astimezone(UTC).isoformat())]
    with pytest.raises(dc.NoData):
        dr.features("kr", 5, DAY, {**S, dr.HOURLY["kr"]: hours})


def test_the_rounds_are_scored_served_and_counted(desk):
    for h in (10, 11, 12, 13, 14, 15):
        desk.run(_kst(DAY, h, 30))
        desk._tried.clear()
    assert desk.score(_kst(DAY, 16, 30), force=True) >= 6
    view = desk.view("kr", _kst(DAY, 16, 40))
    rounds = view["rounds"]["to_close"]
    assert [r["round"] for r in rounds] == [3, 4, 5, 6, 7, 8] and rounds[0]["round_label"] == "3차 10:00"
    assert rounds[0]["measured_from"] == "round" and rounds[0]["anchor"] and rounds[0]["outcome"]["stage"]
    assert view["calls"]["to_close"]["round"] == 8 and view["calls"]["to_close"]["record"]["basis"] == "backtest"
    sched = view["schedule"]["to_close"]
    assert [s["called"] for s in sched] == [True] * 6 and sched[0]["at"].endswith("+09:00")
    stats = desk.stats("kr", now=_kst(DAY, 16, 40))
    block = stats["calls"]["to_close"]
    assert set(block["by_round"]) == {"3", "4", "5", "6", "7", "8"} and block["n"] == 6
    assert "follow_trend" in block["baselines"] and block["by_round"]["8"]["round_label"] == "8차 15:00"
    assert "follow_yesterday" in stats["calls"]["open"]["baselines"] and stats["rounds_backtest"]["model_version"] == "dr-test-kr"
    # the answer of a round: its own anchor, in the ATR left
    r8 = rounds[-1]
    day_bar = dc.on_day("^KS11", S["^KS11"], DAY)
    assert r8["outcome"]["close"] == pytest.approx(day_bar.close) and r8["outcome"]["open"] == pytest.approx(r8["anchor"])


def test_a_missed_round_window_is_counted_per_call(desk):
    desk.run(_kst(DAY, 10, 30))
    stats = desk.stats("kr", now=_kst(DAY, 13, 10))
    # 3차 called, 4차 (11:25-12:05) and 5차 (12:25-13:05) missed; the morning calls are their own count
    assert stats["calls"]["to_close"]["missed"] == 2


def test_only_a_jump_of_two_stages_is_pushed(desk):
    probs = {"strong_long": 10.0, "weak_long": 40.0, "flat": 30.0, "weak_short": 15.0, "strong_short": 5.0}

    def row(round_, stage):
        r = {"market": "kr", "trade_date": DAY, "round": round_, "call": "to_close", "model": "to_close", "model_version": "dr-x",
                 "source": "live", "called_at": datetime.now(UTC), "cutoff_at": datetime.now(UTC), "stage": stage, "verdict": "long",
                 "probs": probs, "reasons": [], "edges_pct": {"flat": 0.2, "strong": 0.6}, "features": {"anchor": 1.0}}
        with desk.engine.begin() as conn:
            conn.execute(insert(t).values(**r))
        return r
    assert desk.push_of(row(3, "weak_long")) is None                       # the first round: the morning was pushed
    assert desk.push_of(row(4, "flat")) is None                            # one stage
    title, body = desk.push_of(row(5, "strong_short"))                     # flat -> strong_short: two
    assert title == "국장 장중 방향 바뀜" and "5차 12:00" in body and "4차 11:00 약보합에서 바뀜" in body
    held = {"market": "kr", "trade_date": DAY, "round": 6, "call": "to_close", "stage": "weak_long", "verdict": "hold",
            "probs": probs}
    assert desk.push_of(held) is None                                       # three stages, but a hold is not news
    assert desk.push_of({**row(6, "strong_short"), "call": "open"}) is None
    first = {"market": "us", "round": 1, "call": "prev_close", "stage": "flat", "verdict": "long", "probs": probs,
             "model": "prev_close"}
    assert desk.push_of(first)[0] == "오늘 미장 방향"
    # a round model without a hold-out edge: shown as reference only, its jumps never pushed
    desk.round_models["kr"] = {**desk.round_models["kr"], "verdict": "no_edge"}
    assert desk.push_of(row(7, "strong_long")) is None
    entry = desk.view("kr", _kst(DAY, 16))["rounds"]["to_close"][-1]
    assert entry["reference_only"] is True and entry["reference_label"] == dr.NO_EDGE_KO


def test_the_verdict_needs_every_baseline_beaten_and_a_brier_skill():
    base = {"always_long": {"direction_hit_rate": 0.52}, "always_short": {"direction_hit_rate": 0.48},
            "follow_trend": {"direction_hit_rate": 0.55}}
    assert dr.verdict_of({"direction_hit_rate": 0.61, "brier_skill": 0.02, "baselines": base}) == "edge"
    assert dr.verdict_of({"direction_hit_rate": 0.59, "brier_skill": 0.02, "baselines": base}) == "no_edge"
    assert dr.verdict_of({"direction_hit_rate": 0.70, "brier_skill": -0.01, "baselines": base}) == "no_edge"
    assert dr.verdict_of({"n": 0}) == "no_edge"
    assert dr.verdict_of({"direction_hit_rate": 0.60, "brier_skill": 0.02, "baselines": base}) == "edge"  # 0.55 + 0.05


def test_the_history_keeps_the_rounds_apart(desk):
    desk.run(_kst(DAY, 10, 30))
    plain = desk.history("kr", now=_kst(DAY, 16))
    rounds = desk.history("kr", now=_kst(DAY, 16), call="to_close")
    assert plain["items"] and {i["call"] for i in plain["items"]} <= {"prev_close", "open"}
    assert [i["call"] for i in rounds["items"]] == ["to_close"]


def test_the_backfill_makes_the_rounds_after_the_round_model_training(desk):
    made = desk.backfill("kr", S, days=30, now=_kst(date(2024, 6, 20), 18))
    with desk.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(t.select().where(t.c.call == "to_close")).mappings()]
    assert made and rows and min(r["trade_date"] for r in rows) >= date(2024, 6, 1)
    assert all(r["source"] == "backfill" and r["scored_at"] is not None for r in rows)
    assert {r["round"] for r in rows} == {3, 4, 5, 6, 7, 8}
    assert np.isfinite([r["brier"] for r in rows if r["brier"] is not None]).all()
