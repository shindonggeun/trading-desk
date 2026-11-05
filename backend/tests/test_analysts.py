import asyncio

import pytest

from desk.broker.base import Bar
from desk.broker.mock import MockBroker
from desk.prices import PriceSource
from desk.related import relation_for
from desk.runner import ANALYSTS, RunRequest


@pytest.mark.parametrize("ticker", ["^KS11", "^kq11", "NQ=F", "GC=F", "KRW=X", "DX-Y.NYB", "BTC-USD"])
def test_markets_and_crypto_skip_the_fundamentals_analyst(ticker):
    analysts = RunRequest(ticker=ticker, trade_date="2026-11-05").selected_analysts()
    assert "fundamentals" not in analysts and analysts == ["market", "social", "news"]


@pytest.mark.parametrize("ticker", ["NVDA", "005930.KS", "SOXL", "BRK-B"])
def test_companies_keep_every_analyst(ticker):
    assert RunRequest(ticker=ticker, trade_date="2026-11-05").selected_analysts() == list(ANALYSTS)


class CountingBroker(MockBroker):
    name = "toss"

    def __init__(self):
        self.calls = []

    async def quote(self, symbol):
        self.calls.append(("quote", symbol))
        return 1.0, "USD"

    async def candles(self, symbol, interval, count):
        self.calls.append(("candles", symbol))
        return [Bar("2026-11-04T14:30:00+00:00", 1, 2, 0.5, 1.5, 100)]


@pytest.mark.parametrize("ticker", ["^KS11", "NQ=F", "KRW=X", "BTC-USD"])
def test_market_and_crypto_prices_skip_the_broker(ticker):
    broker = CountingBroker()
    yahoo_bar = [Bar("2026-11-04T00:00:00+00:00", 1, 1, 1, 1, 0)]
    source = PriceSource(broker, yahoo_quote=lambda t: (2.0, "KRW"), yahoo_candles=lambda t, i, c: yahoo_bar)
    assert asyncio.run(source.quote(ticker)).source == "yahoo"
    assert asyncio.run(source.candles(ticker, "1d", 10))["source"] == "yahoo"
    assert broker.calls == []


def test_a_company_still_asks_the_broker_first():
    broker = CountingBroker()
    source = PriceSource(broker, yahoo_quote=lambda t: pytest.fail("broker answered"))
    assert asyncio.run(source.quote("NVDA")).source == "toss"
    assert broker.calls == [("quote", "NVDA")]


def test_korean_indices_take_korean_market_drivers():
    assert relation_for("^KQ11").drivers == ("^KS11", "KRW=X", "ES=F")   # the Korean market set
    assert relation_for("^KS11").drivers == ("KRW=X", "ES=F")            # less itself
    assert "KRW=X" not in relation_for("NVDA").drivers
