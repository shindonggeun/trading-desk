"""Domestic short-sale publication tests with synthetic, already-normalized KRX rows."""

import asyncio
from datetime import date, datetime
from functools import partial

import pytest

from desk import market_calendar, positioning
from desk.futures_flow import KisError
from desk.kr_short_sales import (
    KIS_SHORT_PATH,
    KIS_SHORT_TR,
    BalanceRow,
    KrxShortSales,
    Publication,
    TradeRow,
    UnsupportedTicker,
    connect,
    is_kr_ticker,
    kis_source,
    parse_kis_trades,
)

THURSDAY = date(2026, 10, 8)
WEDNESDAY = date(2026, 10, 7)
TUESDAY = date(2026, 10, 6)
MONDAY = date(2026, 10, 12)


def at(day, hour=21, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=market_calendar.KST)


def test_uses_distinct_dates_and_measures_for_domestic_symbols():
    async def run():
        rows = {
            "005930.KS": (TradeRow(THURSDAY, 2_000, 50_000), BalanceRow(TUESDAY, 0.77)),
            "000660.KS": (TradeRow(THURSDAY, 0, 80_000), BalanceRow(TUESDAY, 0.0)),
            "247540.KQ": (TradeRow(THURSDAY, 6_000, 20_000), BalanceRow(TUESDAY, 2.16)),
        }

        async def source(ticker, start, end):
            assert start < TUESDAY < end == THURSDAY
            trade, balance = rows[ticker]
            return Publication([trade], [balance])

        service = KrxShortSales(source)
        for ticker, trade_ratio, balance_ratio, market in (
            ("005930.KS", 4.0, 0.77, "KOSPI"),
            ("000660.KS", 0.0, 0.0, "KOSPI"),
            ("247540.KQ", 30.0, 2.16, "KOSDAQ"),
        ):
            result = await service.get(ticker, at(THURSDAY))
            assert result["ticker"] == ticker
            assert result["market"] == market
            assert result["status"] == "available"
            assert result["trade"]["latest"] == {
                "as_of": "2026-10-08", "ratio_pct": trade_ratio, "status": "available",
            }
            assert result["balance"]["latest"] == {
                "as_of": "2026-10-06", "ratio_pct": balance_ratio, "status": "available",
            }
            days = [date.fromisoformat(row["as_of"]) for row in result["trade"]["history"]]
            assert len(days) == len(set(days)) == 20
            assert days == sorted(days)
            assert all(market_calendar.is_open("kr", day) for day in days)

    asyncio.run(run())


def test_publication_cutoff_and_closed_days_use_the_completed_session_cache():
    async def run():
        ends = []

        async def source(ticker, start, end):
            ends.append(end)
            return Publication([TradeRow(end, 0, 120)], [])

        service = KrxShortSales(source, clock=lambda: 0.0)
        for day in (THURSDAY, date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11)):
            result = await service.get("005930.KS", at(day))
            assert result["status"] == "available"
            assert result["trade"]["latest"]["as_of"] == "2026-10-08"
        early = await service.get("005930.KS", at(MONDAY, 20, 9))
        assert ends == [THURSDAY]
        assert early["status"] == "not_published"
        assert early["trade"]["history"][-1] == {
            "as_of": "2026-10-12", "ratio_pct": None, "status": "not_published",
        }
        assert early["trade"]["latest"]["as_of"] == "2026-10-08"
        assert not early["trade"]["stale"]
        final = await service.get("005930.KS", at(MONDAY, 20, 10))
        assert ends == [THURSDAY, MONDAY]
        assert final["trade"]["latest"]["as_of"] == "2026-10-12"
        assert final["trade"]["latest"]["ratio_pct"] == 0.0

    asyncio.run(run())


@pytest.mark.parametrize(
    ("trade", "status"),
    [
        (None, "no_data"),
        (TradeRow(THURSDAY, None, 50), "no_data"),
        (TradeRow(THURSDAY, 0, 0), "zero_denominator"),
        (TradeRow(THURSDAY, None, None, published=False), "not_published"),
    ],
)
def test_missing_unpublished_and_zero_denominator_never_render_zero(trade, status):
    async def source(ticker, start, end):
        return Publication([trade] if trade else [], [BalanceRow(WEDNESDAY, None)])

    result = asyncio.run(KrxShortSales(source).get("247540.KQ", at(THURSDAY)))
    assert result["status"] == status
    assert result["trade"]["latest"] is None
    assert result["trade"]["history"][-1]["ratio_pct"] is None
    assert result["balance"]["status"] == "no_data"
    assert result["balance"]["latest"] is None


