"""#166 stage 5: the #312 expected-R gate measured in the main backtest report."""

from datetime import date, timedelta

from desk import backtest, horizons

SWING = horizons.get("swing")


def _t(at: str, setup: str, r: float, filled: bool = True, distance: float = 0.5) -> backtest.Trade:
    t = backtest.Trade("SOXL", at, "long", setup, "uptrend", 70, "t2" if r > 0 else "stop",
                       filled_shares=1.0 if filled else 0.0, r_net=r if filled else None, took_t1=r > 0)
    if not filled:
        t.status = "unfilled"
    t.entry_kind, t.distance_atr = "limit", distance
    return t


def _days(n: int, start: str = "2020-01-01") -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def test_the_gate_skips_a_losing_setup_judged_out_of_its_own_block():
    days = _days(800)
    trades = []
    for i, d in enumerate(days):
        trades.append(_t(d, "pullback", 1.0 if i % 3 else -1.0))       # +0.33R a fill
        trades.append(_t(d, "dip_catch", -1.0 if i % 3 else 1.0))      # -0.33R a fill
    cut = backtest.split_cut(t.at for t in trades)
    rep = backtest.ev_gate_report(trades, cut, SWING)
    off, on = rep["gates"]["off"], rep["gates"]["ev>0"]
    assert rep["folds"] == backtest.EV_GATE_FOLDS
    assert on["train"]["kept"] < off["train"]["kept"] and on["valid"]["kept"] < off["valid"]["kept"]
    assert set(on["by_setup"]) == {"long:pullback"}                      # dip_catch is negative in every block
    assert on["train"]["expectancy_r"] > off["train"]["expectancy_r"]
    assert on["valid"]["per_plan_r"] > off["valid"]["per_plan_r"]        # per plan of all: a skip is 0 R
    assert on["train"]["plans"] == off["train"]["plans"]
    assert set(on["crash_windows"]) == set(backtest.CRASH_WINDOWS)


def test_unknown_worth_is_kept_by_ev_and_skipped_by_known_and_other_frames_have_no_gate():
    days = _days(600)
    trades = [_t(d, "pullback", 1.0) for d in days] + [_t(days[-1], "range_low", -1.0)]   # range_low: thin
    cut = backtest.split_cut(t.at for t in trades)
    rep = backtest.ev_gate_report(trades, cut, SWING)
    assert "long:range_low" in rep["gates"]["ev>0"]["by_setup"]
    assert "long:range_low" not in rep["gates"]["ev>0+known"]["by_setup"]
    assert backtest.ev_gate_report(trades, cut, horizons.get("intraday")) == {}
    assert backtest.ev_gate_report(trades, None, SWING) == {}
