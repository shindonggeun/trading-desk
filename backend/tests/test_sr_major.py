"""#328: major levels from years of weekly bars - found, merged when switched on, measured."""

import json
from pathlib import Path

from desk import sr, sr_major, sr_major_bt

FIX = Path(__file__).parent / "fixtures"


def _nvda():
    daily = json.loads((FIX / "nvda_2026-11-12.json").read_text(encoding="utf-8"))
    long = json.loads((FIX / "nvda_weekly_10y_2026-11-06.json").read_text(encoding="utf-8"))["weekly"]
    return daily, long


def test_nvda_the_2024_25_box_top_is_a_major_support_with_the_big_wave_retracement():
    _, long = _nvda()
    zone = next(m for m in sr_major.find(long, 173.73) if m["zone"][0] <= 148.8 <= m["zone"][1])
    assert zone["kind"] == "support" and zone["tier"] == "major" and zone["reactions"] >= 3
    assert "옛 저항(고점) → 지지" in zone["basis"] and "되돌림" in zone["basis"] and "라운드 넘버 150" in zone["basis"]


def test_switched_on_the_major_zone_joins_the_levels_and_leads_the_note(monkeypatch):
    fx, long = _nvda()
    monkeypatch.setattr(sr, "MAJOR_TIER", True)
    r = sr.compute(fx["daily"][:-1], fx["weekly"], None, weekly_complete=True, horizon="swing", long_weekly=long)
    major = [lv for lv in r["levels"] if lv.get("tier") == "major" and lv["kind"] == "support"]
    assert any(lv["zone"][0] <= 148.8 <= lv["zone"][1] for lv in major)
    assert all(lv.get("tier") in ("major", "minor") for lv in r["levels"])
    assert all("_core" not in lv for lv in r["levels"])
    assert sr.prompt_note(r).split("\n")[1].startswith("- MAJOR")


def test_off_by_default_the_levels_are_unchanged():
    fx, long = _nvda()
    assert not sr.MAJOR_TIER
    with_long = sr.compute(fx["daily"], fx["weekly"], None, weekly_complete=True, horizon="swing", long_weekly=long)
    without = sr.compute(fx["daily"], fx["weekly"], None, weekly_complete=True, horizon="swing")
    assert with_long["levels"] == without["levels"]


def test_majors_only_read_the_weeks_they_are_given():
    _, long = _nvda()
    cut = [w for w in long if w["at"] < "2025-03-01"]
    for m in sr_major.find(cut, cut[-1]["close"]):
        years = m["basis"].split(" ")[0]
        assert years.split("~")[1] <= "2025"


def test_a_round_number_and_a_big_wave():
    assert sr_major.round_number(142.0, 153.0) == 150.0
    assert sr_major.round_number(64000, 66000) == 65000
    bars = [{"at": f"2020-01-{i:02d}", "open": 10 + i, "high": 11 + i, "low": 9 + i, "close": 10 + i} for i in range(1, 29)]
    wave = sr_major.big_wave(bars)
    assert wave["direction"] == "up" and abs(wave["levels"][0.5] - (39 + 10) / 2) < 1e-9   # low 10 (day 1) to high 39 (day 28)


def test_the_event_study_counts_a_touch_once_and_judges_the_bounce():
    days = [{"at": f"2021-01-{i + 1:02d}", "open": 100, "high": 101, "low": 99, "close": 100} for i in range(5)]
    days += [{"at": "2021-01-06", "open": 96, "high": 97, "low": 94.5, "close": 96},
             {"at": "2021-01-07", "open": 96, "high": 99, "low": 95, "close": 98.5}]
    assert sr_major_bt.outcome(days, 4, 94.0, 95.0, atr=2.0) == ("2021-01-06", True)
    rows = [{"group": g, "touch": t, "bounced": b, "dist_atr": 2.0}
            for g in ("major", "current", "control") for t, b in (("2020-01-01", True), ("2023-01-01", False))]
    t = sr_major_bt.table(rows, "2022-01-01")
    assert t["major"]["train"]["bounce_pct"] == 100.0 and t["major"]["valid"]["bounce_pct"] == 0.0
    assert sr_major_bt.verdict(t)["beats_control"] is False
