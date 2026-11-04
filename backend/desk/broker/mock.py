from __future__ import annotations

from .base import Broker, Holding


class MockBroker(Broker):
    """Fixed sample book for development."""

    name = "mock"

    async def holdings(self) -> list[Holding]:
        return [
            Holding("NVDA", "NVDA", "NVIDIA", "US", "USD", 12, 168.4, 181.2),
            Holding("005930", "005930.KS", "삼성전자", "KR", "KRW", 40, 71200, 74800),
        ]
