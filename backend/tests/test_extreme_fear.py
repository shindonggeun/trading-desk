from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from desk.extreme_fear import Bar, Fixing, Session, evaluate

NY = ZoneInfo("America/New_York")


def _sessions(n, start=date(2024, 6, 10), *, holidays=(), early_close=()):
    sessions = []
    day = start
    while len(sessions) < n:
        if day.weekday() < 5 and day not in holidays:
            sessions.append(Session(day, datetime.combine(day, time(9, 30), NY),
                                    datetime.combine(day, time(13 if day in early_close else 16), NY)))
        day += timedelta(days=1)
    return sessions


def _bar(session, close, *, opens=None, high=None, low=None):
    opens = close if opens is None else opens
    return Bar(session.day, opens, max(opens, close) if high is None else high,
               min(opens, close) if low is None else low, close, session.closes_at)


def _fixings(sessions, values, lag):
    return [Fixing(sessions[i].day, value, sessions[i].closes_at + timedelta(minutes=lag))
            for i, value in values.items()]


def _run(sessions, bars, cnns, vixs, *, as_of=None, symbol="SOXL", cost_bps=0):
    return evaluate(symbol, sessions, bars, cnns, vixs,
                    as_of=as_of or sessions[-1].closes_at + timedelta(hours=1), cost_bps=cost_bps)


@pytest.mark.parametrize("symbol", ["SOXL", "KORU"])
def test_wilder_inclusive_thresholds_fixed_horizons_mae_and_separate_peak(symbol):
    sessions = _sessions(280, holidays=(date(2024, 7, 4),), early_close=(date(2024, 7, 3),))
    closes = [20, 23] + [16] * 13 + [90] * (len(sessions) - 15)
    bars = [_bar(session, value) for session, value in zip(sessions, closes, strict=True)]
    bars[15] = _bar(sessions[15], 90, opens=100, low=89)
    bars[17] = _bar(sessions[17], 90, low=80)
    bars[30] = _bar(sessions[30], 90, high=145)
    bars[266] = _bar(sessions[266], 80)
    cnn = _fixings(sessions, {13: 11, 14: 10, 15: 11}, 30)
    vix = _fixings(sessions, {13: 32, 14: 30, 15: 32}, 15)
    current = _run(sessions, {symbol: bars}, cnn, vix, symbol=symbol,
                   as_of=sessions[14].closes_at + timedelta(minutes=40))["current"]
    assert current["date"] == sessions[14].day.isoformat()
    assert current["matched_count"] == 3
    assert [item["value"] for item in current["conditions"].values()] == [30.0, 10, 30]

    report = _run(sessions, {symbol: bars}, cnn, vix, symbol=symbol, cost_bps=10)
    [sample] = report["signal_history"]
    assert sample["entry_date"] == sessions[15].day.isoformat()
    for horizon in (5, 20, 60, 252):
        outcome = sample["outcomes"][str(horizon)]
        assert outcome["exit_date"] == sessions[14 + horizon].day.isoformat()
        assert outcome["status"] == "complete"
        exit_price = 80 if horizon == 252 else 90
        expected = 100 * (exit_price * .999 / (100 * 1.001) - 1)
        assert outcome["return_pct"] == pytest.approx(expected)
        assert outcome["mae_pct"] == -20
        rsi_only = report["strategies"]["rsi_only"]["horizons"][str(horizon)]
        assert (rsi_only["n"], rsi_only["wins"], rsi_only["losses"]) == (1, 0, 1)
        assert rsi_only["median_return_pct"] == pytest.approx(expected)
        assert rsi_only["worst_mae_pct"] == -20
        buy_hold = report["strategies"]["buy_hold"]["horizons"][str(horizon)]
        # The first common macro date is one session before the RSI warms up.
        assert (buy_hold["n"], buy_hold["wins"], buy_hold["losses"], buy_hold["overlap_skipped"]) == (1, 1, 0, 2)
        assert buy_hold["median_return_pct"] == pytest.approx(100 * (90 * .999 / (16 * 1.001) - 1))
        assert buy_hold["worst_mae_pct"] == 0
    five = report["strategies"]["triple"]["horizons"]["5"]
    assert (five["n"], five["wins"], five["losses"], five["win_rate"]) == (1, 0, 1, 0)
    assert five["median_loss_pct"] == five["worst_return_pct"] == five["median_return_pct"]
    assert five["loss_quartiles_pct"] == [five["median_loss_pct"]] * 3
    assert five["win_rate_ci95"][0] == 0 and 0 < five["win_rate_ci95"][1] < 1
    assert sample["ex_post_252"]["max_return_pct"] == pytest.approx(100 * (145 * .999 / (100 * 1.001) - 1))
    assert report["strategies"]["triple"]["ex_post_252"]["n"] == 1
    assert report["strategies"]["buy_hold"]["signal_count"] == 3
    assert report["cost_bps"] == 10


