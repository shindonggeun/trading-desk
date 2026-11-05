from __future__ import annotations

from datetime import date, timedelta

from .base import Broker, Fill, Holding, Listing

# A handful of listings so the name search works without Toss keys (dev, tests, and the
# server until TOSS_* is set). Shape matches GET /api/v1/stocks/all.
SAMPLE_LISTINGS = [
    Listing("NVDA", "엔비디아", "NASDAQ", "STOCK"),
    Listing("AAPL", "애플", "NASDAQ", "STOCK"),
    Listing("MSFT", "마이크로소프트", "NASDAQ", "STOCK"),
    Listing("TSLA", "테슬라", "NASDAQ", "STOCK"),
    Listing("PLTR", "팔란티어", "NASDAQ", "STOCK"),
    Listing("TQQQ", "프로셰어즈 울트라프로 QQQ", "NASDAQ", "ETF"),
    Listing("SOXL", "디렉시온 반도체 불 3X", "AMEX", "ETF"),
    Listing("NVDL", "그래닛셰어즈 엔비디아 2X 롱", "NASDAQ", "ETF"),
    Listing("TSLL", "디렉시온 테슬라 불 2X", "NASDAQ", "ETF"),
    Listing("BRK.B", "버크셔 해서웨이 B", "NYSE", "STOCK"),
    Listing("005930", "삼성전자", "KOSPI", "STOCK"),
    Listing("000660", "SK하이닉스", "KOSPI", "STOCK"),
    Listing("035420", "NAVER", "KOSPI", "STOCK"),
    Listing("247540", "에코프로비엠", "KOSDAQ", "STOCK"),
    Listing("196170", "알테오젠", "KOSDAQ", "STOCK"),
    Listing("122630", "KODEX 레버리지", "KOSPI", "ETF"),
    Listing("233740", "KODEX 코스닥150레버리지", "KOSPI", "ETF"),
    Listing("252670", "KODEX 200선물인버스2X", "KOSPI", "ETF"),
]


class MockBroker(Broker):
    """Fixed sample book and listing for development."""

    name = "mock"

    async def holdings(self) -> list[Holding]:
        return [
            Holding("NVDA", "NVDA", "NVIDIA", "US", "USD", 12, 168.4, 181.2),
            Holding("005930", "005930.KS", "삼성전자", "KR", "KRW", 40, 71200, 74800),
        ]

    async def list_stocks(self, market: str) -> list[Listing]:
        return [listing for listing in SAMPLE_LISTINGS if listing.market == market]

    async def fills(self, start: date, end: date) -> list[Fill]:
        """Sample executions dated relative to today, so the review page always has rows."""
        today = date.today()

        def at(days_ago: int, hhmm: str) -> str:
            return f"{(today - timedelta(days=days_ago)).isoformat()}T{hhmm}:00+09:00"

        sample = [
            Fill("m-1", "NVDA", "BUY", 12, 168.4, 0.5, "USD", at(40, "23:41")),
            Fill("m-2", "005930", "BUY", 40, 71200, 1068, "KRW", at(35, "09:12")),
            Fill("m-3", "NVDA", "SELL", 5, 182.0, 0.4, "USD", at(12, "00:15")),
            Fill("m-4", "005930", "SELL", 10, 74800, 2805, "KRW", at(5, "14:02")),
        ]
        return [f for f in sample if start <= date.fromisoformat(f.filled_at[:10]) <= end]

