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
    for ticker in ("KRW=X", "^N225"):                               # FX, another market's index: read as it is
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


def test_cme_futures_are_read_on_new_yorks_regular_hours_within_their_day():
    """#559 NQ=F's bar dated Monday trades from Sunday 18:00 ET to Monday 17:00 ET, but most of its
    volume trades 09:30-16:00: before the open it is not judged (0.0), then the regular session's clock."""
    mon = [{"at": "2026-11-16T05:00:00+00:00"}]                     # Yahoo: midnight New York (EST)
    assert session_progress.elapsed("NQ=F", mon, datetime(2026, 11, 15, 23, 30, tzinfo=UTC)) == 0.0   # Sun 18:30
    assert session_progress.elapsed("NQ=F", mon, datetime(2026, 11, 16, 13, 10, tzinfo=UTC)) == 0.0   # Mon 08:10
    assert session_progress.elapsed("NQ=F", mon, datetime(2026, 11, 16, 17, 45, tzinfo=UTC)) == pytest.approx(0.5)
    assert session_progress.elapsed("NQ=F", mon, datetime(2026, 11, 16, 21, 30, tzinfo=UTC)) == 1.0   # 16:30
    # after 17:00 ET Monday's bar is finished; Tuesday's (if the bar is there) has not reached its open
    assert session_progress.elapsed("NQ=F", mon, datetime(2026, 11, 16, 22, 30, tzinfo=UTC)) is None
    tue = [{"at": "2026-11-17T05:00:00+00:00"}]
    assert session_progress.elapsed("NQ=F", tue, datetime(2026, 11, 17, 0, 0, tzinfo=UTC)) == 0.0
    # Friday's bar after the 17:00 close and over the weekend: finished; the day after Thanksgiving closes at 13:00
    fri = [{"at": "2026-11-20T05:00:00+00:00"}]
    assert session_progress.elapsed("NQ=F", fri, datetime(2026, 11, 21, 15, tzinfo=UTC)) is None
    early = [{"at": "2026-11-27T05:00:00+00:00"}]
    assert session_progress.elapsed("NQ=F", early, datetime(2026, 11, 27, 16, 15, tzinfo=UTC)) == pytest.approx(0.5)


def test_crypto_runs_on_the_utc_day_and_indices_on_their_market():
    btc = [{"at": "2026-11-16T00:00:00+00:00"}]
    assert session_progress.elapsed("BTC-USD", btc, datetime(2026, 11, 16, 6, tzinfo=UTC)) == pytest.approx(0.25)
    assert session_progress.elapsed("BTC-USD", btc, datetime(2026, 11, 17, 1, tzinfo=UTC)) is None
    # ^GSPC on the New York session, ^KS11 on Seoul's (its bar opens at local midnight like a stock's)
    assert session_progress.elapsed("^GSPC", [{"at": "2026-11-16T05:00:00+00:00"}],
                                    datetime(2026, 11, 16, 17, 45, tzinfo=UTC)) == pytest.approx(0.5)
    kospi = [{"at": "2026-11-15T15:00:00+00:00"}]
    assert session_progress.elapsed("^KS11", kospi, datetime(2026, 11, 16, 0, 30, tzinfo=UTC)) > 0
