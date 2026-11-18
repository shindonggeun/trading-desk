"""#652 the trade map's two entry scenarios per side: breakout and support, stops where the reason breaks."""

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
    assert sup["stop"] == 6.1                                           # under the zone's far edge (6.4) by 0.5 ATR
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
    assert res["level"] == 7.6 and res["entry_type"] == "limit" and res["entry"] == round(7.6 - 0.15, 2)
    assert res["stop"] == round(7.6 + 0.3, 2) and res["targets"][0]["price"] < res["entry"]


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
