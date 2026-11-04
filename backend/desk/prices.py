"""Where prices come from: Toss first, Yahoo when Toss is not connected, does not list
the symbol, or fails. Every answer says which one it came from and when.

Toss has no push feed for market data, so quotes are polled and cached for a few
seconds per ticker; one server-side cache keeps the whole app inside Toss' MARKET_DATA
rate limit however many tabs are open.

Tickers here are what TradingAgents reads (005930.KS, NVDA, BRK-B); the broker gets
its own symbol (005930, NVDA, BRK.B).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .broker.base import Bar, Broker

log = logging.getLogger(__name__)

QUOTE_TTL_SEC = 10.0
CANDLE_TTL_SEC = 30.0
CACHE_MAX = 256
INTERVALS = ("1m", "1d", "1w")
MAX_COUNT = 500
# Toss serves at most 200 bars and no weekly bars ("지원하지 않는 캔들 주기"); longer or weekly
# histories come straight from Yahoo.
BROKER_MAX_COUNT = 200
BROKER_INTERVALS = ("1m", "1d")
_YAHOO_INTERVAL = {"1m": "1m", "1d": "1d", "1w": "1wk"}


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float
    currency: str | None
    source: str            # toss | yahoo
    # When the desk read it (UTC), not when the market printed it: a Yahoo fallback can
    # lag ~20 min and, on a weekend, is the last close.
    fetched_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def broker_symbol(ticker: str) -> str:
    """005930.KS -> 005930, BRK-B -> BRK.B, NVDA -> NVDA."""
    ticker = ticker.upper()
    for suffix in (".KS", ".KQ"):
        if ticker.endswith(suffix):
            return ticker[: -len(suffix)]
    return ticker.replace("-", ".")


def _tidy(price: float) -> float:
    # Yahoo hands back float noise (204.1999969); KRW quotes are whole won.
    return float(round(price)) if price >= 1000 else round(price, 4)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def yahoo_quote(ticker: str) -> tuple[float, str | None]:
    import yfinance as yf

    info = yf.Ticker(ticker).fast_info
    price = info.get("lastPrice") or info.get("last_price")
    if not price:
        raise ValueError(f"Yahoo has no price for {ticker}")
    return float(price), info.get("currency")


def yahoo_candles(ticker: str, interval: str, count: int) -> list[Bar]:
    import yfinance as yf

    # Yahoo keeps 1-minute bars for about a week; 5 days covers a weekend and a holiday.
    if interval == "1m":
        period = "5d"
    elif interval == "1w":
        period = f"{count * 7 + 14}d"
    else:
        period = f"{max(count * 2, 30)}d"
    frame = yf.Ticker(ticker).history(period=period, interval=_YAHOO_INTERVAL[interval], auto_adjust=False)
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    bars = [Bar(at=index.to_pydatetime().astimezone(UTC).isoformat(timespec="seconds"),
                open=float(row["Open"]), high=float(row["High"]), low=float(row["Low"]),
                close=float(row["Close"]), volume=float(row["Volume"]))
            for index, row in frame.iterrows()]
    return bars[-count:]


class PriceSource:
    def __init__(self, broker: Broker, yahoo_quote=yahoo_quote, yahoo_candles=yahoo_candles,
                 clock=time.monotonic) -> None:
        self.broker = broker
        self._yahoo_quote = yahoo_quote
        self._yahoo_candles = yahoo_candles
        self._clock = clock
        self._quotes: dict[str, tuple[float, Quote]] = {}
        self._candles: dict[tuple[str, str, int], tuple[float, dict]] = {}

    async def quote(self, ticker: str) -> Quote:
        cached = self._quotes.get(ticker)
        if cached and self._clock() - cached[0] < QUOTE_TTL_SEC:
            return cached[1]
        try:
            price, currency = await self.broker.quote(broker_symbol(ticker))
            quote = Quote(ticker, _tidy(price), currency, self.broker.name, _now())
        except Exception as exc:  # noqa: BLE001 - any broker miss falls back to Yahoo
            if not isinstance(exc, NotImplementedError):
                log.info("broker quote for %s failed, using Yahoo: %s", ticker, exc)
            price, currency = await asyncio.to_thread(self._yahoo_quote, ticker)
            quote = Quote(ticker, _tidy(price), currency, "yahoo", _now())
        _remember(self._quotes, ticker, (self._clock(), quote), QUOTE_TTL_SEC, self._clock)
        return quote

    async def candles(self, ticker: str, interval: str = "1d", count: int = 120) -> dict:
        """{"ticker", "interval", "source", "fetched_at", "bars": [...oldest first]}."""
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {INTERVALS}")
        count = min(max(count, 1), MAX_COUNT)
        key = (ticker, interval, count)
        cached = self._candles.get(key)
        if cached and self._clock() - cached[0] < CANDLE_TTL_SEC:
            return cached[1]
        try:
            if interval not in BROKER_INTERVALS or count > BROKER_MAX_COUNT:
                raise NotImplementedError
            bars = await self.broker.candles(broker_symbol(ticker), interval, count)
            if not bars:
                raise ValueError("no bars")
            source = self.broker.name
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, NotImplementedError):
                log.info("broker candles for %s failed, using Yahoo: %s", ticker, exc)
            bars = await asyncio.to_thread(self._yahoo_candles, ticker, interval, count)
            source = "yahoo"
        result = {"ticker": ticker, "interval": interval, "source": source, "fetched_at": _now(),
                  "bars": [asdict(bar) for bar in bars]}
        _remember(self._candles, key, (self._clock(), result), CANDLE_TTL_SEC, self._clock)
        return result


def _remember(cache: dict, key, entry: tuple[float, object], ttl: float, clock) -> None:
    """Store, dropping expired entries once the cache grows past CACHE_MAX tickers."""
    if len(cache) >= CACHE_MAX:
        now = clock()
        for stale in [k for k, (stamp, _) in cache.items() if now - stamp >= ttl]:
            del cache[stale]
        if len(cache) >= CACHE_MAX:
            cache.clear()
    cache[key] = entry
