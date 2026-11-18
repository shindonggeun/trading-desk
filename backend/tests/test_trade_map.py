"""Volume profile and trade map (#106)."""

import pytest

from desk import sr as srmod
from desk import trade_map, volume_profile


def _bar(low, high, volume, at="2026-10-01T00:00:00+00:00"):
    return {"at": at, "open": low, "high": high, "low": low, "close": high, "volume": volume}


def _bars():
    # 20 quiet bars across 100-120, then a heavy cluster at 108-110 and a light pass at 115-116
    bars = [_bar(100 + i % 20, 101 + i % 20, 1_000) for i in range(40)]
    bars += [_bar(108, 110, 20_000) for _ in range(10)]
    bars += [_bar(115, 116, 50) for _ in range(3)]
    return bars


def test_profile_keeps_all_volume_and_finds_the_heavy_price():
    bars = _bars()
    vp = volume_profile.profile(bars, atr=2.0, interval="1d")
    assert vp["interval"] == "1d" and vp["bars"] == len(bars)
    assert sum(b["volume"] for b in vp["bins"]) == pytest.approx(sum(b["volume"] for b in bars), rel=1e-3)
    assert 108 <= vp["poc"] <= 110
    assert vp["value_area"]["low"] <= vp["poc"] <= vp["value_area"]["high"]
    thick = [n["price"] for n in vp["nodes"] if n["kind"] == "thick"]
    assert thick and 108 <= thick[0] <= 110
    assert volume_profile.density(vp, 109) == "thick"
    assert volume_profile.density(vp, 500) is None


def test_profile_needs_volume():
    assert volume_profile.profile([_bar(1, 2, 0) for _ in range(30)]) is None
    assert volume_profile.profile([_bar(1, 2, 5) for _ in range(5)]) is None


def test_the_prompt_mentions_poc_and_nodes():
    vp = volume_profile.profile(_bars(), atr=2.0, interval="1d")
    note = srmod.prompt_note({"levels": [], "volume_profile": vp})
    assert f"POC {vp['poc']}" in note and "thick nodes" in note


SR = {
    "atr": 2.0, "reference_price": 100.0,
    "levels": [
        {"price": 104.0, "kind": "resistance", "strength": 3.5, "basis": "스윙 고점 ×2"},
        {"price": 110.0, "kind": "resistance", "strength": 2.0, "basis": "52주 고가"},
        {"price": 97.0, "kind": "support", "strength": 2.5, "basis": "20일선"},
        {"price": 93.0, "kind": "support", "strength": 1.0, "basis": "스윙 저점"},
    ],
}


def _roles(m):
    return [(s["role"], s["price"]) for s in m["steps"]]


def test_a_long_map_reads_up_for_targets_and_down_for_guards():
    plan = {"opinion": "buy", "bias": "long", "entry_price": 100.0, "stop_loss": 95.0, "price_target": 110.0,
            "entry_type": "now"}
    tm = trade_map.build(plan, SR)
    m = tm["maps"][0]
    assert m["side"] == "long"
    assert _roles(m) == [("target2", 110.0), ("target1", 104.0), ("entry", 100.0), ("guard1", 97.0), ("stop", 95.0)]
    t1 = m["steps"][1]
    assert t1["share"] == 50 and t1["then_stop"] == 100.0 and t1["rr"] == 0.8   # strength 3.5 = heavy wall
    assert m["steps"][0]["source"] == "plan" and m["steps"][0]["rr"] == 2.0
    assert m["steps"][2]["entry_type"] == "now"
    assert [s["action"] for s in m["steps"]] == ["take_rest", "take_partial", "enter", "hold_check", "exit"]


def test_a_short_map_is_the_mirror():
    plan = {"opinion": "sell", "bias": "short", "entry_price": 103.0, "stop_loss": 106.0, "price_target": None}
    m = trade_map.build(plan, SR)["maps"][0]
    assert _roles(m) == [("stop", 106.0), ("guard1", 104.0), ("entry", 103.0), ("target1", 97.0), ("target2", 93.0)]
    assert m["steps"][3]["share"] == 33


def test_a_hold_plan_gets_both_trigger_maps_with_computed_stops():
    plan = {"opinion": "hold", "bias": "flat", "entry_price": None, "long_trigger": 104.0, "short_trigger": 97.0}
    maps = trade_map.build(plan, SR)["maps"]
    assert [m["side"] for m in maps] == ["long", "short"] and all(m["origin"] == "trigger" for m in maps)
    long = dict(_roles(maps[0]))
    assert long["entry"] == 104.0 and long["target1"] == 110.0
    assert long["stop"] == 103.0  # #652 half an ATR back under the level it broke (104), not two supports down
    short = dict(_roles(maps[1]))
    assert short["target1"] == 93.0 and short["stop"] == 98.0    # half an ATR back over the broken 97


