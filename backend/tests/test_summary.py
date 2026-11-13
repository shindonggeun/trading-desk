"""#221: the one card - verdict, split buy, the single price, stop, targets."""

from desk import summary


def _long(**over) -> dict:
    side = {
        "setup": "dip_catch", "score": 70, "avg_entry": 99.0, "stop": 94.0,
        "zone": {"low": 96.0, "high": 100.0, "members": [
            {"price": 100.0, "source": "sr", "basis": "일봉 지지", "weight": 1.0},
            {"price": 97.2, "source": "vp", "basis": "가치영역 하단", "weight": 1.5},
            {"price": 97.0, "source": "fib", "basis": "피보나치 0.618", "weight": 1.0},
            {"price": 96.9, "source": "avwap", "basis": "앵커드 VWAP", "weight": 1.0},
        ]},
        "entries": [{"step": 1, "price": 100.0, "share": 0.25, "kind": "limit"},
                    {"step": 2, "price": 98.0, "share": 0.25, "kind": "limit", "held": "event"},
                    {"step": 3, "price": 101.0, "share": 0.5, "kind": "stop"}],
        "targets": [{"role": "t1", "price": 108.0, "rr": 1.8}, {"role": "t2", "price": 115.0, "rr": 3.2}],
        "calibration": {"p_t1": 0.55, "exp_r": 0.05, "n": 120, "thin": False, "negative": False},
        "time_stop": {"bars": 5, "interval": "1d"},
    }
    return {**side, **over}


def _pb(ref=103.0, long=None, short=None, **over) -> dict:
    return {"frame": {"reference_price": ref, "atr": 2.0, "atr_zone": 2.0},
            "long": long if long is not None else _long(),
            "short": short if short is not None else {"setup": "none", "reason": "숏 근거 없음", "entries": []}, **over}


def test_a_long_plan_above_its_zone_waits_and_shows_the_ladder_and_the_single_price():
    card = summary.build({"bias": "long", "rating": "BUY", "valid_until": "2026-11-10"}, _pb())
    assert card["side"] == "long" and card["verdict"] == "wait_for_zone" and "기다렸다" in card["text"]
    assert [s["price"] for s in card["ladder"]] == [100.0, 98.0, 101.0]
    assert [s["when"] for s in card["ladder"]] == ["지정가", "경제 발표 지나간 뒤", "되찾기 확인 뒤(스톱 진입)"]
    one = card["single_entry"]
    # 97.2 · 97.0 · 96.9 overlap within 0.25 ATR (0.5): the most evidence; the price is their weighted centre
    centre = round((97.2 * 1.5 + 97.0 + 96.9) / 3.5, 2)
    assert one["price"] == centre and one["rr"] >= summary.MIN_RR and one["weight"] == 3.5
    assert one["why"] == ["가치영역 하단", "피보나치 0.618", "앵커드 VWAP"] and one["p_t1"] == 0.55
    assert "분할이 낫다" in one["note"]              # the backtest said so (desk.backtest.entry_style)
    assert card["stop"] == 94.0 and [t["role"] for t in card["targets"]] == ["t1", "t2"]
    assert card["distance_pct"] == round((centre / 103 - 1) * 100, 2) and card["valid_until"] == "2026-11-10"
    assert card["record"]["n"] == 120


def test_inside_the_zone_is_buy_now_and_a_short_mirrors_it():
    assert summary.build({"bias": "long"}, _pb(ref=99.0))["verdict"] == "buy_now"
    short = {"setup": "fade", "score": 60, "avg_entry": 110.0, "stop": 114.0,
             "zone": {"low": 109.0, "high": 112.0, "members": [{"price": 110.0, "basis": "저항", "weight": 2.0}]},
             "entries": [{"step": 1, "price": 110.0, "share": 1.0, "kind": "limit"}],
             "targets": [{"role": "t1", "price": 103.0, "rr": 1.75}]}
    card = summary.build({"bias": "short"}, _pb(ref=104.0, short=short))
    assert card["side"] == "short" and card["verdict"] == "wait_short"
    assert card["single_entry"]["price"] == 110.0 and card["single_entry"]["rr"] == 1.75
    assert summary.build({"bias": "short"}, _pb(ref=110.5, short=short))["verdict"] == "short_now"


