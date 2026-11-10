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
    untouched = trigger.scan(_bars(DOWN_INTO_ZONE[:2]), LONG, True, 2.0)
    assert untouched["state"] is None and untouched["touched_at"] is None
    # touched before the window (the live page): armed from the first bar
    assert trigger.scan(_bars(DOWN_INTO_ZONE[:2]), LONG, True, 2.0, touched=True)["state"] == "armed"
    assert trigger.scan(_bars(DOWN_INTO_ZONE), LONG, True, 0) is None          # no ATR: no trigger
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
    turn = _bars(DOWN_INTO_ZONE + [(98.5, 99.4, 98.4, 99.3), (99.3, 99.35, 99.0, 99.1)])   # + the forming bar
    armed = trigger.live(LONG, turn, True, 2.0, price=99.3)
    assert armed["state"] == "armed" and set(armed) >= {"price", "invalidation", "signal", "rr_t1"}
    assert armed["preferred"] is True and trigger.PREFERRED["short"] is True      # the backtest's verdict
    assert trigger.live(LONG, turn, True, 2.0, price=armed["price"] + 0.01)["state"] == "triggered"
    assert trigger.live(LONG, _bars(DOWN_INTO_ZONE[:2]), True, 2.0, price=101) is None      # not touched


def test_live_ignores_the_forming_bar_as_a_signal_and_ends_a_stopped_trigger():
    forming = _bars(DOWN_INTO_ZONE + [(98.5, 99.4, 98.4, 99.3)])
    assert trigger.live(LONG, forming, True, 2.0, price=99.3)["price"] is None      # its close is not one yet
    turn = DOWN_INTO_ZONE + [(98.5, 99.4, 98.4, 99.3), (99.3, 99.35, 98.9, 99.0)]
    armed = trigger.live(LONG, _bars(turn), True, 2.0, price=99.0)
    assert armed["state"] == "armed" and armed["price"] is not None
    # filled by a finished bar, then broke the invalidation: that trade is over
    done = turn + [(99.3, 100.4, 99.2, 100.2), (100.2, 100.3, 97.0, 97.2), (97.2, 97.5, 97.0, 97.3)]
    assert trigger.live(LONG, _bars(done), True, 2.0, price=97.3)["state"] == "invalid"
    # filled by the quote, the quote back under the invalidation
    assert trigger.live(LONG, _bars(turn), True, 2.0, price=armed["invalidation"] - 0.01)["state"] == "armed"


def test_a_bar_reaching_both_sides_is_read_against_the_trade_unless_it_opens_through():
    la = 0.5
    turn = DOWN_INTO_ZONE + [(98.5, 99.4, 98.4, 99.3)]
    res = trigger.scan(_bars(turn), LONG, True, 2.0, la)
    both = trigger.scan(_bars(turn + [(99.0, 100.0, 97.0, 99.8)]), LONG, True, 2.0, la)   # dips under, then up
    assert both["state"] == "armed" and both["signal_at"] != res["signal_at"]      # dropped, a new turn armed
    gap = trigger.scan(_bars(turn + [(99.8, 100.0, 97.0, 97.5)]), LONG, True, 2.0, la)     # opens over the trigger
    assert gap["state"] == "triggered" and gap["fill"] == 99.8


