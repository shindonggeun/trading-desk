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
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .broker.base import Bar, Broker
from .indicators import WARMUP, annotate, normalize

log = logging.getLogger(__name__)

QUOTE_TTL_SEC = 10.0
CANDLE_TTL_SEC = 30.0
CACHE_MAX = 256
# 1M is a month, 1m a minute. 240m and 1y are built here from Yahoo's 60m and monthly bars.
INTERVALS = ("1m", "5m", "15m", "30m", "60m", "240m", "1d", "1w", "1M", "1y")
MAX_COUNT = 500
# Toss serves at most 200 bars and only 1m / 1d ("지원하지 않는 캔들 주기"); everything else,
# and longer histories, come straight from Yahoo.
BROKER_MAX_COUNT = 200
BROKER_INTERVALS = ("1m", "1d")
_YAHOO_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "60m": "60m", "240m": "60m",
                   "1d": "1d", "1w": "1wk", "1M": "1mo", "1y": "1mo"}
_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "240m": 240}
# How far back Yahoo keeps intraday bars; the chart says "최근 N일만" when a request runs past it.
HISTORY_LIMIT_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "240m": 730}
# Short bars move, long ones barely do: cache each for about as long as it stays useful.
_CANDLE_TTL = {"1m": CANDLE_TTL_SEC, "5m": CANDLE_TTL_SEC, "15m": 60.0, "30m": 60.0, "60m": 120.0,
               "240m": 300.0, "1d": CANDLE_TTL_SEC, "1w": 300.0, "1M": 3600.0, "1y": 3600.0}


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float
    currency: str | None
    source: str            # kis (websocket trade) | toss | yahoo
    # When the desk read it (UTC), not when the market printed it: a Yahoo fallback can
    # lag ~20 min and, on a weekend, is the last close.
    fetched_at: str

    def to_dict(self) -> dict:
        return asdict(self)


CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


def is_market_symbol(ticker: str) -> bool:
    """A Yahoo index (^KS11), futures contract (NQ=F), FX pair (KRW=X) or ICE index (DX-Y.NYB):
    a market, not a company."""
    t = ticker.upper()
    return t.startswith("^") or t.endswith(("=F", "=X", ".NYB"))


def off_broker(ticker: str) -> bool:
    """Symbols a stock broker never lists - straight to Yahoo instead of a failing broker call."""
    return is_market_symbol(ticker) or ticker.upper().endswith(CRYPTO_SUFFIXES)


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


def _yahoo_period(interval: str, count: int) -> str:
    if interval == "1m":
        return "5d"                       # Yahoo keeps 1-minute bars for about a week
    if interval in _MINUTES:
        # ~6.5 trading hours a day, 5 trading days in 7, plus room for holidays.
        days = math.ceil(count * _MINUTES[interval] / 390 * 7 / 5 * 1.3) + 5
        return f"{min(days, HISTORY_LIMIT_DAYS[interval] - 1)}d"
    if interval == "1w":
        return f"{count * 7 + 14}d"
    if interval == "1M":
        return f"{count * 31 + 62}d"
    if interval == "1y":
        return "max"
    return f"{max(count * 2, 30)}d"


def yahoo_candles(ticker: str, interval: str, count: int) -> list[Bar]:
    import yfinance as yf

    frame = yf.Ticker(ticker).history(period=_yahoo_period(interval, count),
                                      interval=_YAHOO_INTERVAL[interval], auto_adjust=False)
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    # The index is in the exchange's own time zone, so .date() is the trading day there.
    rows = []
    for index, row in frame.iterrows():
        volume = row["Volume"]
        rows.append((index, Bar(at=index.to_pydatetime().astimezone(UTC).isoformat(timespec="seconds"),
                                open=float(row["Open"]), high=float(row["High"]), low=float(row["Low"]),
                                close=float(row["Close"]), volume=0.0 if math.isnan(volume) else float(volume))))
    if interval == "240m":
        bars = group_bars([(index.date(), bar) for index, bar in rows], size=4)
    elif interval == "1y":
        bars = group_bars([(index.year, bar) for index, bar in rows])
    else:
        bars = [bar for _, bar in rows]
    return bars[-count:]


def group_bars(keyed: list[tuple[object, Bar]], size: int | None = None) -> list[Bar]:
    """Merge consecutive bars sharing a key (a trading day, a year) into one, ``size`` at a
    time when given - so a 240-minute bar never spans two sessions. Bars come oldest first."""
    groups: list[list[Bar]] = []
    last_key: object = object()
    for key, bar in keyed:
        if not groups or key != last_key or (size and len(groups[-1]) == size):
            groups.append([])
        groups[-1].append(bar)
        last_key = key
    return [Bar(at=g[0].at, open=g[0].open, high=max(b.high for b in g), low=min(b.low for b in g),
                close=g[-1].close, volume=sum(b.volume for b in g)) for g in groups]