def test_delayed_signal_at_next_open_sends_entry_to_following_session_after_holiday():
    sessions = _sessions(30, holidays=(date(2024, 7, 4),), early_close=(date(2024, 7, 3),))
    signal_idx = next(i for i, session in enumerate(sessions) if session.day == date(2024, 7, 3))
    bars = {"SOXL": [_bar(session, 100 - i) for i, session in enumerate(sessions)]}
    assert sessions[signal_idx + 1].day == date(2024, 7, 5)
    vix = _fixings(sessions, {signal_idx: 35}, 15)
    cnn = [Fixing(sessions[signal_idx].day, 1, sessions[signal_idx + 1].opens_at)]
    result = _run(sessions, bars, cnn, vix)
    [sample] = result["signal_history"]
    assert sample["entry_date"] == "2024-07-08"
    assert datetime.fromisoformat(sample["available_at"]) == sessions[signal_idx + 1].opens_at
    for mode in ("rsi_only", "buy_hold"):
        stats = result["strategies"][mode]["horizons"]["5"]
        assert stats["n"] == 1
        assert stats["median_return_pct"] == sample["outcomes"]["5"]["return_pct"]

    too_late = [replace(cnn[0], available_at=sessions[signal_idx + 1].closes_at)]
    before_vix_close = _fixings(sessions, {signal_idx: 35}, 1)
    assert _run(sessions, bars, too_late, vix)["signal_history"] == []
    assert _run(sessions, bars, cnn, before_vix_close)["signal_history"] == []


def test_future_fixings_are_not_shown_or_used_and_outcomes_require_matured_exit():
    sessions = _sessions(40)
    bars = {"SOXL": [_bar(session, 90 - i) for i, session in enumerate(sessions)]}
    cnn = _fixings(sessions, {14: 10}, 35)
    vix = _fixings(sessions, {14: 30}, 15)
    near_close = _run(sessions, bars, cnn, vix, as_of=sessions[14].closes_at + timedelta(minutes=20))
    assert near_close["current"]["conditions"]["cnn"] == {
        "value": None, "operator": "<=", "threshold": 10, "source": "CNN Fear & Greed",
        "status": "missing", "available_at": None, "reason": "no_same_session_fixing",
    }
    assert near_close == _run(sessions, bars, [], vix, as_of=sessions[14].closes_at + timedelta(minutes=20))
    before_vix = _run(sessions, bars, cnn, vix, as_of=sessions[14].closes_at + timedelta(minutes=1))
    assert before_vix["current"]["conditions"]["vix"]["status"] == "delayed"
    assert near_close["signal_history"] == []
    assert near_close["strategies"]["triple"]["horizons"]["5"]["win_rate"] is None

    signal_confirmed = _run(sessions, bars, cnn, vix, as_of=sessions[15].opens_at - timedelta(minutes=1))
    [recent] = signal_confirmed["signal_history"]
    assert recent["outcomes"]["5"]["status"] == "not_entered"
    inside = _run(sessions, bars, cnn, vix, as_of=sessions[18].closes_at)
    assert inside["signal_history"][0]["outcomes"]["5"]["status"] == "immature"
    assert inside["signal_history"][0]["ex_post_252"] == {"status": "immature", "max_return_pct": None}

    missing_entry = {"SOXL": [bar for bar in bars["SOXL"] if bar.day != sessions[15].day]}
    absent = _run(sessions, missing_entry, cnn, vix, as_of=sessions[18].closes_at)
    assert absent["strategies"]["triple"]["horizons"]["5"]["not_entered"] == 1
    missing_path = {"SOXL": [bar for bar in bars["SOXL"] if bar.day != sessions[17].day]}
    gap = _run(sessions, missing_path, cnn, vix)
    assert gap["strategies"]["triple"]["horizons"]["5"]["missing"] == 1
    assert gap["strategies"]["triple"]["horizons"]["5"]["n"] == 0


