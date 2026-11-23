from datetime import UTC, date, datetime, time, timedelta

import pytest

from desk import nq_weekly_research as nq
from desk import weekly_chart_evidence as evidence

FIRST = date(2025, 7, 14)


def _bar(i: int, *, close: float = 100, high: float = 109, low: float = 89) -> dict:
    monday = FIRST + timedelta(weeks=i)
    return {"at": monday.isoformat(), "open": close, "close": close, "high": high, "low": low}


def _close(i: int) -> datetime:
    return datetime.combine(FIRST + timedelta(weeks=i, days=4), time(17), nq.NY).astimezone(UTC)


def _confirm(i: int) -> datetime:
    return datetime.combine(FIRST + timedelta(weeks=i, days=6), time(18), nq.NY).astimezone(UTC)


def _source(bars: list[dict], fetched: datetime) -> dict:
    return {"source": "yahoo", "fetched_at": fetched.isoformat(), "bars": bars}


def _observed(bars: list[dict], i: int, price: dict | None = None) -> dict:
    now = _confirm(i)
    rows = bars[:i + 2]
    if len(rows) == i + 1:
        rows = [*rows, _bar(i + 1)]
    return evidence.build(_source(rows, now), price, now)


def _anchor() -> list[dict]:
    return [_bar(i) for i in range(55)] + [_bar(55, high=103, low=99)]


def test_forming_week_never_enters_evidence_even_with_invalid_ohlc():
    rows = _anchor()
    observed = _observed(rows, 55)
    forming = {"at": _bar(56)["at"], "close": 0}
    assert _observed([*rows, forming], 55) == observed
    assert observed["week"] == {
        "start": rows[-1]["at"], "source": "yahoo", "candle": "closed",
        "closedAt": _close(55).isoformat(), "fetchedAt": _confirm(55).isoformat(),
    }
    assert observed["usage"] == "research_only"
    assert observed["assumptions"]["rollover"] == "unknown"
    assert observed["assumptions"]["originalSettings"] == observed["assumptions"]["originalAnchors"] == "unknown"


def test_latest_week_requires_confirmation_from_the_next_opened_session():
    rows = _anchor()
    cutoff = _close(55)
    source = _source(rows, cutoff - timedelta(seconds=1))
    before = evidence.build(source, None, cutoff - timedelta(seconds=1))
    assert before["week"]["start"] == rows[-2]["at"]
    assert evidence.build(source, None, cutoff)["reasonCode"] == "unconfirmed_weekly_close"
    next_open = _confirm(55)
    no_label = _source(rows, next_open)
    assert evidence.build(no_label, None, next_open)["reasonCode"] == "unconfirmed_weekly_close"
    next_label = [*rows, _bar(56)]
    early = _source(next_label, next_open - timedelta(seconds=1))
    assert evidence.build(early, None, next_open)["reasonCode"] == "unconfirmed_weekly_close"
    stale = _source(rows[:-1], next_open)
    assert evidence.build(stale, None, next_open)["reasonCode"] == "stale_weekly_bars"
    assert _observed(rows, 55)["week"]["start"] == rows[-1]["at"]


@pytest.mark.parametrize(
    ("weekly", "code"),
    [(None, "weekly_source_unavailable"),
     ({"source": "unsupported"}, "unsupported_weekly_source"),
     ({"source": "yahoo", "fetched_at": "2026-04-03T16:00:00", "bars": []}, "invalid_weekly_timestamp"),
     (_source([], _close(2)), "no_completed_week"),
     (_source([_bar(0), _bar(2)], _close(2)), "invalid_weekly_bars"),
     (_source([_bar(0), _bar(0)], _close(2)), "invalid_weekly_bars"),
     (_source([_bar(0, close=0)], _close(2)), "invalid_weekly_bars")],
)
def test_unusable_weekly_inputs_are_explicit(weekly, code):
    got = evidence.build(weekly, None, _close(2))
    assert got["status"] == "unavailable"
    assert got["reasonCode"] == code and got["reason"]
    assert "evidence" not in got


def test_short_history_has_partial_input_and_explicit_missing_values():
    observed = _observed([_bar(i) for i in range(16)], 15)
    assert observed["status"] == "available"
    assert observed["evidence"]["bollinger"]["value"] is None
    assert observed["evidence"]["bollinger"]["reasonCode"] == "insufficient_history"
    assert observed["evidence"]["rsi"]["value"] == 50.0
    assert observed["evidence"]["rsi"]["state"] == "normal"
    assert observed["overlap"]["state"] == "missing"
    assert observed["rebound"]["reasonCode"] == observed["recovery"]["reasonCode"] == "insufficient_history"


