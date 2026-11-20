"""#652 the trade map's two entry scenarios per side: breakout and support, stops where the reason breaks."""

import pytest

from desk import trade_map, trade_map_record

# KORU as in the issue: 6.93 now, a strong support zone 6.51 just under it, the long trigger 7.73, a far
# second support 4.5 (where the old trigger map's stop went: -45%)
KORU = {
    "atr": 0.6, "reference_price": 6.93,
    "levels": [
        {"price": 7.6, "kind": "resistance", "strength": 4.0, "basis": "스윙 고점"},
        {"price": 9.02, "kind": "resistance", "strength": 3.0, "basis": "52주 고가"},
        {"price": 11.36, "kind": "resistance", "strength": 2.0, "basis": "주간 고점"},
        {"price": 6.51, "kind": "support", "strength": 11.1, "basis": "스윙 저점 ×3"},
        {"price": 6.4, "kind": "support", "strength": 1.5, "basis": "20일선"},
        {"price": 4.5, "kind": "support", "strength": 3.0, "basis": "주간 저점"},
    ],
}
HOLD = {"opinion": "hold", "bias": "flat", "entry_price": None, "long_trigger": 7.73, "short_trigger": 5.92,
        "playbooks": {"regime": {"id": "downtrend"}}}


def _sc(tm, side, kind):
    return next(s for s in tm["scenarios"] if s["side"] == side and s["kind"] == kind)


def test_koru_gets_a_support_entry_near_the_price_beside_the_breakout():
    tm = trade_map.build(HOLD, KORU)
    sup = _sc(tm, "long", "support")
    assert sup["entry_type"] == "now" and sup["entry"] == 6.93          # 6.93 is within half an ATR of 6.51
    assert sup["stop"] == pytest.approx(6.1)                                           # under the zone's far edge (6.4) by 0.5 ATR
    assert sup["targets"][0]["price"] == 7.6 and sup["rr"] == round((7.6 - 6.93) / (6.93 - 6.1), 2)
    assert sup["level"] == 6.51 and sup["strength"] == 11.1 and "스윙 저점" in sup["basis"]
    brk = _sc(tm, "long", "breakout")
    assert brk["entry"] == 7.73 and brk["entry_type"] == "stop"
    assert brk["stop"] == 7.3                                           # half an ATR under the broken 7.6
    assert brk["targets"][0]["price"] == 9.02 and brk["stop_atr"] <= trade_map.MAX_STOP_ATR
    assert {s["side"] for s in tm["scenarios"]} == {"long", "short"}


def test_no_computed_stop_is_further_than_three_atr():
    tm = trade_map.build(HOLD, KORU)
    for s in tm["scenarios"]:
        assert s["stop_atr"] <= trade_map.MAX_STOP_ATR
    for m in tm["maps"]:
        entry = next(st["price"] for st in m["steps"] if st["role"] == "entry")
        stop = next(st["price"] for st in m["steps"] if st["role"] == "stop")
        assert abs(entry - stop) <= trade_map.MAX_STOP_ATR * KORU["atr"] + 1e-9


def test_a_support_further_away_is_a_limit_above_it_and_low_reward_is_flagged():
    sr = {**KORU, "reference_price": 7.5}
    tm = trade_map.build({**HOLD, "live_price": {"price": 7.5}}, sr)
    sup = _sc(tm, "long", "support")
    assert sup["entry_type"] == "limit" and sup["entry"] == round(6.51 + 0.25 * 0.6, 2)
    assert sup["distance_pct"] < 0
    brk = _sc(tm, "long", "breakout")
    assert brk["low_rr"] is (brk["rr"] < trade_map.LOW_RR)


def test_a_short_mirrors_with_the_nearest_strong_resistance():
    tm = trade_map.build(HOLD, KORU)
    res = _sc(tm, "short", "support")
    assert res["level"] == 7.6 and res["entry_type"] == "limit" and res["entry"] == pytest.approx(7.6 - 0.15)
    assert res["stop"] == pytest.approx(7.6 + 0.3) and res["targets"][0]["price"] < res["entry"]


def test_a_plan_with_a_side_gets_that_sides_scenarios_only():
    plan = {"opinion": "buy", "bias": "long", "entry_price": 6.9, "stop_loss": 6.2, "price_target": 9.0,
            "entry_type": "limit"}
    tm = trade_map.build(plan, KORU)
    assert {s["side"] for s in tm["scenarios"]} == {"long"}
    assert _sc(tm, "long", "breakout")["entry"] == round(7.6 + 0.25 * 0.6, 2)   # no trigger: past 7.6


