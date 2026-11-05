import pytest

from desk.levels import entry_vs_live, fill_levels

PRICES = lambda ticker, day: (200.0, 6.0)


def _plan(bias, **levels):
    return {"bias": bias, "entry_price": None, "stop_loss": None, "price_target": None, **levels}


def test_short_with_no_levels_gets_a_2_to_1_short_from_atr():
    plan = fill_levels(_plan("short"), "NVDA", "2026-11-04", PRICES)
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (200.0, 209.0, 182.0)
    assert plan["reward_risk"] == 2.0
    assert plan["levels_source"] == {"entry_price": "atr", "stop_loss": "atr", "price_target": "atr"}
    assert plan["atr"] == 6.0 and plan["last_close"] == 200.0


def test_model_levels_are_kept_and_only_gaps_are_filled_around_the_models_entry():
    plan = fill_levels(_plan("long", entry_price=190.0, price_target=230.0), "NVDA", "2026-11-04", PRICES)
    assert plan["entry_price"] == 190.0 and plan["price_target"] == 230.0
    assert plan["stop_loss"] == 181.0                       # 190 - 1.5 x 6
    assert plan["levels_source"] == {"entry_price": "model", "price_target": "model", "stop_loss": "atr"}
    assert plan["reward_risk"] == pytest.approx(4.44)


def test_complete_and_flat_plans_never_fetch_prices():
    def boom(*_):
        raise AssertionError("should not fetch")
    full = fill_levels(_plan("long", entry_price=1.0, stop_loss=0.5, price_target=2.0), "X", "2026-11-04", boom)
    assert full["levels_source"] == {"entry_price": "model", "stop_loss": "model", "price_target": "model"}
    flat = fill_levels(_plan("flat"), "X", "2026-11-04", boom)
    assert flat["entry_price"] is None and flat["levels_source"] == {}


def test_krw_levels_are_whole_won_and_a_price_failure_keeps_the_run():
    plan = fill_levels(_plan("long"), "005930.KS", "2026-11-04", lambda *_: (74850.0, 1530.0))
    assert plan["stop_loss"] == 72555.0 and plan["price_target"] == 79440.0
    def down(*_):
        raise ConnectionError("yahoo down")
    failed = fill_levels(_plan("long"), "X", "2026-11-04", down)
    assert failed["entry_price"] is None and "yahoo down" in failed["levels_error"]


LIVE = {"price": 200.13, "source": "toss", "fetched_at": "2026-11-05T15:00:00+00:00"}


def test_short_entry_far_above_the_live_price_is_a_limit_on_a_rally():
    # the NVDA case: Sell at 220.84 with the price already near 200
    plan = entry_vs_live({**_plan("short", entry_price=220.84, stop_loss=227.1, price_target=208.2),
                          "live_price": LIVE}, atr=6.0)
    gap = plan["entry_vs_live"]
    assert gap["kind"] == "limit" and gap["far"] and gap["gap"] == 20.71 and gap["gap_pct"] == 10.35
    assert gap["target_passed"] and not gap["stop_passed"]


def test_entry_near_the_live_price_is_now_and_a_breakout_long_is_a_stop():
    near = entry_vs_live({**_plan("long", entry_price=201.0, stop_loss=195.0), "live_price": LIVE}, atr=6.0)
    assert near["entry_vs_live"]["kind"] == "now" and not near["entry_vs_live"]["far"]
    breakout = entry_vs_live({**_plan("long", entry_price=206.0), "live_price": LIVE}, atr=6.0)
    assert breakout["entry_vs_live"]["kind"] == "stop"
    # without an ATR the gap is judged in percent
    pct = entry_vs_live({**_plan("short", entry_price=199.5), "live_price": LIVE})
    assert pct["entry_vs_live"]["kind"] == "now" and pct["entry_vs_live"]["gap_atr"] is None


def test_no_live_price_or_flat_bias_leaves_the_plan_alone():
    assert "entry_vs_live" not in entry_vs_live(_plan("short", entry_price=220.0))
    assert "entry_vs_live" not in entry_vs_live({**_plan("flat", entry_price=220.0), "live_price": LIVE})