def test_no_single_price_below_the_minimum_reward_to_risk():
    tight = _long(targets=[{"role": "t1", "price": 101.0, "rr": 0.5}])
    assert summary.build({"bias": "long"}, _pb(long=tight))["single_entry"] is None


def test_stand_aside_when_there_is_no_setup_or_the_plan_side_has_none_or_it_broke():
    flat = summary.build({"bias": "flat"}, _pb(long={"setup": "none", "reason": "롱 근거 없음", "entries": []}))
    assert flat["verdict"] == "stand_aside" and flat["why"] == "롱 근거 없음"
    against = summary.build({"bias": "short"}, _pb())          # the plan says short, the desk has no short
    assert against["verdict"] == "stand_aside" and "데스크 타점이 없다" in against["why"]
    broken = summary.build(None, _pb(long=_long(state="invalid")))
    assert broken["verdict"] == "stand_aside" and "무효" in broken["why"]
    assert summary.build({"bias": "long"}, None) is None


def test_the_live_page_picks_the_stronger_side_and_names_the_holds():
    pb = _pb(event_hold={"label": "CPI"})
    pb["long"]["gate"] = {"text": "투매 진행 — 1차만"}
    card = summary.build(None, pb)
    assert card["side"] == "long" and card["notes"] == ["투매 진행 — 1차만", "CPI 발표 전후 — 1차만"]
    thin = summary.build(None, _pb(long=_long(calibration={"thin": True, "n": 4})))
    assert thin["single_entry"]["thin"] is True and thin["single_entry"]["p_t1"] is None


def test_the_single_arm_buys_it_all_at_the_single_price():
    side = _long()
    one = summary.as_single(side, True, 2.0)
    price = summary.single_entry(side, True, 2.0)["price"]
    assert one["entries"] == [{"step": 1, "price": price, "share": 1.0, "kind": "limit"}]
    assert one["avg_entry"] == price and one["stop"] == side["stop"] and one["targets"] == side["targets"]
    assert summary.as_single(_long(targets=[{"role": "t1", "price": 101.0}]), True, 2.0) is None


def test_a_hold_plan_reads_watch_and_shows_the_better_side_as_if_you_go_in():
    """#231: the SOXL Hold run read "구역까지 기다렸다가 산다" under a 관망 header."""
    card = summary.build({"bias": "flat", "rating": "HOLD"}, _pb())
    assert card["verdict"] == "watch" and card["text"].startswith("관망") and "롱" in card["text"]
    assert card["side"] == "long" and card["ladder"] and "계획은 관망" in card["why"]
    assert summary.build(None, _pb())["verdict"] == "wait_for_zone"     # the live page has no plan to follow


def test_a_ladder_whose_t1_pays_under_1r_is_not_a_buy():
    """#231: T1 46.33 just above the 45.97 entry, stop -43% - rr 0.06 read as a buy."""
    poor = _long(targets=[{"role": "t1", "price": 99.3, "rr": 0.06}, {"role": "t2", "price": 105.0, "rr": 1.2}])
    for plan in ({"bias": "long"}, {"bias": "flat"}, None):
        card = summary.build(plan, _pb(long=poor))
        assert card["verdict"] == "stand_aside" and "손익비" in card["text"] and "0.06" in card["why"]
        assert card["side"] == "long" and card["stop"] == 94.0        # the numbers stay, for reference


def _gdxu() -> tuple[dict, dict]:
    """#331: GDXU 2026-11-13 - 71.85, stand aside (T1 0.76R), triggers 75.05 / 64.39, a 3rd step at 74.99."""
    plan = {"bias": "flat", "rating": "HOLD", "entry_price": 68.54, "stop_loss": 59.65, "price_target": 88.16,
            "long_trigger": 75.05, "short_trigger": 64.39}
    long = _long(avg_entry=71.2, stop=59.78, zone={"low": 66.0, "high": 72.5, "members": []},
                 entries=[{"step": 1, "price": 72.3, "share": 0.25, "kind": "limit"},
                          {"step": 2, "price": 67.41, "share": 0.25, "kind": "limit"},
                          {"step": 3, "price": 74.99, "share": 0.5, "kind": "stop"}],
                 targets=[{"role": "t1", "price": 77.59, "rr": 0.76}, {"role": "t2", "price": 88.22, "rr": 2.1}])
    pb = _pb(ref=71.85, long=long)
    pb["frame"]["primary"] = "1d"
    return plan, pb


