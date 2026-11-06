"""#166: the price trend's direction, and counter-trend shorts taken smaller or not at all."""

from __future__ import annotations

import json
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
    assert direction.apply(pb, neutral, SWING)["short"]["setup"] == "fade"
    down = {"score": -80, "bias": "short", "strong": True, "parts": []}
    kept = direction.apply(pb, down, SWING)
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