def test_chart_reference_selects_the_latest_usable_result_and_only_changes_distances():
    rows = _anchor()
    now = _confirm(55)

    def _frame(at: datetime, close: float) -> dict:
        return {"source": "yahoo", "fetched_at": now.isoformat(),
                "bars": [{"at": at.isoformat(), "close": close}]}

    price = evidence.chart_price(["5m", "15m", "60m", "240m", "1d"], [
        _frame(now - timedelta(hours=5), 100), RuntimeError(),
        _frame(now - timedelta(hours=1), 125), _frame(now - timedelta(hours=1), 126),
        _frame(now + timedelta(minutes=5), 127),
    ], now)
    got = _observed(rows, 55, price)
    assert got["current"] == {
        "price": 125, "source": "yahoo", "asOf": (now - timedelta(hours=1)).isoformat(),
        "fetchedAt": now.isoformat(), "timeframe": "60m", "kind": "chart_bar_close", "reasonCode": None,
        "reason": None,
    }
    assert got["evidence"]["bollinger"]["distancePct"] == pytest.approx(-20)
    assert got["evidence"]["rsi"]["distancePct"] is None
    blank = _observed(rows, 55)
    assert blank["evidence"]["bollinger"]["distancePct"] is None
    assert got["rebound"] == blank["rebound"] and got["recovery"] == blank["recovery"]
    assert got["overlap"] == blank["overlap"]
    stale = evidence.chart_price(["1d"], [_frame(now - timedelta(days=8), 120)], now)
    assert _observed(rows, 55, stale)["current"]["reasonCode"] == "stale_chart_price"


def test_overlap_uses_observed_price_contacts_and_keeps_missing_input_explicit():
    basis = nq._PriceBasis(4.0, {"bollinger": 99.0, "channel": 101.0, "fib_2618": None}, ["2026-01-05"], [])
    rows = evidence._evidence(_bar(55, high=104, low=100), basis, 28.0, 110.0)
    assert rows["channel"]["anchorWeeks"] == ["2026-01-05"]
    assert rows["fib"]["reasonCode"] == "fib_anchors_unavailable"
    overlap = evidence._overlap(rows)
    assert overlap["state"] == "overlap"
    assert overlap["basis"] == ["bollinger", "channel", "rsi"]
    assert (overlap["low"], overlap["high"]) == (99.0, 101.0)

    slipped = evidence._evidence(_bar(55, close=95, high=100, low=94), basis, 28.0, None)
    assert slipped["bollinger"]["state"] == "below"
    assert evidence._overlap(slipped)["state"] == "missing"  # a missing Fibonacci level may matter

    all_price_levels = nq._PriceBasis(4.0, {"bollinger": 105.0, "channel": 110.0, "fib_2618": 90.0}, [], [])
    rsi_only = evidence._evidence(_bar(55, high=101, low=99), all_price_levels, 28.0, None)
    assert rsi_only["rsi"]["state"] == "oversold"
    assert evidence._overlap(rsi_only)["state"] == "none"


def test_a_rebound_can_confirm_while_recovery_stays_watching():
    anchor = _anchor()
    first = _bar(56, close=122, high=124, low=105)
    forming = _observed([*anchor, first], 55)
    assert forming["rebound"]["state"] == forming["recovery"]["state"] == "watching"
    assert forming["rebound"]["decisionWeek"] is None and forming["recovery"]["decisionWeek"] is None
    confirmed = _observed([*anchor, first], 56)
    assert confirmed["rebound"]["state"] == "confirmed"
    assert confirmed["rebound"]["anchorWeek"] == anchor[-1]["at"]
    assert confirmed["rebound"]["decisionWeek"] == first["at"]
    assert confirmed["recovery"]["state"] == "watching"
    assert confirmed["recovery"]["decisionWeek"] is None


def test_bounce_low_invalidation_does_not_disable_later_recovery():
    anchor = _anchor()
    dip = _bar(56, close=101, high=103, low=86)
    future = [dip, _bar(57, close=122, high=124, low=106), _bar(58, close=123, high=125, low=118)]
    got = _observed([*anchor, *future], 58)
    assert got["rebound"]["anchorWeek"] == got["recovery"]["anchorWeek"] == anchor[-1]["at"]
    assert got["rebound"]["state"] == "invalidated"
    assert got["rebound"]["decisionWeek"] == dip["at"]
    assert got["recovery"]["state"] == "confirmed"
    assert got["recovery"]["decisionWeek"] == future[-1]["at"]


def test_checks_report_expiry_weeks_and_remove_anchors_outside_lookback():
    rows = _anchor() + [_bar(i, close=105, high=106, low=104) for i in range(56, 69)]
    expired = _observed(rows, 67)
    assert expired["rebound"]["state"] == expired["recovery"]["state"] == "not_confirmed"
    assert expired["rebound"]["decisionWeek"] == rows[57]["at"]
    assert expired["recovery"]["decisionWeek"] == rows[67]["at"]
    missing = _observed(rows, 68)
    assert missing["rebound"]["anchorWeek"] is None and missing["recovery"]["anchorWeek"] is None
    assert missing["rebound"]["reasonCode"] == missing["recovery"]["reasonCode"] == "no_observation_week"