def test_the_action_says_what_to_do_now_and_on_which_close():
    plan, pb = _gdxu()
    card = summary.build(plan, pb)
    act = card["action"]
    assert act["now"] == "관망" and card["reference"] is True
    assert act["sentence"].startswith("지금 71.85 — 관망.")
    assert "일봉 종가가 75.05 위면 롱(손절 59.78 · 1차 77.59 · 손익비 0.17 — 낮음)" in act["sentence"]
    assert "일봉 종가가 64.39 아래면 숏" in act["sentence"]
    assert [(t["side"], t["price"], t["confirm"]) for t in act["triggers"]] == [("long", 75.05, "close"), ("short", 64.39, "close")]
    assert act["triggers"][0]["then"] == {"entry": 75.05, "stop": 59.78, "t1": 77.59, "rr": 0.17}
    assert act["triggers"][0]["confirm_label"] == "일봉 종가"
    pb["frame"]["primary"] = "60m"
    assert "60분봉 마감이 75.05 위면 롱" in summary.build(plan, pb)["action"]["sentence"]


def test_a_triggers_stop_and_target_never_point_the_wrong_way():
    plan, pb = _gdxu()
    pb["short"] = {"setup": "fade", "stop": 63.0, "entries": [{"step": 1, "price": 74.0, "share": 1.0, "kind": "limit"}],
                   "targets": [{"role": "t1", "price": 66.0, "rr": 1.0}, {"role": "t2", "price": 60.0, "rr": 2.0}]}
    short = summary.build(plan, pb)["action"]["triggers"][1]
    # the fade's stop 63 is below the 64.39 break and its T1 66 above it: neither fits a short on the break
    assert short["then"] == {"entry": 64.39, "stop": None, "t1": 60.0, "rr": None}
    high = {**plan, "long_trigger": 90.0}
    assert summary.build(high, pb)["action"]["triggers"][0]["then"]["t1"] is None   # no desk target past 90


def test_the_ladder_splits_dip_buys_from_adds_on_confirmation():
    plan, pb = _gdxu()
    split = summary.build(plan, pb)["ladder_split"]
    assert [(e["price"], e["label"]) for e in split["dip"]] == [(72.3, "눌림 1차"), (67.41, "눌림 2차")]
    assert [(e["price"], e["label"]) for e in split["confirm"]] == [(74.99, "확인 추가")]
    short = summary.split_ladder([{"price": 10, "kind": "limit"}, {"price": 11, "kind": "limit"}, {"price": 9, "kind": "stop"}],
                                 up=False)
    assert [e["price"] for e in short["dip"]] == [10, 11] and [e["price"] for e in short["confirm"]] == [9]


def test_the_plans_own_numbers_go_to_alt_when_they_differ_from_the_desks():
    plan, pb = _gdxu()
    alt = summary.build(plan, pb)["alt"]
    assert alt["text"] == "AI 계획 진입 68.54 · 손절 59.65 · 목표 88.16 (데스크와 다름 — 카드는 데스크 숫자)"
    assert alt["entry_diff_pct"] == -3.74                # 68.54 vs the desk's average 71.2
    assert alt["stop_diff_pct"] == -0.22 and alt["target_diff_pct"] == -0.07   # vs 59.78 and T2 88.22
    same = {**plan, "entry_price": 71.0, "stop_loss": 59.8, "price_target": 77.5}
    assert summary.build(same, pb)["alt"] is None
    assert summary.build({"bias": "flat"}, pb)["alt"] is None       # no numbers in the plan
    short_numbers = {**plan, "entry_price": 70.0, "stop_loss": 75.0, "price_target": 60.0}
    assert summary.build(short_numbers, pb)["alt"] is None            # a short plan vs the long card


def test_a_buy_now_card_is_an_instruction_and_an_old_card_keeps_its_fields():
    card = summary.build({"bias": "long"}, _pb(ref=99.5))
    assert card["verdict"] == "buy_now" and card["reference"] is False and card["alt"] is None
    assert card["action"]["sentence"] == "지금 99.5 — 지금 매수. 100.0부터 나눠서 매수(손절 94.0, 1차 108.0)."
    assert card["ladder"] and {"verdict", "text", "stop", "targets"} <= set(card)
    aside = summary.build({"bias": "long"}, _pb(long={"setup": "none", "entries": []}))
    assert aside["action"]["now"] == "관망" and aside["ladder_split"] is None and aside["reference"] is True
