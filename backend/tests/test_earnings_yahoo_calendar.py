"""#338: Nasdaq refuses the server (403) - Yahoo's earnings calendar fills the US days instead, the
page says what stands in, and Nasdaq is not asked again for a while."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd

from desk import earnings

FIRST, LAST = date(2026, 11, 1), date(2026, 12, 31)


def _records():
    ts = lambda s: pd.Timestamp(s, tz="UTC")
    return [
        {"Symbol": "WMT", "Company": "Walmart Inc.", "Marketcap": 1.0e12, "Event Start Date": ts("2026-11-19 13:30"),
         "Timing": "BMO", "EPS Estimate": 0.63},
        # after the close in New York is past midnight UTC: still New York's 11-19
        {"Symbol": "NVDA", "Company": "NVIDIA Corporation", "Marketcap": 4.5e12,
         "Event Start Date": ts("2026-11-20 02:00"), "Timing": "AMC", "EPS Estimate": 1.25},
        {"Symbol": "NVDA", "Company": "dup", "Marketcap": 1.0, "Event Start Date": ts("2026-11-25 13:00"), "Timing": "BMO"},
        {"Symbol": "XYZ", "Company": "Weekend Co", "Marketcap": 2e10, "Event Start Date": ts("2026-11-21 15:00"),
         "Timing": "TNS"},
        {"Symbol": "OLD", "Company": "Too early", "Marketcap": 2e11, "Event Start Date": ts("2026-10-20 13:00")},
        {"Symbol": "bad sym!", "Company": "x", "Event Start Date": ts("2026-11-19 13:00")},
        {"Symbol": "NOD", "Company": "No date", "Event Start Date": None},
        {"Symbol": "TNS", "Company": "  ", "Marketcap": 1.5e11, "Event Start Date": ts("2026-11-23 15:00"), "Timing": "TNS"},
        # the same company's OTC line (foreign ordinary / ADR): left out
        {"Symbol": "BABAF", "Company": "Alibaba", "Marketcap": 2.3e11, "Event Start Date": ts("2026-11-19 13:30")},
        {"Symbol": "SMFNF", "Company": "SMFG", "Marketcap": 1.2e11, "Event Start Date": ts("2026-11-19 06:00")},
    ]


def test_yahoo_calendar_rows_take_new_yorks_day_and_timing():
    got = earnings.parse_yahoo_calendar(_records(), FIRST, LAST)
    assert sorted(got) == ["2026-11-19", "2026-11-23"]
    day = {r["ticker"]: r for r in got["2026-11-19"]}
    assert day["WMT"]["when"] == "pre" and day["WMT"]["eps_estimate"] == 0.63 and day["WMT"]["source"] == "yahoo"
    assert day["NVDA"]["when"] == "after" and day["NVDA"]["name"] == "NVIDIA Corporation"
    assert day["NVDA"]["market_cap"] == 4.5e12 and day["NVDA"]["revenue_estimate"] is None     # every field present
    tns = got["2026-11-23"][0]
    assert tns["ticker"] == "TNS" and tns["when"] == "unknown" and tns["name"] is None


def _client(calls: list):
    def handler(request):
        calls.append(request.url.params.get("date"))
        return httpx.Response(403, text="Access Denied")
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_a_403_is_covered_by_yahoos_calendar_and_nasdaq_rests(tmp_path):
    asked: list = []

    def alt(first, last):
        asked.append((first, last))
        return earnings.parse_yahoo_calendar(_records(), first, last)

    e = earnings.Earnings(yahoo=lambda t: None, cache_file=tmp_path / "e.json", yahoo_calendar=alt)
    e.us = {"2026-11-19": [earnings.blank(ticker="OLDROW", date="2026-11-19", market="us", source="nasdaq",
                                          market_cap=5e11)]}
    calls: list = []
    asyncio.run(e.refresh(_client(calls), today=date(2026, 11, 13), gap=0, waits=()))
    assert len(calls) == 1 and asked == [(FIRST, LAST)] and e.refused and e.refused_at
    shown = e.for_days(date(2026, 11, 1), date(2026, 11, 30), set())
    assert [r["ticker"] for r in shown["2026-11-19"]] == ["NVDA", "WMT"]             # the stand-in, by market cap
    assert all(r["source"] == "yahoo" and not r["stale"] for r in shown["2026-11-19"])
    status = e.source_status()
    assert not status["stale"] and status["error"].startswith("Nasdaq 차단(403) — Yahoo 실적 캘린더로 대신 표시 중")
    assert e.next_delay(21600) == 21600                       # covered: not back in half an hour
    # the next pass within REFUSED_SKIP_SEC does not knock on Nasdaq again
    calls.clear()
    asyncio.run(e.refresh(_client(calls), today=date(2026, 11, 13), gap=0, waits=()))
    assert calls == [] and len(asked) == 2 and not e.source_status()["stale"]
    # the refusal survives a restart; once it is old, Nasdaq is asked again
    restarted = earnings.Earnings(cache_file=tmp_path / "e.json", yahoo_calendar=alt)
    assert restarted.load() and restarted.refused_at == e.refused_at
    restarted.refused_at = (datetime.now(UTC) - timedelta(seconds=earnings.REFUSED_SKIP_SEC + 60)).isoformat()
    asyncio.run(restarted.refresh(_client(calls), today=date(2026, 11, 13), gap=0, waits=()))
    assert len(calls) == 1


def test_when_yahoos_calendar_fails_too_the_last_rows_stay_stale_and_the_page_says_both(tmp_path):
    def broken(first, last):
        raise TimeoutError

    e = earnings.Earnings(yahoo=lambda t: None, cache_file=tmp_path / "e.json", yahoo_calendar=broken)
    e.us = {"2026-11-19": [earnings.blank(ticker="WMT", date="2026-11-19", market="us", source="nasdaq", market_cap=1e12)]}
    e.us_at = {"2026-11-19": "2026-11-12T00:00:00+00:00"}
    asyncio.run(e.refresh(_client([]), today=date(2026, 11, 13), gap=0, waits=()))
    status = e.source_status()
    assert status["stale"] and "Yahoo 실적 캘린더도 실패" in status["error"] and "마지막 자료(" in status["error"]
    row = e.for_days(date(2026, 11, 1), date(2026, 11, 30), set())["2026-11-19"][0]
    assert row["ticker"] == "WMT" and row["stale"]
    assert e.next_delay(21600) == 1800
