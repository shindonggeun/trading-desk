"""Synthetic product, session and point-in-time checks for daily chart evidence."""

import math
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from desk import daily_resistance as subject
from desk import market_calendar as calendar

DAY = date(2026, 11, 24)
AFTER = datetime(2026, 11, 24, 21, 20, tzinfo=UTC)


def _bar(day, opens=97, high=100, low=95, close=98, volume=100):
    at = datetime.combine(day, time(), calendar.ET).astimezone(UTC)
    return {"at": at.isoformat(), "open": opens, "high": high, "low": low, "close": close, "volume": volume}


def _history(day=DAY):
    rows = []
    for _ in range(36):
        day = calendar.previous_open("us", day)
        rows.append(_bar(day))
    rows.reverse()
    rows[-7]["high"] = 105
    rows[-1].update(high=103, close=102)
    return rows


def _source(rows, ticker="SOXX", checked=AFTER):
    return {"ticker": ticker, "interval": "1d", "source": "yahoo", "bars": rows, "fetched_at": checked.isoformat()}


@pytest.fixture
def swing_only(monkeypatch):
    monkeypatch.setattr(subject.channels, "pick", lambda *_: ([], []))


@pytest.mark.parametrize(("prices", "signal", "touched", "wick", "strong", "breakout"), [
    ((103, 109, 97, 100), "wick_rejection", True, True, False, False),
    ((96, 105.5, 95, 105), "strong_bullish_close", True, False, True, False),
    ((102, 114, 101, 112), "close_breakout", True, False, True, True),
    ((110, 114, 109, 112), "close_breakout", False, False, False, True),
    ((102, 105, 100, 104), "resistance_touch", True, False, False, False),
    ((100, 102.8, 98, 100), "resistance_near", False, False, False, False),
    ((100, 101, 98, 100), "none", False, False, False, False),
])
def test_daily_reaction_priority_and_metrics(swing_only, prices, signal, touched, wick, strong, breakout):
    history = _history()
    target = _bar(DAY, *prices, volume=220)
    result = subject.build("SOXX", _source([*history, target]), AFTER)

    assert result["status"] == "confirmed"
    assert result["reason"] is None
    assert result["instrument"] == {"kind": "etf", "unit": "USD"}
    assert result["previous_close"] == {"at": history[-1]["at"], "price": 102}
    assert result["resistance"] == {
        "kind": "swing_high", "price": 105, "basis_at": history[-1]["at"], "projected_at": target["at"],
        "ref": {"kind": "swing", "side": "high", "at": history[-7]["at"]},
    }
    assert result["reaction"] == {
        "kind": signal, "near": touched or signal == "resistance_near", "touched": touched,
        "wick_rejection": wick, "strong_bullish_close": strong, "close_breakout": breakout,
    }
    opens, high, low, close = prices
    span = high - low
    assert result["metrics"] == pytest.approx({
        "range": span, "body_ratio": abs(opens - close) / span,
        "upper_wick_ratio": (high - max(opens, close)) / span, "close_position": (close - low) / span,
        "body_atr": abs(opens - close) / subject.channels.atr(history),
        "close_distance_pct": (close / 105 - 1) * 100,
    })
    assert result["volume"] == {"ratio": 2.2, "hot": True}


def test_channel_is_selected_from_history_and_projected_to_target(monkeypatch):
    history = _history()
    target = _bar(DAY, 102, 115, 101, 112)
    future = _bar(DAY + timedelta(days=1), 107, 120, 101, 111)
    seen = []

    def pick(bars, tf, unit, atr):
        seen.append((bars, tf, unit, atr))
        return [SimpleNamespace(i=0, j=7, upper=lambda x: math.log(103 + 0.1 * x))], []

    monkeypatch.setattr(subject.channels, "pick", pick)
    result = subject.build("SOXX", _source([*history, target, future]), AFTER)
    assert seen == [(history, "1d", subject.channels.atr(history) / 102, subject.channels.atr(history))]
    assert result["resistance"] == {
        "kind": "channel_upper", "price": pytest.approx(103 + 0.1 * len(history)),
        "basis_at": history[-1]["at"], "projected_at": target["at"],
        "ref": {"kind": "channel", "edge": "upper", "id": f"1d-channel-{history[0]['at']}-{history[7]['at']}"},
    }
    assert result["reaction"]["kind"] == "close_breakout"


