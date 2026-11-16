"""#432: how much of today's session a live plan's last daily bar has behind it."""

from datetime import UTC, datetime

import pytest

from desk import playbook, session_progress

PRE = datetime(2026, 11, 16, 12, 2, tzinfo=UTC)          # 21:02 KST = 07:02 New York (EST), pre-market


@pytest.mark.parametrize("at", ["2026-11-16T05:00:00+00:00",   # Yahoo: midnight New York (EST)
                                "2026-11-16T00:00:00+00:00",   # a broker's date-only bar as UTC midnight
                                "2026-11-16"])
def test_a_us_bar_for_today_is_read_on_its_own_date(at):
    assert session_progress.elapsed("SOXL", [{"at": at}], PRE) == 0.0
    mid = session_progress.elapsed("SOXL", [{"at": at}], datetime(2026, 11, 16, 17, 45, tzinfo=UTC))
    assert mid == pytest.approx(0.5)                               # 12:45 New York: half the day
    assert session_progress.elapsed("SOXL", [{"at": at}], datetime(2026, 11, 16, 21, 5, tzinfo=UTC)) is None


def test_finished_sessions_other_markets_and_no_bars_are_none():
    yahoo_edt = [{"at": "2026-07-15T04:00:00+00:00"}]
    assert session_progress.elapsed("SOXL", yahoo_edt, datetime(2026, 7, 16, 12, tzinfo=UTC)) is None   # yesterday's
    assert session_progress.elapsed("SOXL", yahoo_edt, datetime(2026, 7, 15, 12, tzinfo=UTC)) == 0.0   # 08:00 EDT
    for ticker in ("NQ=F", "BTC-USD", "KRW=X", "^GSPC"):            # no one daily session: read as it is
        assert session_progress.elapsed(ticker, [{"at": "2026-11-16T05:00:00+00:00"}],
                                        datetime(2026, 11, 16, 15, tzinfo=UTC)) is None
    assert session_progress.elapsed("SOXL", [], PRE) is None
    assert session_progress.elapsed("SOXL", [{"at": "garbage"}], PRE) is None


def test_korean_bars_open_at_local_midnight_and_special_days_keep_their_hours():
    kr = [{"at": "2026-11-15T15:00:00+00:00"}]                     # 00:00 KST on the 16th
    assert session_progress.elapsed("005930.KS", kr, datetime(2026, 11, 15, 23, 30, tzinfo=UTC)) == 0.0  # 08:30
    assert session_progress.elapsed("005930.KS", kr, datetime(2026, 11, 16, 0, 30, tzinfo=UTC)) > 0       # 09:30
    exam = [{"at": "2026-11-18T15:00:00+00:00"}]                   # the 19th: the exam day opens at 10:00
    assert session_progress.elapsed("005930.KS", exam, datetime(2026, 11, 19, 0, 30, tzinfo=UTC)) == 0.0


def test_a_us_early_close_ends_the_session_at_one():
    friday = [{"at": "2026-11-27T05:00:00+00:00"}]                 # the day after Thanksgiving
    assert session_progress.elapsed("SOXL", friday, datetime(2026, 11, 27, 18, 5, tzinfo=UTC)) is None   # 13:05 ET
    assert session_progress.elapsed("SOXL", friday, datetime(2026, 11, 27, 16, 15, tzinfo=UTC)) == pytest.approx(0.5)


def test_a_tracked_analysis_shows_todays_volume_note_not_the_runs():
    from desk import horizons

    h = horizons.get("swing")
    side = {"setup": "none", "conditions": []}
    run_pb = {"regime": {"flags": {}}, "frame": {"atr": 1.0, "reference_price": 10.0}, "volume_note": "old",
              "long": dict(side), "short": dict(side)}
    fresh = {**run_pb, "volume_note": None}
    bars = [{"close": 10.0, "high": 10.5, "low": 9.5, "open": 10.0, "at": "2026-11-16T05:00:00+00:00"}] * 30
    out = playbook.track(fresh, run_pb, bars, h)
    assert out["volume_note"] is None