def test_the_regime_record_says_which_entry_did_better():
    table = {"generated_at": "2026-11-18", "rows": {
        "long:breakout:downtrend": {"n": 120, "win_rate": 0.41, "avg_r": -0.05, "max_dd_r": -9.0},
        "long:support:downtrend": {"n": 90, "win_rate": 0.48, "avg_r": 0.12, "max_dd_r": -6.0}}}
    found = trade_map.scenarios(KORU, 6.93, 0.6, "long", 7.73)
    got = trade_map_record.lookup("downtrend", found, table)
    assert "하락 추세에서는 과거 지지 근처 진입이 나았어요" in got["regime_note"]
    assert next(s for s in found if s["kind"] == "support")["record"]["n"] == 90
    thin = {"rows": {"long:breakout:range": {"n": 5, "win_rate": 0.4, "avg_r": 0.1}}}
    assert "표본" in trade_map_record.lookup("range", found, thin)["regime_note"]
    assert trade_map_record.lookup(None, found, table)["regime_note"] is None


def _bar(o, h, lo, c):
    return {"open": o, "high": h, "low": lo, "close": c}


def test_a_limit_fills_on_the_touch_and_the_stop_comes_first_on_a_wide_bar():
    from desk import trade_map_bt as bt

    sc = {"side": "long", "kind": "support", "entry": 10.0, "entry_type": "limit", "stop": 9.0,
          "targets": [{"price": 12.0, "rr": 2.0}]}
    win = bt.play(sc, [_bar(10.5, 10.6, 10.2, 10.4), _bar(10.3, 10.4, 9.9, 10.1), _bar(10.2, 12.3, 10.1, 12.0)],
                  cost_bps=0)
    assert win["exit"] == "target" and win["fill"] == 10.0 and win["r"] == 2.0
    both = bt.play(sc, [_bar(10.2, 10.3, 9.95, 10.1), _bar(10.0, 12.5, 8.8, 11.0)], cost_bps=0)
    assert both["exit"] == "stop" and both["r"] == -1.0                    # a bar through both: the stop
    assert bt.play(sc, [_bar(11, 11.5, 10.5, 11)] * 6) is None             # never reached in 5 bars


def test_a_breakout_fills_at_the_open_when_it_gaps_through_and_a_short_mirrors():
    from desk import trade_map_bt as bt

    sc = {"side": "long", "kind": "breakout", "entry": 10.0, "entry_type": "stop", "stop": 9.5,
          "targets": [{"price": 11.0, "rr": 2.0}]}
    got = bt.play(sc, [_bar(10.4, 10.6, 10.3, 10.5), _bar(10.5, 10.7, 10.4, 10.6)], cost_bps=0)
    assert got["fill"] == 10.4 and got["exit"] == "time" and round(got["r"], 3) == round(0.2 / 0.5, 3)   # plan risk
    short = {"side": "short", "kind": "support", "entry": 10.0, "entry_type": "now", "stop": 10.5,
             "targets": [{"price": 9.0, "rr": 2.0}]}
    s = bt.play(short, [_bar(10.0, 10.1, 9.9, 10.0), _bar(9.8, 9.9, 8.9, 9.0)], cost_bps=0)
    assert s["exit"] == "target" and s["r"] == 2.0


def test_the_summary_rows_and_the_worst_run():
    from desk import trade_map_bt as bt

    trades = [{"day": f"2026-01-0{i}", "ticker": "X", "side": "long", "kind": "support", "regime": "range",
               "r": r, "exit": "stop" if r < 0 else "target"} for i, r in enumerate([1.0, -1.0, -1.0, 2.0], 1)]
    rows = bt.summarize(trades, {"long:support:range": 8, "long:support:all": 8})
    r = rows["long:support:range"]
    assert (r["n"], r["fill_rate"], r["win_rate"], r["avg_r"], r["max_dd_r"]) == (4, 0.5, 0.5, 0.25, -2.0)
    assert rows["long:support:all"]["n"] == 4


def test_one_scenario_only_is_not_called_a_thin_sample():
    # #671 no strong level near: one scenario on the side, so nothing to compare - not "표본 부족"
    table = {"rows": {"long:breakout:downtrend": {"n": 120, "win_rate": 0.41, "avg_r": -0.05}}}
    only = [s for s in trade_map.scenarios(KORU, 6.93, 0.6, "long", 7.73) if s["kind"] == "breakout"]
    got = trade_map_record.lookup("downtrend", only, table)
    assert got["regime_note"] is None and only[0]["record"]["n"] == 120


