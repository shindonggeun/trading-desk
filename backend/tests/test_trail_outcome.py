"""#277: a plan that tells the 2-ATR trail (#239) is scored with that trail, as desk.exits measured it."""

from desk import exits, horizons, outcome
from tests.test_exits import FILL, _bars

SWING = horizons.get("swing")
PLAN_AT = "2025-12-31T12:00:00+00:00"
ATR = 2.0


def _side(up: bool = True, trail: bool = True) -> dict:
    entry, stop, t1, t2 = (100.0, 95.0, 105.0, 115.0) if up else (100.0, 105.0, 95.0, 85.0)
    side = {"setup": "pullback", "entries": [{"step": 1, "price": entry, "share": 1.0, "kind": "limit"}],
            "stop": stop, "targets": [{"role": "t1", "price": t1}, {"role": "t2", "price": t2}],
            "zone": {"low": 99.0, "high": 101.0}, "time_stop": {"bars": 3}, "flat_by_close": False}
    if trail:
        side["exit"] = {"rule": "trail_atr", "trail_atr": exits.TRAIL_ATR, "distance": exits.TRAIL_ATR * ATR}
    return side


def _replay(direction: int, bars: list[dict], stop: float, t1: float) -> float:
    return exits.replay(direction, bars, bars[0]["at"], 100.0, stop, t1, ATR, fixed_r=0.0, time_stop=3)["trail_atr"]


def test_a_reversal_after_t1_ends_on_the_trail_with_the_same_r_as_the_replay():
    bars = _bars(FILL + [(101, 106, 105.5), (105, 110, 109), (101, 109, 102), (96, 102, 97)])
    res = outcome.judge_playbook(_side(), 1, bars, SWING, PLAN_AT)
    assert res["status"] == "t1_be" and res["trailed"] is True and res["took_t1"] is True
    assert res["r"] == round(_replay(1, bars, 95.0, 105.0), 2) == 1.1   # half +1R, half out at 106 (+1.2R)


def test_the_trail_does_not_stop_at_t2_and_marks_an_open_runner():
    bars = _bars(FILL + [(100 + 3 * i, 103 + 3 * i, 102 + 3 * i) for i in range(10)])   # to 129, past T2 115
    res = outcome.judge_playbook(_side(), 1, bars, SWING, PLAN_AT)
    assert res["status"] == "open" and res["trailing"] is True and res["r"] == round(_replay(1, bars, 95.0, 105.0), 2)
    fixed = outcome.judge_playbook(_side(trail=False), 1, bars, SWING, PLAN_AT)
    assert fixed["status"] == "t2" and "trailed" not in fixed


def test_a_gap_through_the_trail_fills_at_the_open():
    bars = _bars(FILL + [(101, 110, 109), (100, 104, 101)])
    bars[2]["open"] = 103.0                                      # opens under the trail 106
    res = outcome.judge_playbook(_side(), 1, bars, SWING, PLAN_AT)
    assert res["trailed"] and res["r"] == round(0.5 * 1 + 0.5 * 3 / 5, 2) == round(_replay(1, bars, 95.0, 105.0), 2)


def test_a_short_trails_above_the_lowest_low():
    bars = _bars([(99, 101, 100), (94.5, 99, 95), (90, 95, 91), (91, 98, 97)])
    res = outcome.judge_playbook(_side(up=False), -1, bars, SWING, PLAN_AT)
    # T1 95 (+1R half), lowest low 90 -> trail 94; the last bar's high 98 hits it: +1.2R on the rest
    assert res["trailed"] and res["r"] == round(0.5 * 1 + 0.5 * 6 / 5, 2) == round(_replay(-1, bars, 105.0, 95.0), 2)


def test_a_stop_before_t1_and_the_time_stop_are_the_same_with_or_without_the_trail():
    loser = _bars(FILL + [(96, 101, 97), (94, 97, 95)])
    for side in (_side(), _side(trail=False)):
        res = outcome.judge_playbook(side, 1, loser, SWING, PLAN_AT)
        assert res["status"] == "stop" and res["r"] == -1.0 and "trailed" not in res
    flat = _bars(FILL + [(99, 102, 101)] * 5)
    assert outcome.judge_playbook(_side(), 1, flat, SWING, PLAN_AT)["status"] == "time_stop"


def test_a_trail_left_at_break_even_is_the_plain_break_even():
    side = _side()
    side["exit"]["distance"] = 6.0                          # wider than T1's 5: the trail stays at break-even
    bars = _bars(FILL + [(101, 105.5, 105), (99, 104, 100)])
    res = outcome.judge_playbook(side, 1, bars, SWING, PLAN_AT)
    assert res["status"] == "t1_be" and "trailed" not in res and res["r"] == 0.5


def test_no_distance_falls_back_to_the_fixed_exit():
    side = _side()
    side["exit"]["distance"] = None
    bars = _bars(FILL + [(100 + 3 * i, 103 + 3 * i, 102 + 3 * i) for i in range(10)])
    assert outcome.judge_playbook(side, 1, bars, SWING, PLAN_AT)["status"] == "t2"


def test_a_runner_still_trailing_at_the_age_limit_is_marked_not_dropped():
    import asyncio
    from datetime import UTC, datetime, timedelta

    bars = _bars(FILL + [(100 + 3 * i, 103 + 3 * i, 102 + 3 * i) for i in range(10)])

    class Prices:
        async def candles(self, ticker, interval, n):
            return {"bars": bars}

    plan = {"bias": "long", "entry": 100.0, "stop": 95.0, "target": 115.0, "live_price": {"fetched_at": PLAN_AT},
            "playbooks": {"regime": {"id": "uptrend"}, "long": _side(), "short": {"setup": "none"}}}
    run = {"ticker": "NVDA", "horizon": "swing", "plan": plan, "created_at": PLAN_AT}
    now = datetime.fromisoformat(PLAN_AT).astimezone(UTC) + outcome.MAX_AGE["swing"] + timedelta(days=1)
    res = asyncio.run(outcome.score(run, Prices(), now))
    pb = res["playbooks"]["long"]
    assert pb["status"] == "t1_be" and pb["trailed"] and pb["marked"] and pb["r"] > 2
