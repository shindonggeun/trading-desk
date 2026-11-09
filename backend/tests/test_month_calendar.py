"""#243: a month of KRX / NYSE closed days, releases, expiries and earnings in Korean time."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import httpx

from desk import earnings, events_calendar
from desk import market_calendar as mc


def _day(view, iso):
    return next(d for d in view["days"] if d["date"] == iso)


def _calendar(*events):
    cal = events_calendar.Calendar()
    cal.by_source = {"bls": list(events)}
    return cal


def test_november_2026_thanksgiving_early_close_and_expiries():
    v = mc.month_view("2026-11", calendar=_calendar(), earn=earnings.Earnings())
    assert len(v["days"]) == 30 and _day(v, "2026-11-02")["weekday"] == "월"
    thanks = _day(v, "2026-11-26")
    assert thanks["us"] == {"open": False, "holiday": "추수감사절", "early_close": None, "close_kst": None}
    assert thanks["kr"]["open"] is True
    friday = _day(v, "2026-11-27")["us"]
    assert friday["early_close"] == "추수감사절 다음 날" and friday["close_kst"] == "03:00"   # 13:00 ET, winter time
    assert _day(v, "2026-11-25")["us"]["close_kst"] == "06:00"
    (us_exp,) = _day(v, "2026-11-20")["events"]
    assert us_exp["id"] == "us_expiry" and us_exp["time_kst"] == "06:00" and us_exp["next_day_kst"] is True
    (kr_exp,) = _day(v, "2026-11-12")["events"]
    assert kr_exp["label"] == "코스피200 옵션 만기" and kr_exp["time_kst"] == "15:20" and kr_exp["importance"] == 1
    assert not _day(v, "2026-11-07")["kr"]["open"] and _day(v, "2026-11-07")["kr"]["holiday"] is None   # a Saturday


def test_us_releases_follow_summer_time_in_korean_time():
    before = events_calendar.Event("cpi", "CPI", datetime(2026, 3, 6, 13, 30, tzinfo=UTC), 2, "bls")   # 08:30 EST
    after = events_calendar.Event("cpi", "CPI", datetime(2026, 3, 13, 12, 30, tzinfo=UTC), 2, "bls")   # 08:30 EDT
    fomc = events_calendar.Event("fomc", "FOMC", datetime(2026, 3, 18, 18, 0, tzinfo=UTC), 2, "fed")   # 14:00 EDT
    v = mc.month_view("2026-03", calendar=_calendar(before, after, fomc), earn=earnings.Earnings())
    assert _day(v, "2026-03-06")["events"][0]["time_kst"] == "22:30"
    assert _day(v, "2026-03-13")["events"][0]["time_kst"] == "21:30"
    f = _day(v, "2026-03-18")["events"][0]
    assert f["time_kst"] == "03:00" and f["next_day_kst"] and f["time_local"] == "14:00 ET"
    # March is a quarter month: both expiries are futures and options together
    assert _day(v, "2026-03-20")["events"][0]["label"].startswith("미국 선물 · 옵션 동시만기")
    assert _day(v, "2026-03-12")["events"][0]["importance"] == 2


def test_korean_holidays_in_korean_and_an_expiry_on_a_holiday_moves_back():
    assert mc.holiday("kr", date(2026, 9, 25)) == "추석"
    assert mc.holiday("kr", date(2026, 12, 31)) is not None                         # the year-end closing
    assert mc.holiday("us", date(2026, 7, 3)).startswith("독립기념일")                # observed: July 4 a Saturday
    assert mc.us_early_close(date(2026, 7, 2)) is None and mc.us_early_close(date(2026, 12, 24)) == "크리스마스이브"
    # October 2025: the second Thursday (9th, Hangul Day) and the days before it were closed
    kr = next(e for e in mc.expiries(2025, 10) if e["market"] == "kr")
    assert kr["date"] == "2025-10-02"


def test_the_bank_of_korea_dates_come_from_the_data_file():
    v = mc.month_view("2026-10", calendar=_calendar(), earn=earnings.Earnings())
    bok = [e for e in _day(v, "2026-10-22")["events"] if e["id"] == "bok"]
    assert bok and bok[0]["time_kst"] == "10:00" and bok[0]["importance"] == 2


NASDAQ = {"data": {"rows": [
    {"symbol": "NVDA", "name": "NVIDIA", "time": "time-after-hours", "epsForecast": "$2.47",
     "marketCap": "$4,500,000,000,000"},
    {"symbol": "TINY", "name": "Tiny Co", "time": "time-pre-market", "epsForecast": "($0.10)", "marketCap": "$900,000,000"},
    {"symbol": "WMT", "name": "Walmart", "time": "time-not-supplied", "epsForecast": "",
     "marketCap": "$1,013,298,100,000"},
    {"symbol": "bad sym!", "name": "x", "time": "", "epsForecast": "", "marketCap": ""}]}}


def test_nasdaq_rows_parse_and_the_view_keeps_big_or_watched_names():
    rows = earnings.parse_nasdaq(NASDAQ, date(2026, 11, 18))
    assert [r["ticker"] for r in rows] == ["NVDA", "TINY", "WMT"]
    assert rows[0]["when"] == "after" and rows[0]["eps_estimate"] == 2.47 and rows[1]["eps_estimate"] == -0.10
    assert rows[2]["when"] == "unknown" and rows[2]["eps_estimate"] is None
    e = earnings.Earnings()
    e.us = {"2026-11-18": rows}
    shown = e.for_days(date(2026, 11, 1), date(2026, 11, 30), {"TINY"})["2026-11-18"]
    assert [r["ticker"] for r in shown] == ["TINY", "NVDA", "WMT"] and shown[0]["watched"]
    assert [r["ticker"] for r in e.for_days(date(2026, 11, 1), date(2026, 11, 30), set())["2026-11-18"]] == ["NVDA", "WMT"]


def test_refresh_reads_each_weekday_and_the_korean_tickers_and_keeps_old_rows_on_failure():
    asked = []

    def handler(request):
        day = request.url.params["date"]
        asked.append(day)
        if day == "2026-11-18":
            return httpx.Response(200, json=NASDAQ)
        if day == "2026-11-19":
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"rows": []}})

    e = earnings.Earnings(yahoo=lambda t: {"ticker": t, "name": None, "date": "2026-11-27", "market": "kr",
                                           "when": "unknown", "eps_estimate": 100.0, "market_cap": None,
                                           "source": "yahoo"} if t == "005930.KS" else None)
    e.us["2026-11-19"] = [{"ticker": "OLD", "market_cap": 2e11}]
    e.want({"005930.KS", "NVDA"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = asyncio.run(e.refresh(client, today=date(2026, 11, 9), gap=0))
    assert asked[0] == "2026-11-02" and asked[-1] == "2026-12-31" and "2026-11-07" not in asked
    assert ok > 40 and e.us["2026-11-19"] == [{"ticker": "OLD", "market_cap": 2e11}] and "nasdaq 2026-11-19" in e.error
    assert e.kr["005930.KS"]["date"] == "2026-11-27" and "NVDA" not in e.want_kr
    v = mc.month_view("2026-11", {"005930.KS"}, calendar=_calendar(), earn=e)
    assert [r["ticker"] for r in _day(v, "2026-11-18")["earnings"]] == ["NVDA", "WMT"]
    assert _day(v, "2026-11-27")["earnings"][0]["watched"] is True


def test_the_api_needs_a_session_validates_the_month_and_watches_favorites(db_url):
    from tests.test_api import OWNER, PASSWORD, _client

    earnings.CACHE.want_kr.discard("000660.KS")
    with _client(db_url, login=False) as client:
        assert client.get("/api/calendar").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.post("/api/favorites", json={"ticker": "000660.KS"}).status_code in (200, 201)
        assert client.get("/api/calendar", params={"month": "2026-13"}).status_code == 422
        assert client.get("/api/calendar", params={"month": "1999-01"}).status_code == 422
        body = client.get("/api/calendar").json()
        assert len(body["days"]) >= 28 and {"kr", "us", "events", "earnings"} <= set(body["days"][0])
    assert "000660.KS" in earnings.CACHE.want_kr