def test_us_market_open_and_lag_do_not_confirm_a_visible_bar(swing_only):
    rows = [*_history(), _bar(DAY, 102, 114, 101, 112)]
    opened = AFTER.replace(hour=15)
    waiting = AFTER.replace(minute=5)
    for now, reason in ((opened, "session_open"), (waiting, "awaiting_confirmation")):
        response = subject.build("SOXX", _source(rows, checked=now), now)
        assert response["status"] == "in_progress" and response["reason"] == reason
        assert response["bar"]["close"] == 112 and response["reaction"] is None
    no_today = subject.build("SOXX", _source(rows[:-1], checked=opened), opened)
    assert no_today["reason"] == "session_open" and no_today["bar"] is None
    stale = subject.build("SOXX", _source(rows, checked=waiting), AFTER)
    assert stale["status"] == "unavailable" and stale["reason"] == "source_delayed"
    assert stale["bar"] is None and stale["reaction"] is None


def test_closed_us_market_uses_last_session_and_half_day_has_own_confirmation(swing_only):
    thanksgiving = date(2026, 11, 26)
    wednesday = thanksgiving - timedelta(days=1)
    holiday_now = datetime(2026, 11, 26, 16, tzinfo=UTC)
    holiday = subject.build("SOXX", _source([*_history(wednesday), _bar(wednesday)], checked=holiday_now), holiday_now)
    assert holiday["status"] == "confirmed" and holiday["session"]["trade_date"] == wednesday.isoformat()

    friday = thanksgiving + timedelta(days=1)
    rows = [*_history(friday), _bar(friday)]
    waiting = datetime(2026, 11, 27, 18, 10, tzinfo=UTC)
    pending = subject.build("SOXX", _source(rows, checked=waiting), waiting)
    assert pending["reason"] == "awaiting_confirmation"
    assert pending["session"]["closes_at"] == "2026-11-27T18:00:00+00:00"
    assert pending["session"]["confirm_after"] == "2026-11-27T18:15:00+00:00"
    confirmed_at = waiting + timedelta(minutes=6)
    result = subject.build("SOXX", _source(rows, checked=confirmed_at), confirmed_at + timedelta(days=1))
    assert result["status"] == "confirmed" and result["session"]["trade_date"] == friday.isoformat()
    assert result["volume"] is None
    cme = subject.build("NQ=F", _source(rows, ticker="NQ=F", checked=confirmed_at), confirmed_at)
    assert cme["reason"] == "session_unverified" and cme["reaction"] is None


def test_cme_open_after_maintenance_targets_next_trade_day(swing_only):
    rows = [*_history(), _bar(DAY, 102, 114, 101, 112)]
    waiting = datetime(2026, 11, 24, 22, 5, tzinfo=UTC)
    delayed = subject.build("NQ=F", _source(rows, ticker="NQ=F", checked=waiting), waiting)
    assert delayed["reason"] == "awaiting_confirmation"
    assert delayed["session"]["confirm_after"] == "2026-11-24T22:10:00+00:00"
    confirmed_at = waiting + timedelta(minutes=7)
    confirmed = subject.build("NQ=F", _source(rows, ticker="NQ=F", checked=confirmed_at), confirmed_at)
    assert confirmed["status"] == "confirmed"
    assert confirmed["instrument"] == {"kind": "continuous_future", "unit": "index_points"}
    assert confirmed["session"]["opens_at"] == "2026-11-23T23:00:00+00:00"
    assert confirmed["volume"] is None
    reopened_at = datetime(2026, 11, 24, 23, 1, tzinfo=UTC)
    reopened = subject.build("NQ=F", _source(rows, ticker="NQ=F", checked=reopened_at), reopened_at)
    assert reopened["status"] == "in_progress" and reopened["session"]["trade_date"] == "2026-11-25"
    assert reopened["bar"] is None and reopened["reaction"] is None
    next_bar = _bar(DAY + timedelta(days=1))
    labelled = subject.build("NQ=F", _source([*rows, next_bar], ticker="NQ=F", checked=reopened_at), reopened_at)
    assert labelled["status"] == "in_progress" and labelled["reaction"] is None
    assert labelled["bar"]["at"] == next_bar["at"]


