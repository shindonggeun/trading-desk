from copy import deepcopy

import pytest

from desk import regime_bt


def candle(index, opening=101, high=104, low=100, close=101):
    return {"at": f"{index:04}", "open": opening, "high": high, "low": low, "close": close}


RANGE = {"regime": "range", "close": 101, "atr": 2, "box": {"low": 100, "high": 108}}


def test_stop_wins_ambiguous_bar_and_a_gap_can_lose_more_than_one_r():
    bars = [candle(0, high=105, low=99)] + [candle(i) for i in range(1, 10)]
    assert regime_bt._play(bars, 0, RANGE, "quick", 0).net_r == -1

    bars[0] = candle(0, high=103)
    bars[1] = candle(1, opening=98, low=97)
    trade = regime_bt._play(bars, 0, RANGE, "quick", 10)
    assert trade.exit_index == 1
    assert trade.net_r == pytest.approx((-3 - 0.199) / 1.5)


def test_short_trailing_stop_uses_previous_close():
    state = {"regime": "trend_down", "close": 99, "ema20": 100, "atr": 2}
    bars = [candle(0, 99, 100, 95, 95), candle(1, 98, 100, 95, 98)]
    bars += [candle(i) for i in range(2, 10)]
    trade = regime_bt._play(bars, 0, state, "wide", 0)
    assert trade.exit_index == 1
    assert trade.net_r == 0


def test_metrics_account_for_flat_trades_and_drawdown():
    row = regime_bt.metrics([2, -1.5, 0, -0.5, 1])
    assert row["win_rate"] == 0.4
    assert row["payoff_ratio"] == 1.5
    assert row["expectancy_r"] == pytest.approx(row["mean_r"])
    assert row["below_minus_1r_rate"] == 0.2
    assert row["worst_r"] == -1.5
    assert row["max_drawdown_r"] == 2


def test_walk_forward_only_uses_completed_bars_and_past_trades(monkeypatch):
    seen = []

    def classify(history, tf):
        assert tf == "1d"
        seen.append(history[-1]["at"])
        return RANGE

    monkeypatch.setattr(regime_bt.regime_live, "classify", classify)
    bars = [candle(i) for i in range(69)]
    result = regime_bt.evaluate(bars, train_bars=23, test_bars=23, min_trades=3, cost_bps=10)
    assert seen == [bar["at"] for bar in bars[:-1]]
    first = result["folds"][0]["rows"]["range"]
    assert first["selected"] == "quick"
    assert first["training"]["quick"]["n"] == 13
    assert first["test"]["quick"]["n"] == 14
    assert result["summary"]["range"]["status"] == "기준 통과"
    assert result["summary"]["trend_up"]["status"] == "표본 부족"

    changed = deepcopy(bars)
    changed[46:] = [candle(i, low=98) for i in range(46, 69)]
    rerun = regime_bt.evaluate(changed, train_bars=23, test_bars=23, min_trades=3, cost_bps=10)
    assert rerun["folds"][0] == result["folds"][0]
    assert rerun["folds"][1]["rows"]["range"]["selected"] == "quick"
    assert rerun["summary"]["range"]["status"] == "우위 없음"


def test_real_classifier_and_bad_timestamps():
    bars = [candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(90)]
    assert regime_bt.regime_live.classify(bars, "1d")["regime"] == "trend_up"
    result = regime_bt.evaluate(bars, train_bars=60, test_bars=15)
    assert len(result["folds"]) == 2
    assert result["summary"]["trend_up"]["status"] == "표본 부족"
    with pytest.raises(ValueError, match="ascending order"):
        regime_bt.evaluate([bars[0], bars[0]])