def test_vwap_is_not_taken_back_across_sessions_and_a_higher_low_signals():
    # a close under the VWAP at the session's end and a new session's first bar: no reclaim
    bars = _bars(DOWN_INTO_ZONE) + _bars([(98.6, 98.7, 98.4, 98.65)], start=datetime(2026, 11, 3, 14, 30))
    assert trigger._signal(bars, len(bars) - 1, True, trigger._vwaps(bars), 4) is None
    # the turn low 98.2, a bar down to 98.4 (above it) and a close over that bar's high on a red bar
    hl = DOWN_INTO_ZONE + [(98.5, 98.8, 98.5, 98.7), (98.7, 98.75, 98.4, 98.45), (98.85, 98.9, 98.6, 98.8)]
    res = trigger.scan(_bars(hl), LONG, True, 2.0, 0.5)
    assert res["signal"] == "higher_low" and res["price"] == round(98.9 + 0.05, 2)


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
    wanted = [s for s in ("long", "short") if out[s].get("state") in ("near", "in_zone", "confirmed")]
    assert wanted, "the crash fixture should put a side near its zone"
    assert asked == [trigger.LOOKBACK["60m"]] and out["frame"]["trigger_interval"] == "60m"
    for s in ("long", "short"):
        assert "lower_trigger" in out[s]
        if s not in wanted:
            assert out[s]["lower_trigger"] is None
    assert out["long"]["lower_trigger"] is None or out["long"]["lower_trigger"]["preferred"] is True
    # #193: the playbook's own planned trigger is left alone - a dict with a text, or absent
    fresh = playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING)
    for s in ("long", "short"):
        assert out[s].get("trigger") == fresh[s].get("trigger")

    async def broken(ticker, interval="1d", count=120, indicators=()):
        if interval == "60m":
            raise RuntimeError("yahoo down")
        return await real(ticker, interval, count, indicators)

    playbook_live._CACHE.clear()
    monkeypatch.setattr(src, "candles", broken)
    out = asyncio.run(playbook_live.current(src, "NVDA", "swing"))
    assert all(out[s]["lower_trigger"] is None for s in ("long", "short")) and out["long"]["state"] is not None


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
    trig = trigger_bt._arm("trigger", "X", "d", "long", True, res["fill"], res["invalidation"], res["filled_at"], side,
                           rest, pb["frame"]["atr"], 0)
    assert trig.filled and trig.risk_atr < touch.risk_atr
    rows = trigger_bt.tally([touch, trig])
    assert rows["fill_rate"] == 1.0


def test_the_fill_bar_counts_its_stop_but_not_a_target_that_may_have_come_first():
    bars = _bars([(102.5, 102.6, 99.5, 100.5), (100.5, 101.0, 100.2, 100.8)])
    out, took, _ = trigger_bt._exit(bars, True, 100.0, 98.0, 102.0, 104.0)
    assert not took and out == 100.8                       # T1's 102 on the fill bar was before the fill
    out, took, _ = trigger_bt._exit(_bars([(100.5, 100.6, 97.5, 98.0)]), True, 100.0, 98.0, 102.0, 104.0)
    assert out == 98.0 and not took


def test_stacked_filters_keep_the_trend_and_market_agreeing_arms():
    """#239 stage 2: each arm carries the direction call and the selling phase; the rows stack them."""
    A = trigger_bt.Arm

    def arm(side, direction, phase, r, at="2025-06-01", lev=False, name="trigger"):
        return A("X", at, side, name, True, r=r, direction=direction, phase=phase, leveraged=lev)
    arms = [arm("long", "with", None, 1.0), arm("long", "against", None, -1.0),
            arm("long", "with", "selling", -0.5),                 # a long while the selling runs
            arm("short", "with", "exhausting", -1.0),             # a short into the exhaustion
            arm("short", "with", "selling", 0.8, lev=True), arm("long", "neutral", None, 0.2, at="2025-01-01")]
    rows = trigger_bt.stacked(arms, "2025-03-01")
    assert rows["trigger|all"]["filled"] == 6
    assert rows["trigger|dir"]["filled"] == 4
    assert rows["trigger|mkt"]["filled"] == 4
    both = rows["trigger|dir+mkt"]
    assert both["filled"] == 2 and both["expectancy_r"] == 0.9 and both["win_rate"] == 1.0
    assert rows["trigger|dir+mkt:lev"]["filled"] == 1 and rows["trigger|dir+mkt:base"]["filled"] == 1
    assert rows["trigger|all:valid"]["filled"] == 5 and rows["touch|all"]["plans"] == 0
