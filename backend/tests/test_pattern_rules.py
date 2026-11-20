"""#664 the practical rules on converging patterns: quality, break status, staged targets, retest entry,
no-edge label - and the backtest's before / after measures."""

from desk import patterns, patterns_bt
from tests.test_patterns import K, _zigzag


def _asc(n=120):
    return _zigzag(lambda i: 120.0, lambda i: 90 + 0.2 * i, n)


def _one(bars, price=None):
    got = patterns.find(bars, K, 1.0, price=price)
    assert got
    return got[0]


def test_quality_scores_touches_closes_trend_and_volume(monkeypatch):
    monkeypatch.setattr(patterns, "_stats_cache", {})
    p = _one(_asc())
    q = p["quality"]
    assert 0 <= q["score"] <= 100 and q["grade"] in ("strong", "normal", "weak")
    assert q["label"] == patterns.GRADE_KO[q["grade"]] and q["touches"][0] >= 2 and q["touches"][1] >= 2
    assert q["score"] == sum(q["points"].values()) and q["volume_ratio"] == 1.0   # flat volume: no points
    dry = _asc()
    for i, b in enumerate(dry):
        b["volume"] = 2000 - 15 * i                                   # volume drying up through the pattern
    assert _one(dry)["quality"]["points"]["volume"] == 20


def test_break_status_close_wick_false_late_and_live_price(monkeypatch):
    monkeypatch.setattr(patterns, "_stats_cache", {})
    assert _one(_asc())["breakout"]["status"] == "converging"

    wick = _asc()
    wick[-1].update(high=125.5)                                       # only the high beyond the top
    b = _one(wick)["breakout"]
    assert b["status"] == "attempt" and b["side"] == "up" and b["at"] is None

    broke = _asc()
    for x in broke[-2:]:
        x.update(close=125.0, high=125.5)
    p = _one(broke)
    assert p["state"] == "broke_up" and p["breakout"]["status"] in ("broken", "late")
    assert p["breakout"]["at"] == broke[-2]["at"] and 0 < p["breakout"]["share"] <= 1

    false = _asc()
    false[-2].update(close=125.0, high=125.5)                         # out, then the last close back inside
    p = _one(false)
    assert p["state"] == "converging" and p["breakout"]["status"] == "false"
    assert p["retest"]["scenarios"] == [] and "거짓 돌파" in p["explain"]
    # the live price back outside makes it a break again
    assert _one(false, price=126.0)["breakout"]["status"] in ("broken", "late")


def test_a_late_break_keeps_the_first_target_only(monkeypatch):
    monkeypatch.setattr(patterns, "_stats_cache", {})
    broke = _asc()
    for x in broke[-2:]:
        x.update(close=125.0, high=125.5)
    monkeypatch.setattr(patterns, "LATE_SHARE", 0.99)
    assert len(_one(broke)["targets"]["stages"]) == 2
    monkeypatch.setattr(patterns, "LATE_SHARE", 0.1)
    p = _one(broke)
    assert p["breakout"]["status"] == "late" and [s["stage"] for s in p["targets"]["stages"]] == [1]
    assert "늦은 돌파" in p["explain"]


def test_staged_targets_and_a_rising_wedges_downside_at_its_lowest_trough(monkeypatch):
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:ascending": {"n": 40, "reach_pct": 30.0,
                                                                               "reach1_pct": 55.0}}})
    p = _one(_asc())
    t = p["targets"]
    first, second = t["stages"]
    assert second["up"] == t["up"] and second["down"] == t["down"]            # the old fields stay the second
    measured = t["basis"] == "measured_move"                                # the measured reach only for it
    assert first["share"] == patterns.FIRST_SHARE and first["reach_pct"] == (55.0 if measured else None)
    assert p["upper"]["to"]["price"] < first["up"] < second["up"]
    assert second["down"] < first["down"] < p["lower"]["to"]["price"]

    monkeypatch.setattr(patterns, "_stats_cache", {})
    wedge = _zigzag(lambda i: 110 + 0.1 * i, lambda i: 90 + 0.25 * i, 120)
    p = _one(wedge)
    assert p["kind"] == "rising_wedge"
    second = p["targets"]["stages"][-1]
    if second["down_basis"] == "pattern_low":
        lowest = min(b["low"] for b in wedge[-patterns.LOOKBACK:])
        assert second["down"] <= p["targets"]["stages"][0]["down"] and second["down"] >= round(lowest, 2) - 0.01


