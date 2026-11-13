"""#321: support and resistance as zones with a record - width, held visits, role change, sweeps."""

import json
from datetime import date, timedelta
from pathlib import Path

from desk import sr, sr_zones

FIXTURE = Path(__file__).parent / "fixtures" / "nvda_2026-11-12.json"


def _bar(i, o, h, lo, c):
    return {"at": (date(2026, 1, 1) + timedelta(days=i)).isoformat() + "T05:00:00+00:00",
            "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def test_nvda_on_2026_11_12_the_old_resistance_near_150_is_a_flipped_support_swept_and_reclaimed():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    r = sr.compute(fx["daily"], fx["weekly"], None, weekly_complete=True, horizon="swing")
    near = min((lv for lv in r["levels"] if lv["kind"] == "support"), key=lambda lv: abs(lv["price"] - 150))
    lo, hi = near["zone"]
    assert lo <= 148.8 <= hi and 145 < near["price"] < 155
    assert near["flipped"] == "resistance_to_support" and "저항→지지 전환" in near["basis"]
    assert near["buy_zone"]["stop"] < 148.8              # under the wick - the analysis' 151.66 sat inside the zone
    assert {"kind": "sweep_reclaim", "side": "support", "level": near["price"]}.items() <= r["events"][-1].items()
    assert r["events"][-1]["at"].startswith("2026-11-12")
    note = sr.prompt_note(r)
    assert "below the zone's low" in note and "took out the prior lows into the support zone" in note


def test_a_zone_spans_wick_and_near_body_but_is_clipped_around_its_level():
    lo, hi = sr_zones.zone_of([(100.0, 101.0), (100.4, None)], atr=4.0, level=100.2)
    assert (lo, hi) == (100.0, 101.0)
    lo, hi = sr_zones.zone_of([(100.0, 110.0)], atr=4.0, level=100.0)   # a body too far from its wick is left out
    assert hi - lo == 0.2 * 4.0 or abs(hi - lo - 0.8) < 1e-9
    lo, hi = sr_zones.zone_of([(90.0, None), (110.0, None)], atr=4.0, level=100.0)
    assert (lo, hi) == (97.0, 103.0)                                   # clipped at 0.75 ATR either side


def test_reactions_count_only_resolved_visits_and_judge_hold_or_break():
    bars = [_bar(0, 110, 111, 109, 110),
            _bar(1, 110, 110, 100.5, 102),        # into the 100-102 zone from above
            _bar(2, 102, 104, 101, 103.5),
            _bar(3, 103.5, 106, 103, 105.5),      # back out by 1 ATR (ATR 3): held
            _bar(4, 105, 105, 101, 101.5),        # in again
            _bar(5, 101, 101.5, 98, 99)]          # closed 0.25 ATR under: broke
    rec = sr_zones.reactions(bars, 100.0, 102.0, atr=3.0)
    assert rec["support"] == [2, 1] and rec["order"] == [("support", True), ("support", False)]
    pending = sr_zones.reactions(bars[:3], 100.0, 102.0, atr=3.0)
    assert pending["support"] == [0, 0]                                 # still playing out: not judged


def test_polarity_and_buy_zone_needs_a_record_and_room():
    assert sr_zones.flipped("support", {"resistance"}) == "resistance_to_support"
    assert sr_zones.flipped("resistance", {"support"}) == "support_to_resistance"
    assert sr_zones.flipped("support", {"support"}) is None
    sup = {"kind": "support", "price": 100.0, "zone": [99.0, 101.0], "touches": 5, "held": 4, "hold_rate": 0.8,
           "flipped": "resistance_to_support"}
    bz = sr_zones.buy_zone(sup, [{"kind": "resistance", "zone": [106.0, 107.0]}], atr=2.0)
    assert bz["stop"] == 98.5 and bz["target"] == 106.0 and bz["ok"] and "저항→지지 전환" in bz["why"]
    thin = sr_zones.buy_zone({**sup, "touches": 1, "held": 1}, [{"kind": "resistance", "zone": [106.0, 107.0]}], 2.0)
    assert not thin["ok"] and "근거 부족" in thin["note"]
    near = sr_zones.buy_zone(sup, [{"kind": "resistance", "zone": [101.2, 102.0]}], 2.0)
    assert not near["ok"] and "손익비" in near["note"]


def test_old_saved_levels_without_zones_still_make_a_note():
    note = sr.prompt_note({"reference_price": 100, "atr": 2, "levels": [
        {"price": 95, "kind": "support", "timeframe": "1d", "basis": "스윙 저점", "strength": 1.0}]})
    assert "S 95" in note and "zone" not in note.split("\n")[1]


def test_the_playbook_zone_carries_the_sr_zone_floor_for_the_stop():
    from desk import playbook

    cands = [{"price": 150.0, "source": "sr", "weight": 2.0, "basis": "주봉 스윙 고점", "zone": [148.6, 153.1]},
             {"price": 151.0, "source": "vp", "weight": 1.0, "basis": "매물대"}]
    z = playbook.zones(cands, ref=162.6, atr=9.9, below=True, max_atr=6.0)[0]
    assert z["floor"] == 148.6 and z["low"] > z["floor"]
    assert playbook.ZONE_STOP                         # the stop goes under the floor (held-out R better, #321)


def test_a_pullback_into_a_zone_is_no_sweep_and_a_forming_bar_is_not_judged():
    lv = {"kind": "support", "price": 101.0, "zone": [100.0, 102.0]}
    bars = [_bar(i, 105, 106, 103 + i * 0.1, 105) for i in range(5)]
    stuck = [*bars, _bar(5, 104, 104, 101.8, 101.9)]                 # into the zone, closed inside it: no reclaim
    assert sr_zones.sweeps(stuck, [lv]) == []
    shallow = [*bars, _bar(5, 104, 104, 103.05, 104)]                # above the zone: no stops of the zone run
    assert sr_zones.sweeps(shallow, [lv]) == []
    into = sr_zones.sweeps([*bars, _bar(5, 104, 104, 101.8, 103)], [lv])[0]
    assert into["kind"] == "sweep_reclaim" and not into["through"]
    assert sr_zones.sweeps([*bars, _bar(5, 104, 104, 99.0, 103)], [lv])[0]["through"]
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    live = sr.compute(fx["daily"], fx["weekly"], 162.6, weekly_complete=True, horizon="swing", partial=True)
    assert not any(e["at"].startswith("2026-11-12") for e in live["events"])   # the session may still close below