def test_partial_and_total_source_error_keep_only_last_good_dates():
    async def run():
        time = 0
        responses = iter((
            Publication([TradeRow(THURSDAY, 5, 50)], [BalanceRow(WEDNESDAY, 0.15)]),
            Publication(None, [BalanceRow(THURSDAY, 0.0)]),
            RuntimeError("test fixture failure"),
            Publication([TradeRow(MONDAY, 0, 100)], []),
        ))
        calls = 0

        async def source(ticker, start, end):
            nonlocal calls
            calls += 1
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result

        service = KrxShortSales(source, clock=lambda: time)
        await service.get("005930.KS", at(THURSDAY))
        partial = await service.get("005930.KS", at(MONDAY))
        assert partial["trade"]["status"] == "source_error"
        assert partial["trade"]["stale"]
        assert partial["trade"]["latest"]["as_of"] == "2026-10-08"
        assert partial["balance"]["latest"] == {
            "as_of": "2026-10-08", "ratio_pct": 0.0, "status": "available",
        }
        assert not partial["balance"]["stale"]
        assert await service.get("005930.KS", at(MONDAY)) == partial
        assert calls == 2
        time = 61
        failed = await service.get("005930.KS", at(MONDAY))
        assert failed["balance"]["status"] == "source_error"
        assert failed["balance"]["stale"]
        assert failed["balance"]["latest"] == partial["balance"]["latest"]
        time = 122
        recovered = await service.get("005930.KS", at(MONDAY))
        assert recovered["trade"]["latest"]["as_of"] == "2026-10-12"
        assert not recovered["trade"]["stale"]
        assert recovered["balance"]["status"] == "no_data"

    asyncio.run(run())


def test_no_authorized_source_and_unsupported_symbol_are_separate():
    async def run():
        async def source(ticker, start, end):
            assert ticker == "999999.KQ"
            raise UnsupportedTicker(ticker)

        assert is_kr_ticker(" 247540.kq ")
        assert not is_kr_ticker("AAPL")
        absent = await KrxShortSales().get("005930.KS")
        assert absent["market"] == "KOSPI"
        assert absent["trade"]["status"] == absent["balance"]["status"] == "source_unavailable"
        assert (await KrxShortSales(source).get("AAPL"))["status"] == "unsupported"
        unknown = await KrxShortSales(source).get("999999.KQ")
        assert unknown["market"] == "KOSDAQ"
        assert unknown["status"] == "unsupported"

    asyncio.run(run())


def test_same_ticker_concurrent_requests_share_one_source_call():
    async def run():
        calls = 0

        async def source(ticker, start, end):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return Publication([TradeRow(THURSDAY, 1, 100)], [])

        service = KrxShortSales(source)
        results = await asyncio.gather(*(service.get("000660.KS", at(THURSDAY)) for _ in range(5)))
        assert calls == 1
        assert len(results) == 5
        assert all(result == results[0] for result in results)

    asyncio.run(run())