def test_retest_scenarios_in_the_trade_maps_shape(monkeypatch):
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:ascending": {
        "n": 40, "pullback_pct": 64.0, "retest_win_pct": 48.0, "retest_r_avg": 0.21, "retest_n": 25,
        "chase_r_avg": -0.05}}})
    p = _one(_asc())
    sc = p["retest"]["scenarios"]
    assert [s["side"] for s in sc] == ["long", "short"] and all(s["when"] == "if_break" for s in sc)
    long_, short = sc
    assert long_["stop"] < long_["entry"] and short["stop"] > short["entry"]
    assert long_["kind"] == "retest" and long_["entry_type"] == "limit" and long_["rr"] > 0
    assert all(t["price"] > long_["entry"] for t in long_["targets"])
    assert p["retest"]["pullback_pct"] == 64.0 and p["retest"]["win_pct"] == 48.0 and p["retest"]["n"] == 25

    broke = _asc()
    for x in broke[-2:]:
        x.update(close=125.0, high=125.5)
    p = _one(broke)
    sc = p["retest"]["scenarios"]
    assert len(sc) == 1 and sc[0]["side"] == "long" and sc[0]["when"] == "after_break"
    assert "선으로 되돌아온 비율 64%" in p["explain"] and "되돌림 진입 평균 +0.21R" in p["explain"]


def test_no_edge_says_reference_only(monkeypatch):
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:ascending": {"n": 40, "verdict": "no_edge"}}})
    p = _one(_asc())
    assert p["reference_only"] is True and p["reference_label"] == patterns.NO_EDGE_KO
    assert patterns.NO_EDGE_KO in p["explain"] and p["explain"].endswith("예측 아님.")
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:ascending": {"n": 40, "verdict": "edge"}}})
    p = _one(_asc())
    assert p["reference_only"] is False and p["reference_label"] is None and patterns.NO_EDGE_KO not in p["explain"]
    monkeypatch.setattr(patterns, "_stats_cache", {})
    assert _one(_asc())["reference_label"] == "참고용 · 과거 통계 없음"


def test_the_mid_of_the_window_is_the_measured_median_else_the_textbook_one(monkeypatch):
    monkeypatch.setattr(patterns, "_stats_cache", {})
    w = _one(_asc(90))["breakout_window"]
    assert w["mid"]["share"] == patterns.TEXTBOOK_MID["ascending"]
    monkeypatch.setattr(patterns, "_stats_cache", {"kinds": {"1d:ascending": {
        "n": 40, "frac_q25": 0.55, "frac_q50": 0.7, "frac_q75": 0.8}}})
    w = _one(_asc(90))["breakout_window"]
    assert w["mid"]["share"] == 0.7 and w["bars_ahead"][0] <= w["mid"]["bars_ahead"] <= w["bars_ahead"][1]


def _run_up(n=170, steep=2.0, dip=False):
    bars = _zigzag(lambda i: 120.0, lambda i: 90 * 1.002 ** i, n)
    for i, b in enumerate(bars[100:], start=100):
        c = 121.0 + (i - 100) * steep
        if dip and i in (103, 104):
            c = 120.6                                                 # back to the line: the pullback
        b.update(open=c, high=c + 0.3, low=c - 0.3, close=c)
    return bars


def test_the_backtest_walks_the_chase_and_the_retest_entries():
    r = patterns_bt.instances(_run_up(), "1d", K, 5, "T")[0]
    assert r["direction"] == "up" and r["grade"] in ("strong", "normal", "weak") and r["late"] in (True, False)
    assert r["reach1"] is True and r["chase_r"] > 0 and r["pullback"] is False and r["retest_r"] is None
    r = patterns_bt.instances(_run_up(dip=True), "1d", K, 5, "T")[0]
    assert r["pullback"] is True and r["retest_r"] is not None


def test_walk_stops_on_a_close_past_the_stop_and_wins_at_the_goal():
    import math

    def bars(closes):
        return [{"open": c, "high": c + 0.5, "low": c - 0.5, "close": c} for c in closes]

    lg = math.log
    assert patterns_bt._walk(bars([100, 99, 98]), 0, 2, lg(100), lg(98.5), lg(110), 1) < 0       # stopped at 98
    assert patterns_bt._walk(bars([100, 104, 110]), 0, 2, lg(100), lg(98), lg(104.4), 1) > 0     # high touched
    assert patterns_bt._walk(bars([100, 101]), 0, 5, lg(100), lg(98), lg(110), 1) is None         # too recent


def test_the_table_carries_the_rules_and_the_grades(monkeypatch):
    monkeypatch.setattr(patterns_bt, "MIN_N", 4)
    rows = [{"ticker": "T", "at": f"2024-01-{d:02d}", "kind": "ascending", "timeframe": "1d", "expected": "up",
             "direction": "up" if d % 3 else "down", "move_atr": 1.0, "false_break": d % 2 == 0,
             "grade": "weak" if d % 4 == 0 else "normal", "late": d % 5 == 0, "reach1": d % 2 == 1,
             "pullback": d % 3 == 1, "chase_r": 1.0 if d % 2 else -1.0, "retest_r": 1.5 if d % 3 == 1 else None,
             "extreme": None} for d in range(1, 29)]
    s = patterns_bt.table(rows, rows)["1d:ascending"]
    assert s["reach1_pct"] == 50.0 and s["chase_r_avg"] == 0.0 and s["retest_n"] == 10
    assert set(s["by_grade"]) == {"normal", "weak"} and s["by_grade"]["weak"]["n"] == 7
    rules = s["rules"]
    assert rules["n"] == 21                                            # weak ones left out
    assert rules["verdict"] in ("edge", "no_edge", "unstable", "unmeasured") and rules["edge_pct"] is not None
