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
