"""The broker port. Read-only on purpose: placing orders needs a kill switch, order
idempotency and a daily cap first (see README, "Execution"), so there is no
``place_order`` here to call by accident."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

# #994 the default quotes() reads this many symbols at once - as desk.demand ran its per-ticker quote reads
QUOTES_PARALLEL = 2


@dataclass(frozen=True)
class Fill:
    """One executed order, as the broker reports it (averaged over its partial fills)."""

    order_id: str
    symbol: str            # the broker's symbol (005930, NVDA)
    side: str              # BUY | SELL
    quantity: float        # filled quantity
    price: float           # average fill price, native currency
    fee: float             # commission + tax, native currency
    currency: str
    filled_at: str         # ISO 8601 with offset (Toss gives KST)


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


@dataclass(frozen=True)
class Bar:
    at: str                # bar open time, ISO 8601 UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


# Investor classes of the KRX market-wide trading value (Toss Market Indicators group).
INVESTORS = ("individual", "foreigner", "institution", "other_corporation")
INSTITUTION_KINDS = ("financial_investment", "insurance", "trust", "private_equity_fund", "bank",
                     "other_financial_institution", "pension_fund")


@dataclass(frozen=True)
class FlowRecord:
    """One period of market-wide trading value by investor class, KRW integers."""
    date: str                                   # the period's representative day, YYYY-MM-DD
    updated_at: str                             # ISO 8601 with offset; today's row is provisional until the close
    investors: dict[str, tuple[int, int]]       # INVESTORS -> (buy, sell)
    institution_breakdown: dict[str, tuple[int, int]]  # INSTITUTION_KINDS -> (buy, sell)


@dataclass(frozen=True)
class IndexPrice:
    symbol: str                                 # KOSPI | KOSDAQ
    price: float
    at: str | None                              # ISO 8601 with offset


@dataclass(frozen=True)
class MarketDay:
    """One trading day of a market's regular session (desk.live). Closed all day: both None."""

    day: date                                   # the market's own date (KST · New York)
    opens: datetime | None
    closes: datetime | None


class Broker(ABC):
    name: str = "broker"

    @abstractmethod
    async def holdings(self) -> list[Holding]: ...

    @abstractmethod
    async def list_stocks(self, market: str) -> list[Listing]:
        """Every active listing of one market; feeds the name search (desk.universe)."""

    # Executions are optional and read-only (the trade review, desk.review). Still no way to place one.
    async def fills(self, start: date, end: date) -> list[Fill]:
        """Executed orders (filled quantity > 0) whose order date falls in [start, end], KST."""
        raise NotImplementedError

    # Market data is optional: a broker without it leaves desk.prices to fall back on Yahoo.
    async def quote(self, symbol: str) -> tuple[float, str | None]:
        """(last price, currency) for the broker's own symbol."""
        raise NotImplementedError

    async def quotes(self, symbols: list[str]) -> dict[str, tuple[float, str | None]]:
        """#994 ``quote`` for many of the broker's symbols: symbol -> (last price, currency). A symbol left out
        is one whose own read failed as well - the caller does not read it again. One symbol failing never fails
        the others; a failure every next read would meet too (``_stops_quotes``: Toss' 401 · 403 · 429, its
        token) stops the reads, and is raised when no symbol was read (else the ones not read are left out).
        This default reads them one by one, QUOTES_PARALLEL at a time; a broker with a many-symbol read (Toss)
        overrides it."""
        got, stop, _failures = await self._quotes_alone(symbols)
        if stop is not None and not got:
            raise stop
        return got

    def batching(self) -> bool:
        """#994 ``quotes`` reads many symbols in one call now (desk.demand's batch is worth it): not by default."""
        return False

    def _stops_quotes(self, exc: Exception) -> bool:
        """#994 a symbol's read failed in a way every next one's would: the rest are not read."""
        return False

    async def _quotes_alone(self, symbols: list[str]) -> tuple[dict[str, tuple[float, str | None]],
                                                                Exception | None, dict[str, Exception]]:
        """#994 each symbol's ``quote``, QUOTES_PARALLEL at a time: (what was read, the failure that stopped the
        reads or None, each other symbol's failure - left out of what was read)."""
        gate = asyncio.Semaphore(QUOTES_PARALLEL)
        out: dict[str, tuple[float, str | None]] = {}
        stop: list[Exception] = []
        failures: dict[str, Exception] = {}

        async def one(symbol: str) -> None:
            async with gate:
                if stop:
                    return                                 # the source refuses: no more reads this call
                try:
                    out[symbol] = await self.quote(symbol)
                except Exception as exc:  # noqa: BLE001 - that symbol only, unless every next one would fail too
                    if self._stops_quotes(exc):
                        stop.append(exc)
                    else:
                        failures[symbol] = exc

        await asyncio.gather(*(one(s) for s in dict.fromkeys(symbols)))
        return out, (stop[0] if stop else None), failures

    async def candles(self, symbol: str, interval: str, count: int) -> list[Bar]:
        """Up to ``count`` bars of ``interval`` (1m | 1d), oldest first."""
        raise NotImplementedError

    async def usd_krw(self) -> float:
        """KRW per 1 USD now; desk.portfolio falls back on Yahoo without it."""
        raise NotImplementedError

    # Market-wide investor flow (desk.market_flow). Read-only market data, no account involved.
    async def investor_flow(self, market: str, interval: str, count: int,
                            until: date | None = None) -> tuple[list[FlowRecord], str | None]:
        """Records of ``market`` (KOSPI | KOSDAQ) per ``interval`` (1d | 1w | 1mo | 1y), newest first,
        and the ``until`` day of the next page (None when there is none)."""
        raise NotImplementedError

    async def index_prices(self, symbols: list[str]) -> list[IndexPrice]:
        """Current level of market indices (KOSPI, KOSDAQ)."""
        raise NotImplementedError

    async def market_calendar(self, market: str, day: date | None = None) -> list[MarketDay]:
        """The regular session of ``market`` (KR | US) on the business days around ``day`` (the
        market's own date; today when None) as the broker publishes them — holidays and
        shortened days included."""
        raise NotImplementedError

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
