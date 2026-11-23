"""Domestic short-sale publication tests with synthetic, already-normalized KRX rows."""

import asyncio
from datetime import date, datetime

import pytest

from desk import market_calendar
from desk.kr_short_sales import (
    BalanceRow,
    KrxShortSales,
    Publication,
    TradeRow,
    UnsupportedTicker,
    is_kr_ticker,
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