def test_cache_phase_changes_at_daily_boundaries():
    assert subject.cache_phase("SOXX", AFTER.replace(hour=14)) == (calendar.previous_open("us", DAY), "closed")
    assert subject.cache_phase("SOXX", AFTER.replace(hour=15)) == (DAY, "open")
    assert subject.cache_phase("SOXX", AFTER.replace(minute=5)) == (DAY, "awaiting")
    assert subject.cache_phase("SOXX", AFTER) == (DAY, "closed")
    assert subject.cache_phase("NQ=F", datetime(2026, 11, 27, 20, tzinfo=UTC)) == (date(2026, 11, 27), "unverified")
    assert subject.cache_phase("SOXLUSDT", AFTER) is None


def test_missing_or_mismatched_evidence_never_reuses_older_bar(swing_only):
    history = _history()
    rows = [*history, _bar(DAY)]
    assert subject.build("SOXX", None, AFTER)["reason"] == "missing_daily_source"
    assert subject.build("SOXX", _source(history), AFTER)["reason"] == "bar_missing"
    no_prior = subject.build("SOXX", _source([*history[:-1], rows[-1]]), AFTER)
    assert no_prior["reason"] == "previous_bar_missing" and no_prior["reaction"] is None
    for source in (_source(rows, ticker="SOXL"), {**_source(rows), "interval": "1w"}):
        mismatch = subject.build("SOXX", source, AFTER)
        assert mismatch["reason"] == "product_mismatch" and mismatch["bar"] is None
    bad_source = subject.build("SOXX", {**_source(rows), "source": "binance"}, AFTER)
    assert bad_source["reason"] == "source_unverified"
    bad_clock = subject.build("SOXX", {**_source(rows), "fetched_at": "2026-11-24T21:20"}, AFTER)
    assert bad_clock["reason"] == "confirmation_unverified"


@pytest.mark.parametrize("ticker", ["SOXLUSDT", "SOXLUSDT.P", "NAS100", "NAS100.CFD"])
def test_derivative_references_are_explicitly_unsupported(ticker):
    assert subject.eligible(ticker)
    result = subject.build(ticker, _source([*_history(), _bar(DAY)]), AFTER)
    assert result["reason"] == "product_unsupported"
    assert result["bar"] is None and result["instrument"] is None
    assert not subject.eligible("AAPL")


@pytest.mark.parametrize("bad", [
    {"high": 90}, {"low": 100}, {"open": False}, {"close": math.inf}, {"close": math.nan},
    {"at": "2026-11-24"}, {"at": "2026-11-24T23:59:00+00:00"},
])
def test_invalid_bars_are_not_evidence(bad):
    target = {**_bar(DAY), **bad}
    assert subject.build("SOXX", _source([*_history(), target]), AFTER)["reason"] == "invalid_bar"


def test_duplicate_day_and_non_comparable_volume(swing_only):
    history = _history()
    target = _bar(DAY, 105, 105, 105, 105, volume=0)
    assert subject.build("SOXX", _source([*history, target, target]), AFTER)["reason"] == "invalid_bar"
    output = subject.build("SOXX", _source([*history, target]), AFTER)
    assert output["status"] == "confirmed"
    assert output["volume"] is None
    assert output["metrics"]["body_ratio"] is None and output["metrics"]["close_position"] is None
    cash = subject.build("^NDX", _source([*history, _bar(DAY)], ticker="^NDX"), AFTER)
    assert cash["instrument"] == {"kind": "cash_index", "unit": "index_points"}
    assert cash["volume"] is None
