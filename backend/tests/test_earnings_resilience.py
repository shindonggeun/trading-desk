"""#308: a Nasdaq failure no longer empties the earnings calendar - retries by status, the last rows
kept (marked stale) and saved across a restart, Yahoo standing in for the big names, and detail fields."""

import asyncio
from datetime import date

import httpx

from desk import earnings
from desk import market_calendar as mc

DAY = {"data": {"rows": [
    {"symbol": "NVDA", "name": "NVIDIA", "time": "time-after-hours", "epsForecast": "$2.47", "lastYearEPS": "$1.30",
     "noOfEsts": "38", "fiscalQuarterEnding": "Oct/2026", "marketCap": "$4,500,000,000,000"}]}}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _yahoo(rows):
    return lambda t: rows.get(t)


def _run(e, handler, today=date(2026, 11, 9)):
    return asyncio.run(e.refresh(_client(handler), today=today, gap=0, waits=()))


def test_a_403_is_not_retried_stops_nasdaq_and_keeps_the_last_rows_marked_stale(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.params["date"])
        return httpx.Response(403, text="Access Denied\n<html>")

    e = earnings.Earnings(yahoo=_yahoo({}), cache_file=tmp_path / "e.json")
    e.us = {"2026-11-18": earnings.parse_nasdaq(DAY, date(2026, 11, 18))}
    e.us_at = {"2026-11-18": "2026-11-08T00:00:00+00:00"}
    e.refreshed_at = "2026-11-08T00:00:00+00:00"
    _run(e, handler)
    assert calls == ["2026-11-02"]                                   # one call, then Nasdaq is left alone
    assert e.refused and e.failed_sources == ["nasdaq"] and "nasdaq 2026-11-02: 403" in e.error
    status = e.source_status()
    assert status["stale"] and status["error"].startswith("Nasdaq 응답 거부(403) — 마지막 자료(")
    row = e.for_days(date(2026, 11, 1), date(2026, 11, 30), set())["2026-11-18"][0]
    assert row["ticker"] == "NVDA" and row["stale"] and row["refreshed_at"] == "2026-11-08T00:00:00+00:00"
    assert e.next_delay(21600) == 1800                              # back in half an hour, not six hours


def test_429_and_5xx_are_retried_and_a_timeout_records_its_kind(tmp_path):
    seen: dict[str, int] = {}

    def handler(request):
        day = request.url.params["date"]
        seen[day] = seen.get(day, 0) + 1
        if day == "2026-11-02" and seen[day] == 1:
            return httpx.Response(429, text="slow down")
        if day == "2026-11-03" and seen[day] < 3:
            return httpx.Response(503)
        if day == "2026-11-04":
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=DAY if day == "2026-11-18" else {"data": {"rows": []}})

    e = earnings.Earnings(yahoo=_yahoo({}), cache_file=tmp_path / "e.json")
    _run(e, handler)
    assert seen["2026-11-02"] == 2 and seen["2026-11-03"] == 3 and seen["2026-11-04"] == 1 + earnings.NASDAQ_RETRIES
    assert "2026-11-02" in e.us and "2026-11-03" in e.us and "2026-11-04" not in e.us
    assert "nasdaq 2026-11-04: timeout" in e.error and not e.refused
    assert e.source_status()["error"].startswith("Nasdaq 일부 날짜 실패(timeout)")
    row = e.for_days(date(2026, 11, 1), date(2026, 11, 30), set())["2026-11-18"][0]
    assert (row["eps_year_ago"], row["analysts"], row["fiscal_quarter"], row["currency"]) == (1.3, 38, "Oct/2026", "USD")
    assert row["revenue_estimate"] is None and row["stale"] is False