def test_domestic_positioning_api_requires_session_and_valid_ticker(db_url):
    from tests.test_api import OWNER, PASSWORD, _client

    endpoint = "/api/positioning"
    with _client(db_url, login=False) as client:
        assert client.get(endpoint, params={"ticker": "005930.KS"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        assert client.get(endpoint).status_code == 422
        assert client.get(endpoint, params={"ticker": "bad ticker"}).status_code == 422

        response = client.get(endpoint, params={"ticker": " 247540.kq "})
        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == body["kr_short"]["ticker"] == "247540.KQ"
        assert body["kr_short"]["market"] == "KOSDAQ"
        assert body["supported"] is True
        assert body["reason"] == body["kr_short"]["status"] == "source_unavailable"
        assert body["kr_short"]["source"] is None              # fake runs: no KIS client
        assert body["kr_short"]["balance"]["status"] == "source_unavailable"
        assert body["sources"] == []
        assert body["short"] is body["options"] is None


def test_domestic_positioning_api_uses_injected_publications_for_single_get(db_url, monkeypatch):
    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _client

    calls = []

    async def source(ticker, start, end):
        calls.append(ticker)
        if ticker == "999999.KQ":
            raise UnsupportedTicker(ticker)
        if ticker == "000660.KS":
            return Publication([], [])
        return Publication([TradeRow(THURSDAY, 0, 50_000)], [BalanceRow(TUESDAY, 1.25)])

    service = KrxShortSales(source)
    monkeypatch.setattr(service, "get", partial(service.get, now=at(THURSDAY)))
    monkeypatch.setattr(positioning, "POSITIONING", positioning.Positioning())
    _client(db_url, login=False).close()        # the owner account
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0),
                     short_sales=service)
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        response = client.get("/api/positioning", params={"ticker": "005930.ks"})
        assert response.status_code == 200
        body = response.json()
        assert body["supported"] is True and body["sources"] == ["krx"]
        assert body["reason"] is None
        assert body["kr_short"]["trade"]["latest"] == {
            "as_of": "2026-10-08", "ratio_pct": 0.0, "status": "available",
        }
        assert body["kr_short"]["balance"]["latest"] == {
            "as_of": "2026-10-06", "ratio_pct": 1.25, "status": "available",
        }

        response = client.get("/api/positioning", params={"ticker": "999999.KQ"})
        assert response.status_code == 200
        unknown = response.json()
        assert unknown["supported"] is False
        assert unknown["reason"] == unknown["kr_short"]["status"] == "unsupported"
        assert unknown["sources"] == []

        response = client.get("/api/positioning", params={"ticker": "000660.KS"})
        assert response.status_code == 200
        empty = response.json()
        assert empty["kr_short"]["status"] == "no_data"
        assert empty["sources"] == []

        response = client.get("/api/positioning/batch", params={"tickers": "005930.KS"})
        assert response.status_code == 200
        item, = response.json()["items"]
        assert item["supported"] is False
        assert "kr_short" not in item
    assert calls == ["005930.KS", "999999.KQ", "000660.KS"]


# ---- #979 KIS 국내주식 공매도 일별추이 (FHPST04830000) ----

def kis_row(day, short, total, ratio=None):
    if ratio is None and short and total:
        ratio = short / total * 100
    return {"stck_bsop_date": day.strftime("%Y%m%d"), "ssts_tr_pbmn": str(short), "acml_tr_pbmn": str(total),
            "ssts_tr_pbmn_rlim": f"{ratio or 0:.2f}"}


class FakeKis:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def get(self, path, tr_id, params):
        self.calls.append((path, tr_id, params))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_kis_rows_keep_zero_share_zero_denominator_and_drop_unit_mismatch():
    rows = parse_kis_trades({"output2": [
        kis_row(THURSDAY, 3_180_000_000, 100_000_000_000),            # 3.18%
        kis_row(WEDNESDAY, 0, 50_000_000_000),                        # a real 0%
        kis_row(TUESDAY, 0, 0),                                       # a halted day: zero denominator
        kis_row(date(2026, 10, 5), 3_180, 100_000_000_000, ratio=3.18),   # short value in another unit
        {"stck_bsop_date": "", "ssts_tr_pbmn": "1", "acml_tr_pbmn": "2"},
        {"stck_bsop_date": "20261002", "ssts_tr_pbmn": "-", "acml_tr_pbmn": ""},
    ]})
    by_day = {row.as_of: row for row in rows}
    assert by_day[THURSDAY] == TradeRow(THURSDAY, 3_180_000_000, 100_000_000_000)
    assert by_day[WEDNESDAY] == TradeRow(WEDNESDAY, 0, 50_000_000_000)
    assert by_day[TUESDAY] == TradeRow(TUESDAY, 0, 0)
    assert by_day[date(2026, 10, 5)] == TradeRow(date(2026, 10, 5), None, None)
    assert by_day[date(2026, 10, 2)] == TradeRow(date(2026, 10, 2), None, None)
    assert len(rows) == 5
    with pytest.raises(TypeError):
        parse_kis_trades({"rt_cd": "0"})


