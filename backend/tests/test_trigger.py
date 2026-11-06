"""#149: the entry trigger on the lower bars - armed at the zone, triggered past the turn's bar."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from desk import playbook, playbook_live, trigger, trigger_bt
from tests.test_playbook import SWING, _crash, _crash_sr
from tests.test_playbook_live import _source

LONG = {"setup": "dip_catch", "zone": {"low": 98.0, "high": 100.0, "mid": 99.0}, "stop": 96.0,
        "targets": [{"role": "t1", "price": 106.0}, {"role": "t2", "price": 110.0}]}
SHORT = {"setup": "fade", "zone": {"low": 110.0, "high": 111.0, "mid": 110.5}, "stop": 113.0,
         "targets": [{"role": "t1", "price": 104.0}]}


def _bars(ohlc, start=datetime(2026, 11, 2, 14, 30), vol=None):
    return [{"at": (start + timedelta(hours=i)).isoformat() + "+00:00", "open": o, "high": h, "low": low,
             "close": c, "volume": (vol or {}).get(i, 1000)} for i, (o, h, low, c) in enumerate(ohlc)]


DOWN_INTO_ZONE = [(104, 104.5, 102.5, 103), (103, 103.2, 101.5, 101.8), (101.8, 102, 99.6, 99.9),
                  (99.9, 100.1, 98.6, 98.9), (98.9, 99.0, 98.2, 98.5)]


def test_no_touch_no_trigger_and_a_touch_arms_it():
    assert trigger.scan(_bars(DOWN_INTO_ZONE[:2]), LONG, True, 2.0) == {
        **trigger.scan(_bars(DOWN_INTO_ZONE[:2]), LONG, True, 2.0), "state": None}
    armed = trigger.scan(_bars(DOWN_INTO_ZONE), LONG, True, 2.0)
    assert armed["state"] == "armed" and armed["price"] is None and armed["touched_at"].startswith("2026-11-02T16")
    assert trigger.scan([], LONG, True, 2.0) is None and trigger.scan(_bars(DOWN_INTO_ZONE), {"setup": "none"}, True, 2) is None


def test_a_break_of_the_prior_bar_sets_the_trigger_and_its_invalidation_then_fills():
    turn = DOWN_INTO_ZONE + [(98.5, 99.4, 98.4, 99.3)]          # closes over the prior high, a green bar
    la = 0.5
    res = trigger.scan(_bars(turn), LONG, True, 2.0, la)
    assert res["state"] == "armed" and res["signal"] == "break"
    assert res["price"] == round(99.4 + trigger.BUFFER_ATR * la, 2)
    # the turn's low 98.2 less 0.25 lower ATR, above the plan's stop 96: the nearer one
    assert res["invalidation"] == round(98.2 - trigger.INVALID_ATR * la, 2) and res["invalidation"] > LONG["stop"]
    assert res["rr_t1"] > 0
    filled = trigger.scan(_bars(turn + [(99.3, 100.4, 99.2, 100.2)]), LONG, True, 2.0, la)
    assert filled["state"] == "triggered" and filled["fill"] == res["price"]
    # a gap over the trigger fills at the open
    gapped = trigger.scan(_bars(turn + [(101.0, 101.5, 100.8, 101.2)]), LONG, True, 2.0, la)
    assert gapped["fill"] == 101.0


def test_a_failed_turn_is_dropped_and_a_stop_before_any_trigger_is_invalid():
    turn = DOWN_INTO_ZONE + [(98.5, 99.4, 98.4, 99.3), (99.2, 99.3, 97.5, 97.6)]   # under the turn low: dropped
    res = trigger.scan(_bars(turn), LONG, True, 2.0)
    assert res["state"] == "armed" and res["price"] is None
    crashed = trigger.scan(_bars(DOWN_INTO_ZONE + [(98.5, 98.6, 95.5, 95.8)]), LONG, True, 2.0)
    assert crashed["state"] == "invalid"


def test_a_volume_spike_low_that_holds_is_absorption():
    flat = [(103, 103.5, 102.5, 103)] * 20
    spike = flat + [(103, 103, 98.3, 98.8), (98.8, 98.9, 98.5, 98.6), (98.6, 98.7, 98.45, 98.5)]
    res = trigger.scan(_bars(spike, vol={20: 5000}), LONG, True, 2.0)
    assert res["signal"] == "absorption" and res["price"] is not None


def test_the_short_mirrors_and_a_run_away_turn_is_not_chased():
    up = [(106, 107.5, 105.8, 107.2), (107.2, 109.0, 107.0, 108.8), (108.8, 110.6, 108.6, 110.4),
          (110.4, 111.2, 110.2, 110.9), (110.9, 111.0, 110.0, 110.1)]
    res = trigger.scan(_bars(up), SHORT, False, 2.0)
    assert res["signal"] == "break" and res["price"] < 110.0 and res["invalidation"] > 111.2
    assert res["invalidation"] <= SHORT["stop"]
    trigger.CHASE_ATR, old = 0.0, trigger.CHASE_ATR
    try:
        assert trigger.scan(_bars(up), SHORT, False, 2.0)["price"] is None
    finally:
        trigger.CHASE_ATR = old


def test_live_reads_the_quote_past_the_trigger_as_triggered():
    turn = _bars(DOWN_INTO_ZONE + [(98.5, 99.4, 98.4, 99.3)])
    armed = trigger.live(LONG, turn, True, 2.0, price=99.3)
    assert armed["state"] == "armed" and set(armed) >= {"price", "invalidation", "signal", "rr_t1"}
    assert armed["preferred"] is False and trigger.PREFERRED["short"] is True     # the backtest's verdict
    assert trigger.live(LONG, turn, True, 2.0, price=armed["price"] + 0.01)["state"] == "triggered"
    assert trigger.live(LONG, _bars(DOWN_INTO_ZONE[:2]), True, 2.0, price=101)["state"] is None


def test_the_live_playbooks_carry_a_trigger_from_the_lower_bars(monkeypatch):
    playbook_live._CACHE.clear()
    bars = _crash()
    monkeypatch.setattr(playbook_live, "support_resistance",
                        lambda *a, **k: asyncio.sleep(0, result=_crash_sr(bars[-1]["close"])))
    src = _source()
    asked = []

    async def candles(ticker, interval="1d", count=120, indicators=()):
        if interval == "60m":
            asked.append(count)
            return {"bars": _bars(DOWN_INTO_ZONE)}
        return await real(ticker, interval, count, indicators)

    real = src.candles
    monkeypatch.setattr(src, "candles", candles)
    out = asyncio.run(playbook_live.current(src, "NVDA", "swing"))
    for side in ("long", "short"):
        if out[side].get("state") in ("near", "in_zone", "confirmed"):
            assert "trigger" in out[side] and asked == [trigger.LOOKBACK["60m"]]
        else:
            assert out[side].get("trigger") is None


def test_the_backtest_compares_both_arms_on_the_same_plan():
    pb = playbook.build(_crash_sr(_crash()[-1]["close"]), _crash(), _crash(), SWING)
    side = pb["long"]
    assert side["setup"] != "none"
    z = side["zone"]
    t1 = side["targets"][0]["price"]
    path = [(z["high"] + 1, z["high"] + 1.2, z["high"] + 0.5, z["high"] + 0.6),
            (z["high"] + 0.6, z["high"] + 0.6, z["mid"], z["mid"] + 0.1),
            (z["mid"] + 0.1, z["high"] + 0.8, z["mid"], z["high"] + 0.7),         # break: the trigger
            (z["high"] + 0.2, t1 - 0.1, z["high"], t1 - 0.2),
            (t1 - 0.2, t1 + 50, t1 - 0.3, t1 + 40)]
    rest = _bars(path)
    price, at = trigger_bt._touch(side, rest, True)
    assert price == z["high"] and at == rest[1]["at"]
    touch = trigger_bt._arm("touch", "X", "d", "long", True, price, side["stop"], at, side, rest, pb["frame"]["atr"], 0)
    assert touch.filled and touch.took_t1 and touch.r > 0
    res = trigger.scan(rest, side, True, pb["frame"]["atr"])
    assert res["state"] == "triggered"
    rows = trigger_bt.tally([touch])
    assert rows["fill_rate"] == 1.0 and rows["t1_rate"] == 1.0
