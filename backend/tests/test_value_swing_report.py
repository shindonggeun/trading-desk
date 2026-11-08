"""#167: the oversold swing buy's own backtest view - targets reached before the stop, bars held,
expectancy, worst loss, the crashes against the dip catch - and the reach rates on the plan."""

from desk import backtest, horizons, value_swing

SWING = horizons.get("swing")


def _bars(rows):
    return [{"at": f"2024-01-{i + 2:02d}", "open": c, "high": hi, "low": lo, "close": c}
            for i, (hi, lo, c) in enumerate(rows)]


SIDE = {"stop": 90.0, "targets": [{"price": 105.0}, {"price": 110.0}, {"price": 120.0}]}


def test_targets_count_in_order_until_the_stop():
    future = _bars([(101, 95, 100), (106, 99, 104), (111, 103, 108), (109, 89, 90), (125, 100, 120)])
    held, reach = backtest._held_and_reach(1, future, SIDE, future[0]["at"], future[3]["at"])
    assert (held, reach) == (3, 2)                      # T1, T2, then the stop - T3 after it does not count


def test_a_bar_with_the_stop_and_a_target_counts_the_stop_first():
    future = _bars([(101, 95, 100), (106, 89, 100)])
    assert backtest._held_and_reach(1, future, SIDE, future[0]["at"], None) == (1, 0)


def test_no_fill_in_the_window_gives_nothing():
    future = _bars([(101, 95, 100)])
    assert backtest._held_and_reach(1, future, SIDE, "2023-12-29", None) == (None, None)


def _trade(at, setup="value_swing", r=0.5, reach=1, held=10, score=80, status="t2"):
    return backtest.Trade("X", at, "long", setup, "crash", score, status, filled_shares=1.0, r_net=r,
                          mae_atr=1.0, held_bars=held, reach=reach)


def test_the_report_splits_reach_holding_and_crashes():
    trades = [_trade("2020-03-10", r=1.2, reach=3, held=20), _trade("2021-06-01", r=-1.0, reach=0, held=4),
              _trade("2024-06-01", r=0.4, reach=1, held=12),
              _trade("2020-03-11", setup="dip_catch", r=-0.5)]
    report = backtest.value_swing_report(trades, "2024-01-01", SWING)
    assert report["reach"] == {"t1": 0.667, "t2": 0.333, "t3": 0.333} and report["reach_n"] == 3
    assert report["worst_r"] == -1.0 and report["held_bars"]["max"] == 20
    assert report["valid"]["settled"] == 1 and report["all"]["plans"] == 3
    crash = report["crash"]["2020-03"]
    assert crash["value_swing"]["expectancy_r"] == 1.2 and crash["dip_catch"]["expectancy_r"] == -0.5
    assert list(report["by_score"]) == ["80"]
    assert backtest.value_swing_report([_trade("2020-03-11", setup="dip_catch")], None, SWING) == {}


def test_the_plan_carries_each_targets_reach_rate():
    for role, rate in value_swing.TARGET_REACH.items():
        assert 0 <= rate <= 1, role
    target = {"role": "t2"}
    assert value_swing._with_reach(target)["hit_rate"] == value_swing.TARGET_REACH["t2"]
    assert "hit_rate" not in value_swing._with_reach({"role": "t9"})