def test_kis_rows_without_kis_own_share_fail_closed_and_a_good_duplicate_wins():
    day = date(2026, 10, 5)
    unverified = {"stck_bsop_date": "20261008", "ssts_tr_pbmn": "3180", "acml_tr_pbmn": "100000000000"}
    rows = parse_kis_trades({"output2": [
        unverified,                                                    # no ssts_tr_pbmn_rlim
        {**kis_row(WEDNESDAY, 3_180_000_000, 100_000_000_000), "ssts_tr_pbmn_rlim": "0.00"},
        kis_row(TUESDAY, 1, 100_000_000_000, ratio=0),                 # a tiny share rounds to 0.00
        kis_row(day, 3_180, 100_000_000_000, ratio=3.18),              # dropped first ...
        kis_row(day, 3_180_000_000, 100_000_000_000),                  # ... then a good row for the day
    ]})
    by_day = {row.as_of: row for row in rows}
    assert by_day[THURSDAY] == TradeRow(THURSDAY, None, None)
    assert by_day[WEDNESDAY] == TradeRow(WEDNESDAY, None, None)
    assert by_day[TUESDAY] == TradeRow(TUESDAY, 1, 100_000_000_000)
    assert by_day[day] == TradeRow(day, 3_180_000_000, 100_000_000_000)


def test_connect_uses_kis_when_the_client_exists():
    off = asyncio.run(connect(None).get("005930.KS", now=at(THURSDAY)))
    assert off["status"] == "source_unavailable" and off["source"] is None
    kis = FakeKis({"output2": [kis_row(THURSDAY, 1_000, 100_000)]})
    on = asyncio.run(connect(kis).get("005930.KS", now=at(THURSDAY)))
    assert on["source"] == "kis" and on["status"] == "available"
    assert on["trade"]["latest"] == {"as_of": "2026-10-08", "ratio_pct": 1.0, "status": "available"}
    assert on["balance"]["status"] == "source_unavailable"


def test_kis_source_asks_the_krx_market_for_the_window():
    kis = FakeKis({"output2": [kis_row(THURSDAY, 1, 4)]})
    publication = asyncio.run(kis_source(kis)("005930.KS", date(2026, 9, 7), THURSDAY))
    assert publication == Publication([TradeRow(THURSDAY, 1, 4)], [])
    assert kis.calls == [(KIS_SHORT_PATH, KIS_SHORT_TR, {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930",
        "FID_INPUT_DATE_1": "20260907", "FID_INPUT_DATE_2": "20261008"})]


def test_kis_service_pending_outage_zero_denominator_and_no_balance_source():
    clock = [0.0]
    rows = {"output2": [kis_row(WEDNESDAY, 2_000, 100_000), kis_row(THURSDAY, 0, 0)]}
    kis = FakeKis(rows, rows, KisError("FHPST04830000: HTTP 500 EGW00201"))
    service = KrxShortSales(kis_source(kis), balances=False, name="kis", clock=lambda: clock[0])

    async def run():
        # before 20:10 the day's full session is not published: today's point waits, Wednesday is latest
        early = await service.get("005930.KS", now=at(THURSDAY, 19, 0))
        assert early["source"] == "kis"
        assert early["trade"]["history"][-1] == {"as_of": "2026-10-08", "ratio_pct": None, "status": "not_published"}
        assert early["status"] == "not_published"
        assert early["trade"]["latest"] == {"as_of": "2026-10-07", "ratio_pct": 2.0, "status": "available"}
        assert early["balance"] == {"status": "source_unavailable", "latest": None, "stale": False}
        assert kis.calls[0][2]["FID_INPUT_DATE_2"] == "20261007"

        # after 20:10 a zero denominator never draws as 0%; the last good share stays, marked stale
        late = await service.get("005930.KS", now=at(THURSDAY, 21, 0))
        assert late["status"] == "zero_denominator"
        assert late["trade"]["history"][-1]["ratio_pct"] is None
        assert late["trade"]["latest"]["as_of"] == "2026-10-07" and late["trade"]["stale"] is True
        clock[0] += 61                          # a published zero denominator is an answer: no retry yet
        await service.get("005930.KS", now=at(THURSDAY, 21, 1))
        assert len(kis.calls) == 2

        # a KIS outage keeps the last publication, marked as a source error, and retries soon
        clock[0] += 15 * 60 + 1                 # the published day's cache ends
        down = await service.get("005930.KS", now=at(THURSDAY, 21, 5))
        assert down["status"] == "source_error"
        assert down["trade"]["latest"]["as_of"] == "2026-10-07" and down["trade"]["stale"] is True
        assert down["balance"]["status"] == "source_unavailable"
        assert len(kis.calls) == 3

    asyncio.run(run())
