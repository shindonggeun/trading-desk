"""#239: exit rules replayed on the same fill (desk.exits)."""

from desk import exits


def _bars(path, start=0):
    """Bars from a list of (low, high, close); open = previous close."""
    out, prev = [], path[0][2]
    for i, (lo, hi, cl) in enumerate(path):
        out.append({"at": f"2026-01-{start + i + 1:02d}T00:00:00+00:00", "open": prev, "low": lo, "high": hi, "close": cl})
        prev = cl
    return out


# long from 100, stop 95 (1R = 5), T1 105, ATR 2
FILL = [(99, 101, 100)]


def test_a_straight_run_up_pays_the_trailing_rules_more_than_the_fixed_exit():
    up = [(100 + 3 * i, 103 + 3 * i, 102 + 3 * i) for i in range(10)]   # to ~130, no pullback
    r = exits.replay(1, _bars(FILL + up), "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, fixed_r=1.5)
    assert r["fixed"] == 1.5
    # half at T1 (+0.5R) and the rest marked at the last close 129 (+2.9R)
    assert r["trail_atr"] == round(0.5 + 0.5 * 29 / 5, 3)
    assert r["runner"] == round(29 / 5, 3)
    assert r["pyramid"] > r["runner"] and r["added"] == exits.ADD_SHARE


def test_a_reversal_after_one_r_gives_back_to_the_trail_not_to_the_stop():
    path = FILL + [(101, 106, 105.5), (105, 110, 109), (101, 109, 102), (96, 102, 97)]
    r = exits.replay(1, _bars(path), "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, fixed_r=0.5)
    # trail_atr: after T1 the best high 110 trails to 106; the third bar's low 101 hits it
    assert r["trail_atr"] == round(0.5 * 1 + 0.5 * 6 / 5, 3)
    # runner: +1R at 105 -> stop to 100, trailed 3 ATR under 110 = 104; hit at 104
    assert r["runner"] == round(4 / 5, 3)
    # pyramid adds half at 105; out at 104: +4 on the base, -1 on the add
    assert r["pyramid"] == round((4 - 0.5 * 1) / 5, 3)


def test_a_loser_is_the_same_minus_one_r_for_every_rule_and_the_pyramid_never_adds():
    path = FILL + [(97, 100, 98), (94, 98, 95)]
    r = exits.replay(1, _bars(path), "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, fixed_r=-1.0)
    assert r["trail_atr"] == r["trail_swing"] == r["runner"] == r["pyramid"] == -1.0 and r["added"] == 0


def test_a_gap_through_the_stop_fills_at_the_open():
    path = FILL + [(99, 101, 100), (90, 92, 91)]
    bars = _bars(path)
    bars[2]["open"] = 92.0
    r = exits.replay(1, bars, "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, fixed_r=-1.6)
    assert r["runner"] == round((92 - 100) / 5, 3)


def test_a_short_mirrors_the_long():
    down = [(97 - 3 * i, 100 - 3 * i, 98 - 3 * i) for i in range(8)]
    r = exits.replay(-1, _bars(FILL + down), "2026-01-01T00:00:00+00:00", 100.0, 105.0, 95.0, 2.0, fixed_r=1.5)
    assert r["runner"] == round((100 - 77) / 5, 3) and r["trail_atr"] > 1.5


def test_nothing_to_replay_without_a_fill_or_atr():
    bars = _bars(FILL + [(100, 101, 100)])
    assert exits.replay(1, bars, None, 100.0, 95.0, 105.0, 2.0, 0.0) is None
    assert exits.replay(1, bars, "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 0.0, 0.0) is None
    assert exits.replay(1, bars, "2030-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, 0.0) is None


def test_the_report_puts_the_rules_side_by_side_per_product_kind_and_split():
    from desk import backtest, horizons

    h = horizons.HORIZONS["swing"]

    def trade(day, kind, fixed, runner):
        t = backtest.Trade("X", day, "long", "pullback", "up", 50, "t2", filled_shares=1.0, kind=kind)
        t.exits = {"fixed": fixed, "trail_atr": fixed, "trail_swing": fixed, "runner": runner, "pyramid": runner}
        return t

    trades = [trade(f"2020-{m:02d}-01", "stock", 1.0, 4.0) for m in range(1, 13)]
    trades += [trade(f"2024-{m:02d}-01", "leveraged", -1.0, -1.0) for m in range(1, 6)]
    trades.append(backtest.Trade("X", "2024-06-01", "long", "pullback", "up", 50, "unfilled"))
    rep = backtest.exit_report(trades, "2024-01-01", h)
    assert rep["rules"] == list(exits.VARIANTS)
    assert rep["stock"]["runner"]["train"] == {"n": 12, "win_rate": 1.0, "avg_r": 4.0, "median_r": 4.0,
                                               "big_share": 1.0, "losing_days": 0, "max_dd_r": 0.0}
    assert rep["leveraged"]["fixed"]["valid"]["losing_days"] == 5 and rep["leveraged"]["fixed"]["valid"]["max_dd_r"] == 5.0 and rep["stock"]["fixed"]["valid"]["n"] == 0


def test_the_fill_bar_counts_its_stop_like_the_fixed_exit():
    """#239 review: a limit filled intrabar can fall to the stop in the same bar."""
    path = [(94, 101, 100), (100, 120, 119)]               # fill bar dips through the stop, then a rally
    r = exits.replay(1, _bars(path), "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, fixed_r=-1.0)
    assert r["trail_atr"] == r["trail_swing"] == r["runner"] == r["pyramid"] == -1.0


def test_the_pyramid_adds_at_the_open_on_a_gap_and_the_worse_order_on_a_round_trip():
    gap = _bars(FILL + [(107, 112, 111)])
    gap[1]["open"] = 108.0                                  # opens past +1R (105)
    r = exits.replay(1, gap, "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, fixed_r=1.0)
    assert r["pyramid"] == round((11 + 0.5 * 3) / 5, 3)    # the add bought at 108, marked at 111
    round_trip = _bars(FILL + [(99, 106, 101), (101, 102, 101)])   # +1R and back under break-even
    r = exits.replay(1, round_trip, "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, fixed_r=0.5)
    assert r["runner"] == 0.0 and r["pyramid"] == round(0.5 * (100 - 105) / 5, 3)


def test_the_time_stop_closes_a_trade_that_never_got_going():
    flat = _bars(FILL + [(99, 102, 101)] * 6)
    r = exits.replay(1, flat, "2026-01-01T00:00:00+00:00", 100.0, 95.0, 105.0, 2.0, fixed_r=0.2, time_stop=3)
    assert r["trail_atr"] == r["runner"] == round(1 / 5, 3)
