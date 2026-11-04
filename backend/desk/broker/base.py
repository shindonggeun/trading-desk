"""The broker port. Read-only on purpose: placing orders needs a kill switch, order
idempotency and a daily cap first (see README, "Execution"), so there is no
``place_order`` here to call by accident."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Holding:
    symbol: str            # the broker's own symbol (005930, AAPL)
    yahoo_ticker: str      # what TradingAgents reads (005930.KS, AAPL)
    name: str
    market: str            # KR | US
    currency: str
    quantity: float
    average_price: float | None
    last_price: float | None

    @property
    def profit_rate(self) -> float | None:
        if not self.average_price or self.last_price is None:
            return None
        return round((self.last_price / self.average_price - 1) * 100, 2)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "profit_rate": self.profit_rate}


# Markets searched by name. KR_ETC (KONEX) and US_ETC (OTC) are left out: Yahoo, which
# TradingAgents reads prices from, covers them poorly.
MARKETS = ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ", "AMEX")


@dataclass(frozen=True)
class Listing:
    symbol: str            # the broker's symbol (005930, NVDA)
    name: str              # Korean name, as Toss gives it for US listings too (엔비디아)
    market: str            # one of MARKETS
    security_type: str     # STOCK | ETF | ETN | ...


class Broker(ABC):
    name: str = "broker"

    @abstractmethod
    async def holdings(self) -> list[Holding]: ...

    @abstractmethod
    async def list_stocks(self, market: str) -> list[Listing]:
        """Every active listing of one market; feeds the name search (desk.universe)."""

    async def portfolio(self):
        """Holdings as TradingAgents' PortfolioContext, so the trader and the managers
        decide against the actual book. Cash stays unknown: one cash figure cannot
        describe a KRW + USD account."""
        from tradingagents.portfolio import PortfolioContext

        return PortfolioContext.model_validate({
            "positions": [
                {"ticker": h.yahoo_ticker, "quantity": h.quantity, "average_price": h.average_price}
                for h in await self.holdings()
            ],
        })