class PriceSource:
    def __init__(self, broker: Broker, yahoo_quote=None, yahoo_candles=None,
                 clock=time.monotonic, stream=None) -> None:
        self.broker = broker
        # desk.broker.kis_stream.KisStream or None: a subscribed ticker's last trade wins
        self.stream = stream
        # Looked up at call time when not injected, so tests can keep Yahoo off the network.
        self._yahoo_quote = yahoo_quote or (lambda *args: globals()["yahoo_quote"](*args))
        self._yahoo_candles = yahoo_candles or (lambda *args: globals()["yahoo_candles"](*args))
        self._clock = clock
        self._quotes: dict[str, tuple[float, Quote]] = {}
        self._candles: dict[tuple[str, str, int, tuple[str, ...]], tuple[float, dict, float]] = {}

    async def quote(self, ticker: str) -> Quote:
        tick = self.stream.latest(ticker) if self.stream is not None else None
        if tick is not None:
            currency = "KRW" if ticker.upper().endswith((".KS", ".KQ")) else "USD"
            return Quote(ticker, _tidy(tick.price), currency, "kis", tick.received_at)
        cached = self._quotes.get(ticker)
        if cached and self._clock() - cached[0] < QUOTE_TTL_SEC:
            return cached[1]
        try:
            if off_broker(ticker):
                raise NotImplementedError
            price, currency = await self.broker.quote(broker_symbol(ticker))
            quote = Quote(ticker, _tidy(price), currency, self.broker.name, _now())
        except Exception as exc:  # noqa: BLE001 - any broker miss falls back to Yahoo
            if not isinstance(exc, NotImplementedError):
                log.info("broker quote for %s failed, using Yahoo: %s", ticker, exc)
            price, currency = await asyncio.to_thread(self._yahoo_quote, ticker)
            quote = Quote(ticker, _tidy(price), currency, "yahoo", _now())
        _remember(self._quotes, ticker, (self._clock(), quote), QUOTE_TTL_SEC, self._clock)
        return quote

    async def candles(self, ticker: str, interval: str = "1d", count: int = 120,
                      indicators: tuple[str, ...] = ()) -> dict:
        """{"ticker", "interval", "source", "fetched_at", "bars": [...oldest first], "indicators",
        "history_limit_days", "truncated"}. With "rsi14" each bar also carries "rsi", with "stoch"
        "stoch_k" / "stoch_d", with "bb20" / "bb50" a {"mid", "upper", "lower"} (desk.indicators)."""
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {INTERVALS}")
        indicators = normalize(indicators)
        count = min(max(count, 1), MAX_COUNT)
        key = (ticker, interval, count, indicators)
        ttl = _CANDLE_TTL[interval]
        cached = self._candles.get(key)
        if cached and self._clock() - cached[0] < ttl:
            return cached[1]
        # Extra bars ahead of the shown ones so the indicators have settled by the first bar drawn.
        fetch = count + max((WARMUP[i] for i in indicators), default=0)
        try:
            if interval not in BROKER_INTERVALS or count > BROKER_MAX_COUNT or off_broker(ticker):
                raise NotImplementedError
            # Toss caps at 200: a chart that fits keeps Toss as its source with the RSI on, taking
            # only the warm-up bars that still fit, rather than flipping to Yahoo's (unadjusted) bars.
            bars = await self.broker.candles(broker_symbol(ticker), interval, min(fetch, BROKER_MAX_COUNT))
            if not bars:
                raise ValueError("no bars")
            source = self.broker.name
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, NotImplementedError):
                log.info("broker candles for %s failed, using Yahoo: %s", ticker, exc)
            bars = await asyncio.to_thread(self._yahoo_candles, ticker, interval, fetch)
            source = "yahoo"
        rows = [asdict(bar) for bar in bars]
        annotate(rows, indicators)
        rows = rows[-count:]
        result = {"ticker": ticker, "interval": interval, "source": source, "fetched_at": _now(),
                  "bars": rows, "indicators": list(indicators),
                  "history_limit_days": HISTORY_LIMIT_DAYS.get(interval), "truncated": len(rows) < count}
        _remember(self._candles, key, (self._clock(), result, ttl), ttl, self._clock)
        return result


def _remember(cache: dict, key, entry: tuple, ttl: float, clock) -> None:
    """Store, dropping expired entries once the cache grows past CACHE_MAX tickers. An entry
    may carry its own TTL as a third item (candles: each interval keeps its own)."""
    if len(cache) >= CACHE_MAX:
        now = clock()
        for stale in [k for k, item in cache.items() if now - item[0] >= (item[2] if len(item) > 2 else ttl)]:
            del cache[stale]
        if len(cache) >= CACHE_MAX:
            cache.clear()
    cache[key] = entry
