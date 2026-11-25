"""#1001 a KRX stock's day split after the regular close: regular · off-hours (NXT / after-hours auction /
the estimate on top) · combined - ``change_pct`` keeps its meaning (against the last regular close)."""
from datetime import date

from desk.estimate import change_split

TUE = date(2026, 11, 24)
WED = date(2026, 11, 25)
AT = "2026-11-25T08:00:00+00:00"


def test_a_minus_10_day_then_nxt_minus_0_2_reads_minus_10_2_for_the_day():
    closes = [(TUE, 50000.0), (WED, 45000.0)]                    # today's regular session: -10 %
    got = change_split(44910.0, closes, WED, "post", "nxt", AT)  # NXT after-market: -0.2 % on today's close
    assert got["regular"] == {"pct": -10.0, "base": 50000.0, "base_date": "2026-11-24"}
    assert got["extended"] == {"pct": -0.2, "base": 45000.0, "base_date": "2026-11-25",
                               "session": "post", "source": "nxt", "at": AT}
    assert got["total"] == {"pct": -10.18, "base": 50000.0, "base_date": "2026-11-24"}


def test_before_the_session_nxt_has_no_regular_part_and_both_others_are_on_the_previous_close():
    closes = [(date(2026, 11, 23), 49000.0), (TUE, 50000.0)]    # today's session has not run
    got = change_split(50500.0, closes, WED, "pre", "nxt", AT)
    assert got["regular"] is None
    assert got["extended"]["pct"] == got["total"]["pct"] == 1.0
    assert got["extended"]["base_date"] == got["total"]["base_date"] == "2026-11-24"


def test_the_day_after_a_holiday_is_against_the_last_session_before_it():
    closes = [(date(2026, 11, 20), 50000.0), (TUE, 51000.0)]    # Mon 11-23 closed: no bar
    got = change_split(51510.0, closes, TUE, "post", "nxt", AT)
    assert got["regular"] == {"pct": 2.0, "base": 50000.0, "base_date": "2026-11-20"}
    assert got["extended"]["pct"] == 1.0 and got["total"]["pct"] == 3.02


def test_after_the_close_without_todays_close_there_is_no_split_unless_the_row_knows_it():
    closes = [(TUE, 50000.0)]                                    # Yahoo has not caught up with today
    assert change_split(45000.0, closes, WED, "post", "krx-overtime", AT) is None
    # the KRX after-hours auction carries today's close (#283): it stands in
    got = change_split(45090.0, closes, WED, "post", "krx-overtime", AT, today_close=45000.0)
    assert got["regular"]["pct"] == -10.0 and got["extended"]["pct"] == 0.2 and got["total"]["pct"] == -9.82
    assert got["extended"]["source"] == "krx-overtime"


def test_the_evening_estimate_splits_the_same_day_and_not_the_next_morning():
    closes = [(TUE, 50000.0), (WED, 45000.0)]
    assert change_split(44550.0, closes, WED, "closed", "binance-estimate", AT)["extended"]["pct"] == -1.0
    # Thursday before 08:00: Wednesday's estimate is not "today's" evening any more - the change says it all
    assert change_split(44550.0, closes, date(2026, 11, 26), "closed", "binance-estimate", AT) is None


def test_a_halted_stock_moves_nowhere_and_bad_inputs_give_none():
    closes = [(TUE, 50000.0), (WED, 50000.0)]                    # trading halted: today's close is yesterday's
    got = change_split(50000.0, closes, WED, "post", "nxt", AT)
    assert (got["regular"]["pct"], got["extended"]["pct"], got["total"]["pct"]) == (0.0, 0.0, 0.0)
    assert change_split(0.0, closes, WED, "post", "nxt", AT) is None
    assert change_split(float("nan"), closes, WED, "post", "nxt", AT) is None
    assert change_split(45000.0, [], WED, "post", "nxt", AT) is None
    assert change_split(45000.0, [(WED, 45000.0)], WED, "post", "nxt", AT) is None    # no previous close
    assert change_split(45000.0, [(TUE, float("nan")), (WED, 45000.0)], WED, "post", "nxt", AT) is None
