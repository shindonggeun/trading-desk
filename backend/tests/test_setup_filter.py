"""#285: setups kept only where the chart showed an edge, and the score on those edges."""

import pytest

from desk import calibration, entry_features, horizons, playbook, setup_filter


def _side(setup: str, direction: str = "with") -> dict:
    return {"setup": setup, "reason": None, "size_hint": "normal", "flat_by_close": False, "direction": direction,
            "zone": {"low": 95.0, "high": 97.0, "mid": 96.0, "score": 6.0},
            "entries": [{"step": 1, "price": 97.0, "share": 0.5}], "avg_entry": 96.0, "stop": 94.0,
            "targets": [{"role": "t1", "price": 100.0}], "score": 80,
            "conditions": [{"id": "in_zone", "met": True, "how": "computed"},
                           {"id": "higher_low", "met": False, "how": "computed"},
                           {"id": "tape_hh_hl", "met": None, "how": "watch"}]}


def _pb(long: str = "pullback", short: str = "fade") -> dict:
    return {"regime": {"id": "uptrend"}, "frame": {"atr": 2.0}, "long": _side(long), "short": _side(short)}


@pytest.fixture
def chart(monkeypatch):
    f = {}
    monkeypatch.setattr(entry_features, "compute", lambda daily, weekly=None, risk=None: dict(f))
    return f


def test_a_weak_pullback_and_a_fade_are_dropped_with_the_reason(chart):
    chart.update(rel20=0.02, ret20=0.05, slope20=0.01, vol_ratio=1.2)
    out = setup_filter.apply(_pb(), [{}], None, None, "swing", calibrate=False)
    long, short = out["long"], out["short"]
    assert long["setup"] == "none" and long["filtered"]["setup"] == "pullback"
    assert long["filtered"]["rule"] == "pullback_strength" and long["filtered"]["values"] == {"rel20": 0.02, "ret20": 0.05}
    assert long["reason"] == long["filtered"]["reason"] and "+2.0%" in long["reason"] and long["direction"] == "with"
    assert long["entries"] == [] and long["score"] == 0                  # the page's one shape, empty
    assert short["setup"] == "none" and short["filtered"]["rule"] == "fade_off"


def test_a_strong_pullback_stays_and_is_scored_on_its_edges(chart):
    chart.update(rel20=0.20, ret20=0.05, slope20=0.06, vol_ratio=0.9)
    out = setup_filter.apply(_pb(), [{}], None, None, "swing", calibrate=False)
    long = out["long"]
    assert long["setup"] == "pullback" and "filtered" not in long
    edges = {c["id"]: c["met"] for c in long["conditions"] if c["how"] == "edge"}
    assert edges == {"edge_rel_strength": True, "edge_trend": True, "edge_volume": False}
    # two of three edges: the 60-79 band; the rest (zone 6 → 60, one of two computed → 20 = 80) places it
    assert long["score"] == 75
    again = setup_filter.apply(out, [{}], None, None, "swing", calibrate=False)
    assert again["long"]["conditions"] == long["conditions"]           # idempotent: the edges once
    chart.update(ret20=0.25, rel20=None)                                # no index: its own run is enough
    assert setup_filter.apply(_pb(), [{}], None, None, "swing", calibrate=False)["long"]["setup"] == "pullback"


def test_a_dip_catch_on_a_dry_day_is_dropped_but_no_volume_is_not_a_reason(chart):
    chart.update(vol_ratio=0.5)
    out = setup_filter.apply(_pb("dip_catch", "none"), [{}], None, None, "swing", calibrate=False)
    assert out["long"]["filtered"]["rule"] == "dip_dry_volume" and "0.50배" in out["long"]["reason"]
    chart.update(vol_ratio=None)
    out = setup_filter.apply(_pb("dip_catch", "none"), [{}], None, None, "swing", calibrate=False)
    assert out["long"]["setup"] == "dip_catch"


def test_other_horizons_value_swing_and_the_switch_are_left_alone(chart, monkeypatch):
    chart.update(rel20=0.0, ret20=0.0, vol_ratio=0.1)
    pb = _pb()
    assert setup_filter.apply(pb, [{}], None, None, "intraday") is pb
    vs = setup_filter.apply(_pb("value_swing", "none"), [{}], None, None, "swing", calibrate=False)
    assert vs["long"]["setup"] == "value_swing" and vs["long"]["score"] == 80
    monkeypatch.setattr(setup_filter, "ENABLED", False)
    assert setup_filter.apply(pb, [{}], None, None, "swing") is pb