def test_the_breakout_stop_is_under_the_level_it_broke_not_a_support_under_the_price():
    # #671 a support 6.9 under the price (6.93) is within 1.5 ATR of the 7.73 trigger: not what it broke
    sr = {**KORU, "levels": [*KORU["levels"], {"price": 6.9, "kind": "support", "strength": 1.0, "basis": "5일선"}]}
    brk = _sc(trade_map.build(HOLD, sr), "long", "breakout")
    assert brk["stop"] == 7.3 and brk["level"] == 7.6 and "아래" in brk["basis"]


def test_a_short_breakout_stops_above_the_level_it_broke_and_says_so():
    brk = _sc(trade_map.build(HOLD, KORU), "short", "breakout")
    assert brk["entry"] == 5.92 and brk["level"] == 6.4                  # nearest the trigger
    assert brk["stop"] == round(6.51 + 0.3, 2)                           # past the zone's far edge (6.51)
    assert "이탈한 레벨" in brk["basis"] and brk["basis"].endswith("위")


def test_the_support_entry_is_dimmed_on_its_first_targets_reward():
    # the user's call (#671): low reward/risk is judged on the first target (KORU: 7.6, not 9.02)
    sup = _sc(trade_map.build(HOLD, KORU), "long", "support")
    assert sup["rr"] == sup["targets"][0]["rr"] < trade_map.LOW_RR and sup["low_rr"]
    assert sup["targets"][1]["rr"] >= trade_map.LOW_RR


def test_a_limit_gapped_through_its_stop_is_a_scratch_stop_not_dropped():
    from desk import trade_map_bt as bt

    sc = {"side": "long", "kind": "support", "entry": 10.0, "entry_type": "limit", "stop": 9.0,
          "targets": [{"price": 12.0, "rr": 2.0}]}
    got = bt.play(sc, [_bar(8.5, 8.8, 8.2, 8.6)], cost_bps=10)
    assert got["exit"] == "stop" and got["fill"] == 8.5 and -0.02 < got["r"] < 0
    now = {**sc, "entry_type": "now"}
    assert bt.play(now, [_bar(8.5, 8.8, 8.2, 8.6)]) is None              # a "now" opening past its stop: no plan


def test_overlapping_trades_are_counted_once_in_n_indep():
    from desk import trade_map_bt as bt

    base = {"ticker": "X", "side": "long", "kind": "support", "regime": "range", "r": 1.0, "exit": "target"}
    trades = [{**base, "day": "2026-01-01", "t0": 1, "t1": 10}, {**base, "day": "2026-01-03", "t0": 3, "t1": 12},
              {**base, "day": "2026-01-12", "t0": 11, "t1": 14}, {**base, "ticker": "Y", "day": "2026-01-03",
                                                                  "t0": 3, "t1": 12}]
    row = bt.summarize(trades, {})["long:support:range"]
    assert (row["n"], row["n_indep"]) == (4, 3)


def test_a_tiny_gap_is_no_better_one_and_no_minus_zero():
    table = {"rows": {"long:breakout:downtrend": {"n": 300, "n_indep": 90, "win_rate": 0.41, "avg_r": 0.012},
                      "long:support:downtrend": {"n": 280, "n_indep": 80, "win_rate": 0.45, "avg_r": -0.003}}}
    found = trade_map.scenarios(KORU, 6.93, 0.6, "long", 7.73)
    note = trade_map_record.lookup("downtrend", found, table)["regime_note"]
    assert "차이가 없었어요" in note and "나았어요" not in note
    assert "-0.00" not in note and "+0.00R" in note and "90 · 80번" in note


def test_the_record_is_for_swing_runs_only():
    table = {"rows": {"long:breakout:downtrend": {"n": 120, "win_rate": 0.41, "avg_r": -0.05}}}
    found = trade_map.scenarios(KORU, 6.93, 0.6, "long", 7.73)
    got = trade_map_record.lookup("downtrend", found, table, horizon="intraday")
    assert got == {"regime_note": None, "record_meta": None} and all(s["record"] is None for s in found)
    assert trade_map_record.lookup("downtrend", found, table)["record_meta"]["horizon"] == "swing"
    assert trade_map.build({**HOLD, "horizon": "scalp"}, KORU)["regime_note"] is None