def test_yahoo_stands_in_for_the_big_names_when_nasdaq_fails(tmp_path):
    aapl = earnings.blank(ticker="AAPL", date="2026-11-20", market="us", source="yahoo", eps_estimate=1.9,
                          revenue_estimate=1.2e11, revenue_low=1.1e11, revenue_high=1.3e11, currency="USD")
    e = earnings.Earnings(yahoo=_yahoo({"AAPL": aapl}), cache_file=tmp_path / "e.json")
    _run(e, lambda request: httpx.Response(403))
    shown = e.for_days(date(2026, 11, 1), date(2026, 11, 30), set())["2026-11-20"]
    assert [(r["ticker"], r["source"], r["revenue_estimate"]) for r in shown] == [("AAPL", "yahoo", 1.2e11)]
    # Nasdaq back: its rows are the calendar again, Yahoo's consensus fills what Nasdaq lacks
    e2 = earnings.Earnings(yahoo=_yahoo({"NVDA": earnings.blank(ticker="NVDA", date="2026-11-18", market="us",
                                                                 source="yahoo", revenue_estimate=5.4e10)}),
                           cache_file=tmp_path / "e2.json")
    _run(e2, lambda request: httpx.Response(200, json=DAY if request.url.params["date"] == "2026-11-18"
                                            else {"data": {"rows": []}}))
    nvda = e2.for_days(date(2026, 11, 1), date(2026, 11, 30), set())["2026-11-18"][0]
    assert nvda["source"] == "nasdaq" and nvda["revenue_estimate"] == 5.4e10 and not e2.fallback
    assert e2.next_delay(21600) == 21600 and e2.source_status() == {
        "refreshed_at": e2.refreshed_at, "error": None, "stale": False, "failed_sources": []}


def test_the_rows_survive_a_restart(tmp_path):
    path = tmp_path / "earnings.json"
    e = earnings.Earnings(yahoo=_yahoo({}), cache_file=path)
    _run(e, lambda request: httpx.Response(200, json=DAY if request.url.params["date"] == "2026-11-18"
                                           else {"data": {"rows": []}}))
    restarted = earnings.Earnings(cache_file=path)
    assert restarted.load() and restarted.refreshed_at == e.refreshed_at
    assert [r["ticker"] for r in restarted.for_days(date(2026, 11, 1), date(2026, 11, 30), set())["2026-11-18"]] == ["NVDA"]
    assert earnings.Earnings(cache_file=tmp_path / "missing.json").load() is False
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert earnings.Earnings(cache_file=tmp_path / "broken.json").load() is False


def test_a_korean_row_takes_its_listed_name_and_unknown_details_are_null(tmp_path):
    kr = {"ticker": "035420.KS", "name": None, "date": "2026-11-06", "market": "kr", "when": "unknown",
          "eps_estimate": 2900.0, "market_cap": None, "source": "yahoo"}        # a row saved before #308
    e = earnings.Earnings(name_of=lambda t: {"035420.KS": "NAVER"}.get(t), cache_file=tmp_path / "e.json")
    e.kr = {"035420.KS": kr}
    v = mc.month_view("2026-11", set(), earn=e)
    row = next(r for d in v["days"] for r in d["earnings"] if r["ticker"] == "035420.KS")
    assert row["name"] == "NAVER" and row["eps_estimate"] == 2900.0
    assert all(row[k] is None for k in ("eps_low", "eps_high", "revenue_estimate", "analysts", "fiscal_quarter"))
    assert v["sources"]["earnings"]["failed_sources"] == []


def test_failed_passes_back_off_and_a_good_pass_resets():
    e = earnings.Earnings()
    for n, want in ((1, 1800), (2, 3600), (3, 7200), (4, 14400), (9, 14400)):
        e.failed_passes = n
        assert e.next_delay(21600) == want
    e.failed_passes = 0
    assert e.next_delay(21600) == 21600


def test_watched_us_tickers_are_kept_apart_from_korean_ones():
    e = earnings.Earnings()
    e.want({"NVDA", "035420.KS", "^KS11", "BTC-USD", "NQ=F"})
    assert "NVDA" in e.want_us and "035420.KS" in e.want_kr and "NVDA" not in e.want_kr
    assert not {"^KS11", "BTC-USD", "NQ=F"} & (e.want_us | e.want_kr)