def test_the_score_bands_follow_the_number_of_edges_met():
    base = [{"id": "in_zone", "met": True, "how": "computed"}]
    scores = []
    for met in range(4):
        edges = [{"id": f"e{i}", "met": i < met, "how": "edge"} for i in range(3)]
        lo = playbook._score(0, [{"id": "in_zone", "met": False, "how": "computed"}, *edges])
        hi = playbook._score(6, base + edges)
        scores.append((calibration.band(lo), calibration.band(hi)))
    assert scores == [(0, 0), (40, 40), (60, 60), (80, 80)]            # the zone and the rest never cross a band
    assert playbook._score(6, base) == 100                              # without edges: as before


def test_the_live_record_is_read_at_the_new_score(chart):
    chart.update(rel20=0.3, ret20=0.3, slope20=0.1, vol_ratio=2.0)
    cells = {}
    for _ in range(40):
        calibration.add(cells, "swing", "long", "pullback", "uptrend", 95, True, 1.0)
        calibration.add(cells, "swing", "long", "pullback", "uptrend", 70, False, -1.0)
    table = calibration.Table(cells)
    out = setup_filter.apply(_pb(short="none"), [{}], None, None, "swing", calibrate=False)
    playbook.calibrate_sides(out, horizons.get("swing"), table)
    assert out["long"]["score"] >= 80 and out["long"]["exp_r"] > 0


def test_a_tracked_analysis_keeps_the_edges_it_was_scored_on(chart):
    chart.update(rel20=0.2, ret20=0.2, slope20=0.1, vol_ratio=2.0)
    run_pb = setup_filter.apply(_pb(short="none"), [{}], None, None, "swing", calibrate=False)
    run_pb["frame"] = {"atr": 2.0, "reference_price": 98.0}
    bars = [{"at": f"2026-01-{i + 1:02d}", "open": 98.0, "high": 99.0, "low": 97.0, "close": 98.0, "volume": 100}
            for i in range(30)]
    fresh = {"regime": {"id": "uptrend", "flags": {"climax_recent": False}}, "frame": dict(run_pb["frame"]),
             "long": {"setup": "none"}, "short": {"setup": "none"}}
    out = playbook.track(fresh, run_pb, bars, horizons.get("swing"))
    assert [c["id"] for c in out["long"]["conditions"] if c["how"] == "edge"] == \
        ["edge_rel_strength", "edge_trend", "edge_volume"]
    assert out["long"]["score"] >= 80


# --- #432: a session still trading is not a whole day's volume ---------------------------------------


def test_before_the_open_todays_volume_is_not_judged(chart):
    chart.update(vol_ratio=0.06, rel20=0.20, ret20=0.05, slope20=0.06)
    out = setup_filter.apply(_pb("dip_catch", "none"), [{}], None, None, "swing", calibrate=False, elapsed=0.0)
    long = out["long"]
    assert long["setup"] == "dip_catch" and "filtered" not in long and "0.06" not in (long["reason"] or "")
    vol = next(c for c in long["conditions"] if c["id"] == "edge_volume")
    assert vol["met"] is None and vol["pending"] and "판정 안 함" in vol["label"]
    assert "집계 중" in out["volume_note"]
    # an edge not judged is an edge not met: two of three, the 60 band - never the "all three" one
    assert calibration.band(long["score"]) == 60


def test_during_the_session_volume_is_scaled_by_the_time_passed(chart):
    chart.update(vol_ratio=0.3)
    half = setup_filter.apply(_pb("dip_catch", "none"), [{}], None, None, "swing", calibrate=False, elapsed=0.5)
    assert half["long"]["filtered"]["values"] == {"vol_ratio": 0.6} and "환산 0.60배" in half["volume_note"]
    chart.update(vol_ratio=0.5)
    kept = setup_filter.apply(_pb("dip_catch", "none"), [{}], None, None, "swing", calibrate=False, elapsed=0.5)
    assert kept["long"]["setup"] == "dip_catch"                     # 1.00x a day: not dry
    done = setup_filter.apply(_pb("dip_catch", "none"), [{}], None, None, "swing", calibrate=False)
    assert "volume_note" not in done                                # finished bars (the backtest): as before
