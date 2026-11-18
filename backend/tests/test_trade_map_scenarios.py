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
