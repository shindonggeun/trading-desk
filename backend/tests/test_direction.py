"""#166: the price trend's direction, and counter-trend shorts taken smaller or not at all."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from desk import direction, horizons, playbook

RAM = json.loads((Path(__file__).parent / "fixtures" / "ram_2026-11-05.json").read_text(encoding="utf-8"))
SWING = horizons.get("swing")


def _days(closes, start=date(2026, 1, 1), wiggle=0.0):
    out = []
    for i, c in enumerate(closes):
        c = c * (1 + (wiggle if i % 4 < 2 else -wiggle))     # small waves so swings exist
        out.append({"at": (start + timedelta(days=i)).isoformat() + "T05:00:00+00:00", "open": c,
                    "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1000})
    return out


def test_an_uptrend_leans_long_and_a_downtrend_short():
    up = direction.score(_days([100 * 1.004 ** i for i in range(150)], wiggle=0.01))
    down = direction.score(_days([100 * 0.996 ** i for i in range(150)], wiggle=0.01))
    flat = direction.score(_days([100.0] * 150, wiggle=0.01))
    assert up["bias"] == "long" and up["score"] >= direction.STRONG and up["strong"]
    assert down["bias"] == "short" and down["score"] <= -direction.STRONG
    assert flat["bias"] == "neutral" and abs(flat["score"]) < direction.LEAN
    assert {"ma_stack", "slope50", "ret20"} <= {p["id"] for p in up["parts"]}
    assert direction.score(_days([100.0] * 30)) is None                 # too few bars


def test_a_short_against_a_strong_uptrend_is_dropped_and_a_long_against_a_downtrend_kept():
    pb = {"long": {"setup": "dip_catch", "size_hint": "half", "entries": [{"price": 1}]},
          "short": {"setup": "fade", "size_hint": "half", "entries": [{"price": 2}]}}
    up = {"score": 75, "bias": "long", "strong": True, "parts": []}
    out = direction.apply(pb, up, SWING)
    assert out["short"]["setup"] == "none" and "롱 우세(+75)" in out["short"]["reason"]
    assert out["long"]["setup"] == "dip_catch"
    assert out["short"]["direction"] == "against" and out["long"]["direction"] == "with"
    lean = {"score": 40, "bias": "long", "strong": False, "parts": []}
    assert direction.apply(pb, lean, SWING)["short"]["setup"] == "none"      # halved ones lost too: dropped
    neutral = {"score": 10, "bias": "neutral", "strong": False, "parts": []}
    dropped = direction.apply(pb, neutral, SWING)["short"]                     # stage 4: a fade needs a downtrend
    assert dropped["setup"] == "none" and "-60 이하" in dropped["reason"]
    assert direction.apply(pb, {**neutral, "score": -59}, SWING)["short"]["setup"] == "none"
    assert direction.apply(pb, {**neutral, "score": -60}, SWING)["short"]["setup"] == "fade"
    assert direction.apply(pb, None, SWING)["short"]["setup"] == "none"       # no score: no short
    down = {"score": -80, "bias": "short", "strong": True, "parts": []}
    kept = direction.apply(pb, down, SWING)
    assert kept["short"]["setup"] == "fade"
    # catching the crash is the long's job: marked against the trend, not dropped
    assert kept["long"]["setup"] == "dip_catch" and kept["long"]["direction"] == "against"
    assert kept["short"]["direction"] == "with"
    assert direction.apply(pb, None, SWING)["long"]["direction"] == "neutral"


def test_ram_after_the_crash_keeps_its_dip_catch_and_says_short_leaning():
    daily = RAM["daily"]
    pb = playbook.build(RAM["sr"], daily, daily, SWING, live={"price": 5.13, "source": "t", "fetched_at": "x"})
    assert pb["direction"]["bias"] == "short"
    assert pb["long"]["setup"] == "dip_catch" and pb["long"]["direction"] == "against"
    assert pb["short"]["setup"] != "none" and pb["short"]["direction"] == "with"


def test_the_note_names_the_lean_and_asks_for_a_reason_against_it():
    d = direction.score(RAM["daily"])
    note = direction.prompt_note(d)
    assert "SHORT-leaning" in note and "needs a stated reason" in note
    assert direction.prompt_note(None) is None


def _flat_bars(closes, start=date(2025, 1, 1)):
    return [{"at": (start + timedelta(days=i)).isoformat() + "T05:00:00+00:00", "open": c, "high": c, "low": c,
             "close": c, "volume": 1000} for i, c in enumerate(closes)]


def test_ties_vote_neither_way():
    assert direction.score(_flat_bars([100.0] * 200))["score"] == 0                 # a halted stock
    stair_up = direction.score(_flat_bars([round(100 + 0.1 * i) for i in range(200)]))
    stair_down = direction.score(_flat_bars([round(120 - 0.1 * i) for i in range(200)]))
    assert stair_up["bias"] == "long" and stair_down["bias"] == "short"           # plateaus are no lower highs


def test_an_uptrend_drops_the_short_through_build_on_swing_only():
    daily = _days([50 * 1.004 ** i for i in range(230)], wiggle=0.01)
    ref = daily[-1]["close"]
    sr = {"atr": ref * 0.02, "reference_price": ref, "levels": [
        {"price": ref * 1.05, "kind": "resistance", "strength": 2.0, "basis": "저항"},
        {"price": ref * 0.95, "kind": "support", "strength": 2.0, "basis": "지지"}]}
    pb = playbook.build(sr, daily, daily, SWING, live={"price": ref, "source": "t", "fetched_at": "x"})
    assert pb["direction"]["bias"] == "long"
    assert pb["short"]["setup"] == "none" and "롱 우세" in pb["short"]["reason"] and "reach" not in pb["short"]
    assert pb["long"]["setup"] != "none" and pb["long"]["direction"] == "with"
    # refresh and a model line do not bring the dropped short back
    assert playbook.refresh(pb, {"price": ref * 1.01})["short"]["setup"] == "none"
    merged = playbook.merge(pb, playbook.parse("Short Playbook: zone 1-2 / entry 1.5 / confirm x / stop 2.2 / "
                                                "T1 1 / T2 0.8 / time stop 2 bars"))
    assert merged["short"]["setup"] == "none"
    # the other horizons are marked, not changed (the rule was measured on swing)
    marked = direction.apply({"short": {"setup": "fade", "entries": [{"price": 1}]}}, pb["direction"],
                             horizons.get("intraday"))
    assert marked["short"]["setup"] == "fade" and marked["short"]["direction"] == "against"


# --- #166 stage 2: the market's selling score, the news theme, the hysteresis and the turn ------

def test_the_market_selling_score_is_context_by_default():
    flat = _days([100.0] * 150, wiggle=0.01)
    got = direction.score(flat, {"score": 80, "phase": "selling", "label": "투매 진행"})
    part = {p["id"]: p for p in got["parts"]}["market"]
    assert part["weight"] == 0 and got["score"] == direction.score(flat)["score"]      # swept: no gain
    assert "market -1.00 (context, not in the score)" in direction.prompt_note(got)


def test_the_market_selling_score_votes_and_an_exhausted_dump_stops_voting_short(monkeypatch):
    monkeypatch.setattr(direction, "WEIGHTS", {**direction.WEIGHTS, "market": 1.0})
    flat = _days([100.0] * 150, wiggle=0.01)
    calm = direction.score(flat, {"score": 5, "phase": "calm", "label": "평온"})
    dump = direction.score(flat, {"score": 80, "phase": "selling", "label": "투매 진행"})
    done = direction.score(flat, {"score": 80, "phase": "exhausting", "label": "투매 끝물"})
    part = {p["id"]: p for p in dump["parts"]}["market"]
    assert part["value"] == -1.0 and part["validated"] and "투매 진행" in part["text"]
    assert {p["id"]: p for p in calm["parts"]}["market"]["value"] > 0
    assert {p["id"]: p for p in done["parts"]}["market"]["value"] == 0.0
    assert dump["score"] < direction.score(flat)["score"] < calm["score"]
    assert direction.score(flat, {"score": None}) == direction.score(flat)          # no score: no part


def test_only_a_fresh_surging_directly_relevant_theme_votes_and_it_is_marked_unvalidated(monkeypatch):
    monkeypatch.setattr(direction, "WEIGHTS", {**direction.WEIGHTS, "macro": 0.5})
    flat = _days([100.0] * 150, wiggle=0.01)
    theme = {"id": "chips", "label": "반도체 수출 규제", "relevance": 2, "spike": 3.0, "tone": -2.0, "stale": False}
    got = direction.score(flat, None, {"themes": [theme]})
    part = {p["id"]: p for p in got["parts"]}["macro"]
    assert part["value"] == -1.0 and not part["validated"] and "반도체 수출 규제" in part["text"]
    for other in ({**theme, "relevance": 1}, {**theme, "stale": True}, {**theme, "spike": 1.2}, {**theme, "tone": None}):
        assert "macro" not in {p["id"] for p in direction.score(flat, None, {"themes": [other]})["parts"]}
    assert "unvalidated" in direction.prompt_note(got)


def test_a_lean_is_kept_inside_the_hysteresis_and_a_zero_cross_is_a_turn(monkeypatch):
    days = _days([100.0] * 150, wiggle=0.01)
    real = direction._score

    def fake(now: int, before: int):
        def _score(daily, market=None, macro=None):
            got = real(daily, market, macro)
            value = now if len(daily) == len(days) else before
            bias = "long" if value >= direction.LEAN else "short" if value <= -direction.LEAN else "neutral"
            return {**got, "score": value, "bias": bias}
        return _score

    monkeypatch.setattr(direction, "_score", fake(25, 40))       # off by default (swept): neutral at 25
    assert direction.score(days)["bias"] == "neutral" and not direction.score(days)["held"]
    monkeypatch.setattr(direction, "HYSTERESIS", 10)             # on: the long is kept in the band
    held = direction.score(days)
    assert held["bias"] == "long" and held["held"] and not held["turning"]
    monkeypatch.setattr(direction, "_score", fake(15, 40))       # under the band: neutral
    assert direction.score(days)["bias"] == "neutral"
    monkeypatch.setattr(direction, "_score", fake(-10, 40))      # crossed zero: a turn
    turned = direction.score(days)
    assert turned["turning"] and turned["before"] == 40
    pb = direction.apply({"long": {"setup": "dip_catch"}, "short": {"setup": "fade"}}, turned, SWING)
    assert pb["long"]["turning"] and pb["short"]["turning"]
    # swing: no fresh entry either way while turning (measured: turning trades lost more)
    assert pb["long"]["setup"] == pb["short"]["setup"] == "none"
    assert pb["long"]["reason"].startswith("방향 전환 중(+40 → -10")
    # other horizons are only marked
    marked = direction.apply({"long": {"setup": "dip_catch"}, "short": {"setup": "fade"}}, turned,
                             horizons.get("intraday"))
    assert marked["long"]["setup"] == "dip_catch" and marked["long"]["turning"]
    monkeypatch.setattr(direction, "TURN_PAUSE", False)
    assert direction.apply({"long": {"setup": "dip_catch"}}, turned, SWING)["long"]["setup"] == "dip_catch"
    assert "TURNING" in direction.prompt_note(turned)
    monkeypatch.setattr(direction, "_score", real)


def test_a_hard_break_of_an_uptrend_turns_the_score():
    up = [100 * 1.004 ** i for i in range(150)]
    crash = up + [up[-1] * 0.92 ** k for k in range(1, 7)]    # six sessions of -8%
    turned = direction.score(_days(crash, wiggle=0.01))
    assert turned["before"] > direction.LEAN and turned["score"] < 0 and turned["turning"]


def _index(ret20=0.0, above20=True, slope20=0.01):
    return {"score": 10, "phase": "calm", "label": "평상",
            "index": {"symbol": "^NDX", "ret20": ret20, "above20": above20, "slope20": slope20}}


def test_strength_against_the_index_and_the_index_tape_are_evidence_by_default():
    flat = _days([100.0] * 150, wiggle=0.01)
    got = direction.score(flat, _index(ret20=-0.08, above20=False, slope20=-0.02))
    parts = {p["id"]: p for p in got["parts"]}
    assert parts["rel"]["value"] > 0.5 and "^NDX 대비" in parts["rel"]["text"]    # flat beats a falling index
    assert parts["breadth"]["value"] == -1.0 and "20일선 아래" in parts["breadth"]["text"]
    assert parts["rel"]["weight"] == 0 == parts["breadth"]["weight"] and parts["breadth"]["validated"]
    assert got["score"] == direction.score(flat)["score"]                        # swept: no gain
    assert not {"rel", "breadth"} & {p["id"] for p in direction.score(flat, {"score": 10, "phase": "calm"})["parts"]}


def test_strength_and_breadth_vote_when_weighted(monkeypatch):
    monkeypatch.setattr(direction, "WEIGHTS", {**direction.WEIGHTS, "rel": 1.0, "breadth": 1.0})
    flat = _days([100.0] * 150, wiggle=0.01)
    strong = direction.score(flat, _index(ret20=-0.10, above20=True, slope20=0.01))
    weak = direction.score(flat, _index(ret20=0.10, above20=False, slope20=-0.01))
    mixed = {p["id"]: p for p in direction.score(flat, _index(above20=True, slope20=-0.01))["parts"]}
    assert weak["score"] < direction.score(flat)["score"] < strong["score"]
    assert mixed["breadth"]["value"] == 0.0


def test_a_market_under_its_line_is_no_turn_of_a_steady_trend(monkeypatch):
    """The turn is read on the price alone at both ends: ``before`` has no market as of its day."""
    monkeypatch.setattr(direction, "WEIGHTS", {**direction.WEIGHTS, "breadth": 1.5})
    up = _days([100.0 * 1.0003 ** i * (1 + 0.02 * math.sin(i / 6)) for i in range(150)], wiggle=0.01)
    plain = direction.score(up)
    down = direction.score(up, _index(ret20=-0.1, above20=False, slope20=-0.03))
    assert plain["score"] > 0 > down["score"] and plain["before"] > 0          # the score crossed zero ...
    assert not down["turning"] and down["turning"] == plain["turning"]        # ... but the price did not turn


def test_the_note_says_what_the_index_parts_mean():
    flat = _days([100.0] * 150, wiggle=0.01)
    note = direction.prompt_note(direction.score(flat, _index(above20=False, slope20=-0.02)))
    assert "main index trend" in note and "[^NDX 20일선 아래" in note and "[^NDX 대비 20일" in note


def test_under_the_index_20_day_line_pullback_and_range_longs_pause_and_crash_buys_stay():
    up = {"score": 70, "bias": "long", "strong": True, "parts": []}
    for tape in ("below_up", "below_down"):
        for setup in ("pullback", "range_low"):
            pb = {"long": {"setup": setup, "entries": [{"price": 1}]}, "short": {"setup": "none"}}
            out = direction.apply(pb, {**up, "tape": tape}, SWING)["long"]
            assert out["setup"] == "none" and "20일선 아래" in out["reason"]
        crash = {"long": {"setup": "dip_catch", "entries": [{"price": 1}]}, "short": {"setup": "none"}}
        assert direction.apply(crash, {**up, "tape": tape}, SWING)["long"]["setup"] == "dip_catch"
    pb = {"long": {"setup": "pullback", "entries": [{"price": 1}]}, "short": {"setup": "none"}}
    for tape in ("above_up", "above_down", None):
        assert direction.apply(pb, {**up, "tape": tape}, SWING)["long"]["setup"] == "pullback"
    marked = direction.apply(pb, {**up, "tape": "below_down"}, horizons.get("intraday"))["long"]
    assert marked["setup"] == "pullback"                                     # swing only: elsewhere marked


def test_the_tape_comes_from_the_market_index_and_the_weekly_trend_is_evidence():
    assert direction.tape_of(_index(above20=False, slope20=0.01)) == "below_up"
    assert direction.tape_of(_index(above20=True, slope20=-0.01)) == "above_down"
    assert direction.tape_of({"score": 10}) is None and direction.tape_of(None) is None
    up = _days([100.0 * 1.004 ** i for i in range(200)], wiggle=0.01)
    got = direction.score(up, _index(above20=False, slope20=-0.01))
    weekly = {p["id"]: p for p in got["parts"]}["weekly"]
    assert got["tape"] == "below_down" and weekly["value"] == 1.0 and weekly["weight"] == 0
    assert "주봉 상승" in weekly["text"] and not weekly["validated"]


def test_a_turning_flag_survives_a_side_a_rule_emptied():
    pb = {"long": {"setup": "pullback", "entries": [{"price": 1}]}, "short": {"setup": "fade", "entries": [{"price": 2}]}}
    turning = {"score": -10, "bias": "neutral", "strong": False, "turning": True, "before": 40, "parts": []}
    out = direction.apply(pb, turning, SWING)
    assert out["short"]["setup"] == "none" and out["short"]["turning"] and out["long"]["turning"]


def _uptrend_build_inputs():
    daily = _days([50 * 1.004 ** i for i in range(230)], wiggle=0.01)
    ref = daily[-1]["close"]
    sr = {"atr": ref * 0.02, "reference_price": ref, "levels": [
        {"price": ref * 1.05, "kind": "resistance", "strength": 2.0, "basis": "저항"},
        {"price": ref * 0.95, "kind": "support", "strength": 2.0, "basis": "지지"}]}
    return daily, ref, sr


def test_the_index_rule_runs_through_build_and_the_backtest_keeps_it_with_the_gate_off():
    from types import SimpleNamespace

    from desk import backtest

    daily, ref, sr = _uptrend_build_inputs()
    live = {"price": ref, "source": "t", "fetched_at": "x"}
    plain = playbook.build(sr, daily, daily, SWING, live=live)
    assert plain["long"]["setup"] in direction.TAPE_PAUSE_SETUPS            # the rule's target
    under = _index(above20=False, slope20=0.01)
    paused = playbook.build(sr, daily, daily, SWING, live=live, market=under)
    assert paused["direction"]["tape"] == "below_up"
    assert paused["long"]["setup"] == "none" and "20일선 아래" in paused["long"]["reason"]
    assert "size_hint" in paused["long"] and paused["long"]["entries"] == []   # desk.playbook's empty side shape
    above = playbook.build(sr, daily, daily, SWING, live=live, market=_index(above20=True, slope20=0.01))
    assert above["long"]["setup"] == plain["long"]["setup"]
    # the backtest's gate-off run still hands the market to the direction (the #146 gate is what is off)
    snap = SimpleNamespace(sr=sr, daily=daily, window=daily, plan_at=None, today=None, vix=None, risk=under,
                           weekly=None)
    assert backtest.playbooks_at(snap, SWING, gate=False)["long"]["setup"] == "none"


def test_the_weekly_part_needs_30_weeks_and_reads_two_terms_before_34():
    flat = [100.0] * 145
    assert "weekly" not in {p["id"] for p in direction.score(_days(flat, wiggle=0.01))["parts"]}   # 29 points
    assert "weekly" in {p["id"] for p in direction.score(_days(flat + [100.0] * 5, wiggle=0.01))["parts"]}  # 30
    up = _days([100.0 * 1.004 ** i for i in range(160)], wiggle=0.01)            # 32 weekly points: no slope term
    part = {p["id"]: p for p in direction.score(up)["parts"]}["weekly"]
    assert part["value"] == 1.0


def test_the_note_says_the_tape_in_words_and_the_rules_only_when_on(monkeypatch):
    flat = _days([100.0] * 150, wiggle=0.01)
    note = direction.prompt_note(direction.score(flat, _index(above20=False, slope20=0.01)))
    assert "below its rising 20-day line" in note and "-60 or lower" in note and "pause pullback" in note
    monkeypatch.setattr(direction, "SHORT_NEEDS", None)
    monkeypatch.setattr(direction, "TAPE_PAUSE", ())
    note = direction.prompt_note(direction.score(flat))
    assert "-60" not in note and "pause pullback" not in note and "20-day line" not in note
