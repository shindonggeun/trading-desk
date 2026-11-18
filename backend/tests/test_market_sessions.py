"""#625 the markets' clocks in one place (desk.market_calendar): a table of boundaries per rule, and the old
call sites giving the same answers through it."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from desk import day_call as dc
from desk import estimate, gap_fill, krx, market_calendar, nq_watch, outcome, session_progress, sessions, sr
from desk.broker import kis_stream
from desk.market_calendar import ET, KST

mc = market_calendar


def _et(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ET)


def _kst(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=KST)


@pytest.mark.parametrize(("market", "at", "day"), [
    ("kr", datetime(2026, 11, 17, 14, 59, tzinfo=UTC), date(2026, 11, 17)),     # 23:59 KST
    ("kr", datetime(2026, 11, 17, 15, 0, tzinfo=UTC), date(2026, 11, 18)),      # 00:00 KST
    ("us", datetime(2026, 3, 9, 3, 59, tzinfo=UTC), date(2026, 3, 8)),          # 23:59 EDT (DST began 3/8)
    ("us", datetime(2026, 3, 9, 4, 0, tzinfo=UTC), date(2026, 3, 9)),
    ("us", datetime(2026, 11, 2, 4, 59, tzinfo=UTC), date(2026, 11, 1)),        # 23:59 EST (DST ended 11/1)
    ("us", datetime(2026, 11, 2, 5, 0, tzinfo=UTC), date(2026, 11, 2)),
])
def test_local_today(market, at, day):
    assert mc.local_today(market, at) == day


@pytest.mark.parametrize(("market", "day", "before", "after"), [
    ("kr", date(2024, 6, 7), date(2024, 6, 5), date(2024, 6, 10)),      # 현충일 6/6 · the weekend
    ("kr", date(2026, 11, 16), date(2026, 11, 13), date(2026, 11, 17)),  # Monday: back to Friday
    ("us", date(2026, 11, 27), date(2026, 11, 25), date(2026, 11, 30)),  # Thanksgiving 11/26
    ("us", date(2026, 1, 20), date(2026, 1, 16), date(2026, 1, 21)),     # MLK day 1/19
])
def test_previous_and_next_trading_day(market, day, before, after):
    assert mc.previous_open(market, day) == before and mc.next_open(market, day) == after
    assert dc.previous_session(market, day) == before


@pytest.mark.parametrize(("market", "day", "hours"), [
    ("kr", date(2026, 11, 18), (time(9), time(15, 30))),
    ("kr", date(2026, 11, 19), (time(10), time(16, 30))),        # 수능일
    ("kr", date(2024, 11, 14), (time(10), time(16, 30))),        # a past CSAT
    ("kr", date(2026, 1, 2), (time(10), time(15, 30))),          # the year's first session: late open only
    ("kr", date(2027, 1, 4), (time(10), time(15, 30))),
    ("us", date(2026, 11, 25), (time(9, 30), time(16))),
    ("us", date(2026, 11, 27), (time(9, 30), time(13))),         # the day after Thanksgiving
    ("us", date(2026, 12, 24), (time(9, 30), time(13))),
    ("us", date(2027, 11, 26), (time(9, 30), time(13))),
    ("us", date(2026, 7, 3), (time(9, 30), time(16))),           # July 3 2026 is the observed holiday: no half day
])
def test_regular_hours(market, day, hours):
    assert mc.hours(market, day) == hours
    sess = estimate.KRX if market == "kr" else estimate.US
    assert sess.hours(day) == hours                              # the estimator reads the same rules


def test_the_estimator_keeps_its_old_special_days():
    old = {date(2026, 11, 19): (time(10), time(16, 30)), date(2027, 1, 4): (time(10), time(15, 30))}
    assert all(estimate.KRX.hours(d) == h for d, h in old.items())
    for d in (date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)):
        assert estimate.US.hours(d) == (time(9, 30), time(13))
    assert estimate.FUTURES.hours(date(2026, 11, 27)) == (time(18), time(17))


def test_a_us_half_day_still_reads_the_daily_bar_at_16():
    assert mc.session_close("us", date(2026, 11, 27)) == _et(2026, 11, 27, 13)
    assert mc.session_close("us", date(2026, 11, 27), early_close=False) == _et(2026, 11, 27, 16)
    assert dc.session_close("us", date(2026, 11, 27)) == _et(2026, 11, 27, 16)
    assert dc.session_close("kr", date(2026, 11, 19)) == _kst(2026, 11, 19, 16, 30)
    bar = type("B", (), {"at": "2026-11-18T15:00:00+00:00"})()                      # ^KS11 of 11/19
    assert dc.closed_at("^KS11", bar) == _kst(2026, 11, 19, 16, 30)


@pytest.mark.parametrize(("at", "phase", "day"), [
    (_et(2026, 11, 16, 3, 59), "overnight", date(2026, 11, 16)),    # Monday before 04:00
    (_et(2026, 11, 16, 4, 0), "pre", date(2026, 11, 16)),
    (_et(2026, 11, 16, 9, 29), "pre", date(2026, 11, 16)),
    (_et(2026, 11, 16, 9, 30), "regular", date(2026, 11, 16)),
    (_et(2026, 11, 16, 16, 0), "post", date(2026, 11, 16)),
    (_et(2026, 11, 16, 20, 0), "overnight", date(2026, 11, 17)),    # after 20:00: the next day's
    (_et(2026, 11, 20, 20, 0), None, date(2026, 11, 20)),           # Friday night
    (_et(2026, 11, 21, 12, 0), None, date(2026, 11, 21)),           # Saturday
    (_et(2026, 11, 22, 20, 0), "overnight", date(2026, 11, 23)),    # Sunday night
    (datetime(2026, 3, 9, 13, 30, tzinfo=UTC), "regular", date(2026, 3, 9)),   # 09:30 EDT, the first summer day
    (datetime(2026, 3, 6, 13, 30, tzinfo=UTC), "pre", date(2026, 3, 6)),       # 08:30 EST the Friday before
])
def test_us_phases_by_the_new_york_clock(at, phase, day):
    assert mc.us_phase(at) == phase and kis_stream.us_session(at) == phase
    if phase:
        assert mc.us_phase_day(phase, at) == day and sessions.us_trading_day(phase, at) == day


@pytest.mark.parametrize(("at", "start", "trade_day"), [
    (_et(2026, 11, 16, 17, 59), _et(2026, 11, 15, 18), date(2026, 11, 17)),   # after 17:00: tomorrow's day
    (_et(2026, 11, 16, 16, 59), _et(2026, 11, 15, 18), date(2026, 11, 16)),
    (_et(2026, 11, 16, 18, 0), _et(2026, 11, 16, 18), date(2026, 11, 17)),    # the 18:00 rollover
    (_et(2026, 3, 8, 18, 0), _et(2026, 3, 8, 18), date(2026, 3, 9)),          # Sunday evening, DST's first day
    (datetime(2026, 3, 8, 22, 0, tzinfo=UTC), _et(2026, 3, 8, 18), date(2026, 3, 9)),   # 18:00 EDT = 22:00 UTC
    (datetime(2026, 3, 1, 23, 0, tzinfo=UTC), _et(2026, 3, 1, 18), date(2026, 3, 2)),   # 18:00 EST = 23:00 UTC
])
def test_the_cme_day_rolls_at_18_new_york(at, start, trade_day):
    assert mc.cme_session_start(at) == start and nq_watch._session_start(at) == start
    assert mc.cme_trade_day(at) == trade_day
    assert nq_watch.session_of(at + timedelta(minutes=1)) == start.date()


def test_a_futures_bar_dated_the_evening_before_is_the_next_weekdays_session():
    assert mc.next_weekday(date(2026, 11, 20)) == date(2026, 11, 23)          # Friday -> Monday
    assert mc.next_weekday(date(2026, 11, 25)) == date(2026, 11, 26)          # Thanksgiving still trades on CME
    assert gap_fill._session_day(date(2026, 11, 20), date(2026, 11, 20)) == date(2026, 11, 23)
    assert gap_fill._session_day(date(2026, 11, 23), date(2026, 11, 20)) == date(2026, 11, 23)


@pytest.mark.parametrize(("at", "korean", "naive_is_utc", "day"), [
    ("2026-11-16T15:00:00+00:00", True, False, date(2026, 11, 17)),     # a Korean bar: local midnight
    ("2026-11-17T05:00:00+00:00", False, False, date(2026, 11, 17)),    # a US bar: midnight New York
    ("2026-11-17", True, False, date(2026, 11, 17)),                    # a bare date is read as is
    ("2026-11-16T15:00:00", True, True, date(2026, 11, 17)),            # sr's naive stamps are UTC
    ("2026-11-17T00:00:00-05:00", False, False, date(2026, 11, 17)),
])
def test_a_daily_bars_own_date(at, korean, naive_is_utc, day):
    assert mc.bar_day(at, korean, naive_is_utc) == day


def test_the_old_call_sites_agree():
    assert sr._local_day("2026-11-16T15:00:00+00:00", "005930.KS") == date(2026, 11, 17)
    assert sr._local_day("2026-11-17T05:00:00+00:00", "AAPL") == date(2026, 11, 17)
    assert session_progress.bar_day("2026-11-16T15:00:00+00:00", "^KS11") == date(2026, 11, 17)
    assert outcome.session_open("005930.KS") == timedelta(hours=9)
    assert outcome.session_open("AAPL") == timedelta(hours=9, minutes=30)
    assert krx.kst_today(datetime(2026, 11, 17, 15, 0, tzinfo=UTC)) == date(2026, 11, 18)
    assert krx.ready_at(date(2026, 11, 13)) == _kst(2026, 11, 16, 8, 30)   # Friday's rows on Monday
    assert dc.kr_late_open(date(2026, 11, 19)) and not dc.kr_late_open(date(2026, 11, 18))
    assert dc.CSAT_DAYS is mc.CSAT_DAYS


def test_the_kospi200_futures_sessions_follow_krx_holidays_not_nyse():
    from desk import futures_quote as fq

    thanksgiving = datetime(2026, 11, 26, 10, 0, tzinfo=KST)            # NYSE closed, KRX open
    assert fq.futures_session(thanksgiving)["now"] == "day"
    chuseok = datetime(2026, 9, 25, 10, 0, tzinfo=KST)                  # KRX closed (추석), NYSE open
    assert fq.futures_session(chuseok)["now"] == "closed"


@pytest.mark.parametrize(("at", "phase"), [(_et(2026, 11, 16, 19, 59), "post"), (_et(2026, 11, 16, 20, 0), "overnight")])
def test_the_us_post_session_ends_at_20(at, phase):
    assert mc.us_phase(at) == phase
