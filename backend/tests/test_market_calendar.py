import asyncio
from datetime import UTC, date, datetime, timedelta, timezone

import httpx

from desk import live
from desk.broker.base import MarketDay
from desk.broker.toss import TossBroker

KST = timezone(timedelta(hours=9))


def _kr(day: str, start="09:00", end="15:30") -> dict:
    return {"date": day, "integrated": {"regularMarket": {"startTime": f"{day}T{start}:00+09:00",
                                                          "endTime": f"{day}T{end}:00+09:00"}}}


def _toss(result: dict, requests: list) -> TossBroker:
    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        requests.append(request)
        return httpx.Response(200, json={"result": result})
    return TossBroker("id", "secret", http=httpx.AsyncClient(base_url="https://toss.test", transport=httpx.MockTransport(fake)))


def test_toss_calendar_reads_the_regular_session_and_holidays():
    requests: list = []
    kr = _toss({"previousBusinessDay": _kr("2026-11-04"), "today": {"date": "2026-11-05", "integrated": None},
                "nextBusinessDay": _kr("2026-11-06", "10:00", "16:30")}, requests)
    days = asyncio.run(kr.market_calendar("KR"))
    assert requests[0].url.path == "/api/v1/market-calendar/KR" and "x-account" not in {k.lower() for k in requests[0].headers}
    assert [d.day for d in days] == [date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)]
    assert days[1].opens is None and days[1].closes is None                     # holiday
    assert days[2].opens == datetime(2026, 11, 6, 10, 0, tzinfo=KST)            # delayed open (수능)

    us = _toss({"today": {"date": "2026-11-27", "regularMarket": {"startTime": "2026-11-27T23:30:00+09:00",
                                                                  "endTime": "2026-11-28T03:00:00+09:00"},
                          "preMarket": None, "afterMarket": None, "dayMarket": None},
                "nextBusinessDay": {"date": "bad"}}, [])
    (day,) = asyncio.run(us.market_calendar("US"))                               # an unreadable day is skipped
    assert day.day == date(2026, 11, 27) and day.closes == datetime(2026, 11, 28, 3, 0, tzinfo=KST)


class Broker:
    def __init__(self, days=None, error=None):
        self.days, self.error, self.calls = days or [], error, 0

    async def market_calendar(self, market):
        self.calls += 1
        if self.error:
            raise self.error
        return self.days


def _day(d: date, start=(9, 0), end=(15, 30)) -> MarketDay:
    return MarketDay(day=d, opens=datetime(d.year, d.month, d.day, *start, tzinfo=KST),
                     closes=datetime(d.year, d.month, d.day, *end, tzinfo=KST))


def _at(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST).astimezone(UTC)


def test_the_calendar_decides_holidays_and_shortened_sessions():
    wed, thu, fri = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
    holiday = Broker([_day(wed), MarketDay(day=thu, opens=None, closes=None), _day(fri)])
    cal = live.Calendar(holiday)
    # a weekday 10:00 the clock calls open, the calendar closed
    assert live.market_open("005930.KS", _at(thu, 10))
    assert asyncio.run(live.is_open(cal, "005930.KS", _at(thu, 10))) is False
    # a delayed open (10:00) on the calendar: 09:30 is still closed
    late = live.Calendar(Broker([_day(thu, (10, 0), (16, 30))]))
    assert asyncio.run(late.is_open("KR", _at(thu, 9, 30))) is False
    assert asyncio.run(late.is_open("KR", _at(thu, 16))) is True
    # a day between listed business days (holiday not listed at all) is closed
    gap = live.Calendar(Broker([_day(wed), _day(fri)]))
    assert asyncio.run(gap.is_open("KR", _at(thu, 10))) is False


def test_without_a_calendar_the_clock_stands_in_and_failures_are_retried_later():
    thu = date(2026, 11, 5)
    failing = Broker(error=ConnectionError("down"))
    now = [0.0]
    cal = live.Calendar(failing, clock=lambda: now[0])
    assert asyncio.run(live.is_open(cal, "005930.KS", _at(thu, 10))) is True          # clock: weekday 10:00
    assert asyncio.run(live.is_open(cal, "005930.KS", _at(thu, 10))) is True and failing.calls == 1
    now[0] = live.CALENDAR_RETRY_SEC + 1
    asyncio.run(cal.is_open("KR", _at(thu, 10)))
    assert failing.calls == 2

    class Mock:                                                                          # broker without a calendar
        async def market_calendar(self, market):
            raise NotImplementedError
    assert asyncio.run(live.is_open(live.Calendar(Mock()), "NVDA", datetime(2026, 11, 5, 15, 0, tzinfo=UTC))) is True
    assert asyncio.run(live.is_open(None, "NVDA", datetime(2026, 11, 7, 15, 0, tzinfo=UTC))) is False  # Saturday


def test_one_call_per_market_and_a_new_day_refetches():
    thu, fri = date(2026, 11, 5), date(2026, 11, 6)
    broker = Broker([_day(thu)])
    cal = live.Calendar(broker)
    for _ in range(5):
        asyncio.run(cal.is_open("KR", _at(thu, 10)))
    assert broker.calls == 1
    broker.days = [_day(thu), _day(fri)]
    assert asyncio.run(cal.is_open("KR", _at(fri, 10))) is True and broker.calls == 2   # date rolled over


def test_crypto_fx_and_futures_do_not_use_the_calendar():
    broker = Broker(error=AssertionError("must not be called"))
    cal = live.Calendar(broker)
    sat = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
    assert asyncio.run(live.is_open(cal, "BTC-USD", sat)) is True
    assert asyncio.run(live.is_open(cal, "KRW=X", sat)) is False and broker.calls == 0
    assert live.calendar_market("^KS11") == "KR" and live.calendar_market("^GSPC") == "US"
    assert live.calendar_market("NQ=F") is None and live.calendar_market("035720.KQ") == "KR"
