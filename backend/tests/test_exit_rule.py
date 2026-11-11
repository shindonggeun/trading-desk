"""#239 채택: the exit rule each playbook side carries."""

from desk import exits, horizons, playbook
from tests.test_playbook import SWING, _crash, _crash_sr


def _side(**extra) -> dict:
    return {"setup": "pullback", "targets": [{"role": "t1", "price": 110.0}, {"role": "t2", "price": 120.0}],
            "flat_by_close": False, **extra}


def _pb(**extra) -> dict:
    return {"frame": {"atr": 2.5}, "long": _side(**extra), "short": _side(**extra)}


def test_a_stock_swing_plan_trails_the_rest_after_t1():
    pb = playbook.set_exits(_pb(), "NVDA", horizons.get("swing"))
    long_exit, short_exit = pb["long"]["exit"], pb["short"]["exit"]
    assert long_exit["rule"] == short_exit["rule"] == "trail_atr"
    assert long_exit["trail_atr"] == exits.TRAIL_ATR and long_exit["distance"] == 5.0
    assert "최고가에서" in long_exit["text"] and "최저가에서" in short_exit["text"]


def test_leveraged_products_and_unmeasured_frames_keep_the_fixed_exit():
    assert playbook.set_exits(_pb(), "SOXL", horizons.get("swing"))["long"]["exit"]["rule"] == "fixed"
    assert playbook.set_exits(_pb(), "NVDA", horizons.get("short_swing"))["long"]["exit"]["rule"] == "fixed"
    assert playbook.set_exits(_pb(flat_by_close=True), "NVDA", horizons.get("swing"))["long"]["exit"]["rule"] == "fixed"


def test_no_setup_and_the_oversold_swing_get_no_exit_rule():
    pb = _pb()
    pb["long"] = {"setup": "none", "targets": []}
    pb["short"] = _side(setup="value_swing")
    playbook.set_exits(pb, "NVDA", horizons.get("swing"))
    assert pb["long"]["exit"] is None and pb["short"]["exit"] is None
    assert playbook.set_exits(None, "NVDA", horizons.get("swing")) is None


def test_the_prompt_note_names_the_trail():
    bars = _crash()
    pb = playbook.set_exits(playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING), "NVDA", SWING)
    assert pb["long"]["exit"]["rule"] == "trail_atr"
    assert "trail the rest 2 ATR behind the best price" in playbook.prompt_note(pb)