def test_without_levels_the_map_is_filled_from_atr():
    plan = {"opinion": "buy", "bias": "long", "entry_price": 50.0, "stop_loss": None, "price_target": None, "atr": 1.0}
    m = trade_map.build(plan, None)["maps"][0]
    assert dict(_roles(m)) == {"target2": 54.0, "target1": 52.0, "entry": 50.0, "stop": 48.5}
    assert {s["source"] for s in m["steps"] if s["role"] != "entry"} == {"atr"}


def test_volume_nodes_count_as_levels():
    vp = volume_profile.profile(_bars(), atr=2.0, interval="1d")
    plan = {"opinion": "buy", "bias": "long", "entry_price": 104.0, "stop_loss": 101.0, "price_target": None}
    m = trade_map.build(plan, {"atr": 2.0, "levels": [], "volume_profile": vp})["maps"][0]
    t1 = next(s for s in m["steps"] if s["role"] == "target1")
    assert t1["source"] == "volume" and t1["volume"] == "thick" and t1["share"] == 50


def test_nothing_to_anchor_is_none():
    assert trade_map.build({"opinion": "review", "bias": "review"}, SR) is None


def test_a_fake_run_plan_carries_a_trade_map(db_url):
    from tests.test_api import _client, _wait_done

    with _client(db_url) as client:
        rid = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-03"}).json()["id"]
        run = _wait_done(client, rid)
    tm = run["plan"]["trade_map"]
    assert tm["maps"] and all(m["steps"] for m in tm["maps"])
    prices = [s["price"] for s in tm["maps"][0]["steps"]]
    assert prices == sorted(prices, reverse=True)   # highest first, whichever side


def test_a_stop_on_the_wrong_side_is_set_aside_and_said():
    plan = {"opinion": "buy", "bias": "long", "entry_price": 100.0, "stop_loss": 105.0, "price_target": 98.0}
    m = trade_map.build(plan, SR)["maps"][0]
    assert m["ignored"] == {"stop_loss": 105.0, "price_target": 98.0}
    roles = dict(_roles(m))
    assert roles["stop"] < 100.0 and roles["target1"] == 104.0 and roles["target2"] == 110.0
    prices = [s["price"] for s in m["steps"]]
    assert prices == sorted(prices, reverse=True)
    assert all(s["source"] != "plan" for s in m["steps"] if s["role"] != "entry")


def test_a_plan_target_further_out_becomes_the_second_target_and_guards_past_the_stop_drop():
    plan = {"opinion": "buy", "bias": "long", "entry_price": 97.4, "stop_loss": 95.5, "price_target": 110.0}
    m = trade_map.build(plan, SR)["maps"][0]
    assert dict(_roles(m))["target1"] == 104.0 and dict(_roles(m))["target2"] == 110.0
    assert [r for r, _ in _roles(m) if r.startswith("guard")] == []   # 97 is inside the entry's own zone, 93 past the stop
    t1 = next(s for s in m["steps"] if s["role"] == "target1")
    assert t1["heavy"] is True and t1["share"] == 50


def test_a_demoted_plan_without_triggers_maps_its_bias():
    plan = {"opinion": "hold", "bias": "long", "entry_price": 100.0, "stop_loss": 95.0, "price_target": 110.0}
    assert trade_map.build(plan, SR)["maps"][0]["side"] == "long"


def test_no_target_below_zero_on_a_cheap_short():
    plan = {"opinion": "sell", "bias": "short", "entry_price": 4.0, "stop_loss": None, "price_target": None, "atr": 1.2}
    m = trade_map.build(plan, None)["maps"][0]
    assert all(s["price"] > 0 for s in m["steps"])
    assert "target2" not in dict(_roles(m))


def test_a_bad_bar_drops_only_itself():
    bars = _bars() + [_bar(130, 120, 999), {"at": "x", "high": float("nan"), "low": 1.0, "volume": 5}]
    vp = volume_profile.profile(bars, atr=2.0)
    assert vp["bars"] == len(_bars()) and vp["high"] <= 120


def test_gap_density_between_targets():
    vp = volume_profile.profile(_bars(), atr=2.0)
    assert volume_profile.gap_thin(vp, 112.0, 119.0) is True     # only quiet bars up there
    assert volume_profile.gap_thin(vp, 107.0, 111.0) is False    # the heavy cluster