def test_nonoverlap_null_gap_and_flat_are_separate_from_wins():
    sessions = _sessions(40)
    bars = {"SOXL": [_bar(session, max(80, 100 - i)) for i, session in enumerate(sessions)]}
    bars["SOXL"][19] = _bar(sessions[19], 85)
    cnn = _fixings(sessions, {14: 8, 16: 7, 17: 11, 18: 9}, 25)
    vix = _fixings(sessions, {i: 35 for i in range(14, 19)}, 15)
    report = _run(sessions, bars, cnn, vix)
    assert report["coverage"]["verified_macro_sessions"] == 4
    assert [item["signal_date"] for item in report["signal_history"]] == [
        sessions[18].day.isoformat(), sessions[14].day.isoformat(),
    ]
    metrics = report["strategies"]["triple"]
    assert metrics["episodes_suppressed"] == 1
    five = metrics["horizons"]["5"]
    assert (five["n"], five["flat"], five["wins"], five["losses"], five["overlap_skipped"]) == (1, 1, 0, 0, 1)
    assert report["signal_history"][0]["outcomes"]["5"]["status"] == "overlap_skipped"


def test_missing_rsi_warmup_and_wrong_day_fixing_are_not_backfilled():
    sessions = _sessions(32)
    bars = [_bar(session, 100 - i) for i, session in enumerate(sessions)]
    bars[12] = replace(bars[12], available_at=sessions[23].closes_at)
    macro = {14: 1, 15: 1}
    cnn = _fixings(sessions, macro, 20)
    vix = _fixings(sessions, {14: 40}, 16)
    report = _run(sessions, {"SOXL": bars}, cnn, vix, as_of=sessions[15].closes_at + timedelta(hours=1))
    assert report["current"]["conditions"]["rsi"]["reason"] == "rsi_warmup_or_gap"
    assert report["current"]["conditions"]["vix"]["status"] == "missing"
    assert report["signal_history"] == []
    assert report["status"] == "unverified"


def test_prelisting_and_separate_soxl_signal_for_koru():
    sessions = _sessions(38, start=date(2013, 4, 8))
    bars = {"SOXL": [_bar(session, 100 - i) for i, session in enumerate(sessions)],
            "KORU": [_bar(session, 30 + i) for i, session in enumerate(sessions)]}
    cnn = _fixings(sessions, {0: 1, 17: 5}, 20)
    vix = _fixings(sessions, {0: 40, 17: 40}, 15)
    report = _run(sessions, bars, cnn, vix, symbol="KORU")
    assert report["strategies"]["triple"]["prelisting_sessions"] == 2
    assert report["strategies"]["triple"]["signal_count"] == 0
    assert report["strategies"]["soxl_to_koru"]["signal_count"] == 1
    assert report["strategies"]["soxl_to_koru"]["horizons"]["5"]["wins"] == 1
    assert report["signal_history"] == []


@pytest.mark.parametrize("symbol", ["SOXL", "KORU"])
def test_absent_real_archive_cannot_produce_rates_or_baseline_win_counts(symbol):
    result = evaluate(symbol, [], {}, [], [], as_of=datetime(2024, 6, 1, tzinfo=UTC))
    assert result["status"] == "unverified" and result["current"]["date"] is None
    assert result["signal_history"] == []
    for strategy in result["strategies"].values():
        for stats in strategy["horizons"].values():
            assert stats["n"] == 0 and stats["win_rate"] is None and stats["win_rate_ci95"] is None


def test_invalid_prices_duplicates_and_naive_cutoff_are_rejected():
    sessions = _sessions(20)
    valid = _bar(sessions[0], 10)
    with pytest.raises(ValueError, match="invalid or duplicate adjusted bar"):
        _run(sessions, {"SOXL": [valid, valid]}, [], [])
    with pytest.raises(ValueError, match="invalid or duplicate adjusted bar"):
        _run(sessions, {"SOXL": [replace(valid, high=float("nan"))]}, [], [])
    with pytest.raises(ValueError, match="invalid research symbol, cutoff or execution cost"):
        _run(sessions, {}, [], [], as_of=datetime(2024, 6, 1))
