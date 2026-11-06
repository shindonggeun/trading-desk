"""#151: right after a crash to a new low the playbooks trade the levels the crash made, near the
price - not the old range 50% above it - and far levels are flagged instead of offered."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from desk import horizons, outcome, playbook
from desk.levels import entry_vs_live, triggers_vs_live

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ram_2026-11-05.json").read_text(encoding="utf-8"))
LIVE = {"price": 5.13, "source": "toss", "fetched_at": "2026-11-06T04:52:00+00:00"}


def _ram(price: float = 5.13) -> dict:
    h = horizons.get("swing")
    daily = FIXTURE["daily"]
    return playbook.build(FIXTURE["sr"], daily, daily, h, live={**LIVE, "price": price})


def test_ram_after_the_crash_trades_the_levels_the_crash_made():
    pb = _ram()
    assert pb["regime"]["id"] == "crash"
    assert pb["shock"]["down"] and pb["shock"]["change"] < -0.3 and pb["shock"]["low"] == 4.84
    long, short = pb["long"], pb["short"]
    # the dip catch sits at the crash low, a few % under the price - not "none", not 8.4
    assert long["setup"] == "dip_catch"
    assert long["zone"]["low"] <= 4.84 <= long["zone"]["high"]
    assert abs(long["entries"][0]["price"] / 5.13 - 1) < 0.15
    assert 4.2 < long["stop"] < long["zone"]["low"]
    assert [t["basis"] for t in long["targets"]] == ["급락일 중간값", "급락일 시가"]
    # the short fades a bounce into the gap the crash left (its open / high), not the 7.75-8.4 range
    assert 5.9 <= short["zone"]["low"] and short["zone"]["high"] <= 6.5
    assert short["entries"][0]["price"] < short["zone"]["low"] and short["stop"] > short["zone"]["high"]
    assert short["targets"][0]["price"] < short["entries"][0]["price"]
    assert all(t["price"] > 0 for t in short["targets"])
    for side in (long, short):
        assert side["reach"]["reachable"] and abs(side["reach"]["gap_pct"]) < 25
    # nothing in any zone is further than the swing cap (25%) from the price
    for side in (long, short):
        assert all(abs(m["price"] / 5.13 - 1) <= 0.25 for m in side["zone"]["members"])
    assert pb["frame"]["zone_cap_pct"] == 25.0 and pb["frame"]["atr_zone"] < pb["frame"]["atr"]


def test_under_the_crash_low_the_long_waits_for_the_reclaim():
    pb = _ram(4.50)
    long = pb["long"]
    assert long["setup"] == "dip_catch" and long.get("variant") == "reclaim"
    assert all(e["kind"] == "stop" and e["price"] > 4.84 and "reclaim" in e["after"] for e in long["entries"])
    assert long["stop"] < 4.50
    # scored: bars that stay under the low buy nothing (no limit above the market fills at once)
    rows = [{"at": f"2026-11-0{6 + i}T05:00:00+00:00", "open": 4.5, "high": 4.6, "low": 4.4, "close": 4.5,
             "volume": 1} for i in range(3)]
    r = outcome.judge_playbook(long, 1, rows, horizons.get("swing"), "2026-11-06T04:00:00+00:00")
    assert not r.get("filled_shares") and r["status"] != "stop"


@pytest.mark.parametrize("price", [5.13, 4.95, 4.80, 4.50])
def test_no_buy_limit_ever_sits_above_the_price(price):
    for e in _ram(price)["long"]["entries"]:
        assert e["kind"] != "limit" or e["price"] <= price


def test_reach_follows_the_price_and_says_why():
    pb = _ram()
    assert pb["long"]["reach"]["gap_pct"] < 0 and pb["long"]["reach"]["reason"] is None
    far = playbook.refresh(pb, {"price": 8.0})           # a squeeze away from the ladder
    assert far["long"]["reach"]["reason"] == "too_far" and not far["long"]["reach"]["reachable"]
    assert far["frame"]["reference_price"] == 8.0


def test_the_note_names_the_shock_session_and_the_reach():
    note = playbook.prompt_note(_ram())
    assert "Shock session 2026-11-05" in note and "Reach: levels further than 25%" in note


def test_shock_needs_a_big_move_for_the_instrument():
    calm = [{"at": f"2026-01-{i + 1:02d}", "open": 100, "high": 101, "low": 99, "close": 100 + (i % 2) * 0.5,
             "volume": 1} for i in range(30)]
    assert playbook.shock(calm, 2.0) is None
    crash = [*calm, {"at": "2026-02-01", "open": 92, "high": 93, "low": 85, "close": 86, "volume": 5}]
    s = playbook.shock(crash, 2.0)
    assert s and s["down"] and s["bar"]["low"] == 85
    bases = {c["basis"] for c in playbook.shock_candidates(s)}
    assert {"급락일 저가", "급락일 고가", "급락일 시가", "급락일 중간값", "급락일 전 종가", "갭 상단(전일 저가)"} <= bases


@pytest.mark.parametrize(("ref", "expected"), [(5.13, [4.5, 5.0, 5.5, 6.0]), (188800, [170000, 180000, 190000, 200000])])
def test_round_numbers_scale_with_the_price(ref, expected):
    assert [c["price"] for c in playbook.round_levels(ref, ref)] == expected


def test_round_numbers_alone_never_make_a_zone():
    cands = [{"price": 5.0, "source": "sr", "weight": 0.5, "basis": "라운드 넘버", "tag": "round"},
             {"price": 4.5, "source": "sr", "weight": 0.5, "basis": "라운드 넘버", "tag": "round"}]
    assert playbook.zones(cands, 5.13, 0.7, below=True, max_atr=5) == []
    real = [*cands, {"price": 4.84, "source": "sr", "weight": 2.0, "basis": "급락일 저가"}]
    zone = playbook.zones(real, 5.13, 0.7, below=True, max_atr=5)[0]
    assert zone["low"] > 4.5            # the round number scores, it does not stretch the zone


def test_a_far_entry_is_flagged_even_when_the_entry_is_the_only_anchor():
    # the RAM run: the model's short at 7.64 / stop 8.73 / target 5.42, the price 5.13
    plan = {"bias": "short", "entry_price": 7.64, "stop_loss": 8.73, "price_target": 5.42,
            "live_price": {"price": 5.13}}
    out = entry_vs_live(plan, 1.14, last_close=4.87)["entry_vs_live"]
    assert out["far"] and out["target_passed"] and out["gap_pct"] > 40
    assert entry_vs_live(plan, 1.14)["entry_vs_live"]["far"]      # no last close: still judged


def test_far_triggers_are_watch_levels():
    plan = {"long_trigger": 8.4, "short_trigger": 4.84, "live_price": {"price": 5.13}}
    out = triggers_vs_live(plan, 1.14)["triggers_vs_live"]
    assert out["long"]["far"] and out["long"]["gap_pct"] > 60
    assert not out["long"]["reachable"] and out["long"]["reason"] == "too_far"
    assert not out["short"]["far"] and out["short"]["reachable"]
    # the horizon's reach: the 6.22 gap bottom (+21%) is in reach on a swing (25%), not on a day trade (6%)
    gap = {"long_trigger": 6.22, "live_price": {"price": 5.13}}
    assert triggers_vs_live(gap, 0.7, playbook.MAX_ZONE_PCT["swing"])["triggers_vs_live"]["long"]["reachable"]
    assert not triggers_vs_live(gap, 0.7, playbook.MAX_ZONE_PCT["intraday"])["triggers_vs_live"]["long"]["reachable"]
    assert "triggers_vs_live" not in triggers_vs_live({"long_trigger": 8.4}, 1.14)   # no live price


def test_an_intraday_horizon_keeps_its_small_reach_after_the_crash():
    # 60-minute bars of the day after the crash, daily bars with the shock behind them
    daily = FIXTURE["daily"]
    hourly = [{"at": f"2026-11-06T{14 + i // 2:02d}:{(i % 2) * 30:02d}:00+00:00", "open": 5.1 + 0.01 * (i % 5),
               "high": 5.2 + 0.01 * (i % 5), "low": 5.0 + 0.01 * (i % 5), "close": 5.12, "volume": 1000}
              for i in range(60)]
    sr = {**FIXTURE["sr"], "atr": 0.12}
    pb = playbook.build(sr, daily, hourly, horizons.get("short_swing"), live=LIVE)
    cap = pb["frame"]["zone_cap_pct"]
    assert cap <= 12.0
    for side in ("long", "short"):
        s = pb[side]
        if s["setup"] != "none":
            assert all(abs(m["price"] / 5.13 - 1) * 100 <= cap + 0.01 for m in s["zone"]["members"])
            assert all(abs(t["price"] / 5.13 - 1) <= 2 * cap / 100 + 0.01 for t in s["targets"] if t["source"] == "desk")