def test_short_notes_use_the_short_names():
    table = {"rows": {"short:breakout:uptrend": {"n": 200, "n_indep": 60, "win_rate": 0.3, "avg_r": -0.3},
                      "short:support:uptrend": {"n": 200, "n_indep": 60, "win_rate": 0.4, "avg_r": -0.1}}}
    found = trade_map.scenarios(KORU, 6.93, 0.6, "short", 5.92)
    note = trade_map_record.lookup("uptrend", found, table)["regime_note"]
    assert "저항 근처 진입" in note and "이탈 확인 진입" in note and "지지 근처" not in note


def test_a_price_already_past_the_trigger_keeps_the_broken_level_and_one_stop():
    # review #671: live 7.8 is past the 7.73 trigger - 7.6 is still what it broke, and the map says the same
    tm = trade_map.build({**HOLD, "live_price": {"price": 7.8}}, KORU)
    brk = _sc(tm, "long", "breakout")
    assert brk["stop"] == 7.3 and brk["level"] == 7.6
    long_map = next(m for m in tm["maps"] if m["side"] == "long")
    assert next(st["price"] for st in long_map["steps"] if st["role"] == "stop") == brk["stop"]


def test_a_gap_on_a_later_bar_and_old_files_without_bar_indexes():
    from desk import trade_map_bt as bt

    sc = {"side": "long", "kind": "support", "entry": 10.0, "entry_type": "limit", "stop": 9.0,
          "targets": [{"price": 12.0, "rr": 2.0}]}
    got = bt.play(sc, [_bar(10.5, 10.6, 10.2, 10.4), _bar(8.7, 8.9, 8.5, 8.8)], cost_bps=0)
    assert (got["exit"], got["fill"], got["r"], got["start"], got["end"]) == ("stop", 8.7, 0.0, 1, 1)
    old = [{"day": "2026-01-01", "ticker": "X", "side": "long", "kind": "support", "regime": "range",
            "r": 1.0, "exit": "target"}]
    assert bt.summarize(old, {})["long:support:range"]["n_indep"] is None    # not silently n
    assert trade_map_record._n({"n": 5, "n_indep": None}) == 5


@pytest.mark.parametrize(("side", "sign"), [("long", 1), ("short", -1)])
@pytest.mark.parametrize(("entry", "atr"), [(1000.6, 0.2), (0.1006, 0.0002)])
def test_trade_map_keeps_precision_for_ladder_and_scenario_risk(side, sign, entry, atr):
    sr = {"reference_price": entry - sign * atr, "atr": atr, "levels": [
        {"price": entry - sign * atr, "strength": 4, "basis": "진입 근거"},
    ]}
    tm = trade_map.build({f"{side}_trigger": entry}, sr)
    steps = {s["role"]: s for s in tm["maps"][0]["steps"]}
    scenario = _sc(tm, side, "breakout")
    stop = entry - sign * 1.5 * atr
    assert steps["entry"]["price"] == scenario["entry"] == entry
    assert steps["stop"]["price"] == scenario["stop"] == pytest.approx(stop)
    assert steps["target1"]["then_stop"] == entry
    assert tm["atr"] == atr
    assert scenario["stop_atr"] == 1.5
    assert trade_map.scenarios(sr, sr["reference_price"], atr, side, entry)[0]["entry"] == (
        float(round(entry)) if entry >= 1000 else round(entry, 2)
    )


@pytest.mark.parametrize(("side", "sign"), [("long", 1), ("short", -1)])
@pytest.mark.parametrize(("entry", "atr"), [(1000.6, 0.2), (0.1006, 0.0002)])
def test_ladder_keeps_a_clamped_or_explicit_stop_at_its_calculated_price(side, sign, entry, atr):
    sr = {"levels": [{"price": entry - sign * 10 * atr, "strength": 4}]}
    computed = trade_map.side_map(side, entry, None, None, sr, atr)
    stop = next(s for s in computed["steps"] if s["role"] == "stop")
    assert stop["price"] == pytest.approx(entry - sign * 3 * atr)
    explicit_price = entry - sign * 5 * atr
    custom = trade_map.side_map(side, entry, explicit_price, None, sr, atr)
    explicit_stop = next(s for s in custom["steps"] if s["role"] == "stop")
    assert explicit_stop["source"] == "plan"
    assert explicit_stop["price"] == explicit_price
