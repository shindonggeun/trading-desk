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
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as clock_time

from . import market_calendar
from .broker.base import Bar, Broker
from .indicators import WARMUP, annotate, normalize

log = logging.getLogger(__name__)

QUOTE_TTL_SEC = 10.0
CANDLE_TTL_SEC = 30.0
CACHE_MAX = 256
# 1M is a month, 1m a minute. 120m / 240m and 1y are built here from Yahoo's 60m and monthly bars.
INTERVALS = ("1m", "5m", "15m", "30m", "60m", "120m", "240m", "1d", "1w", "1M", "1y")
# #206: the chart loads older bars as it scrolls left (doubling the count) up to this many.
MAX_COUNT = 2000
# Toss serves at most 200 bars and only 1m / 1d ("지원하지 않는 캔들 주기"); everything else,
# and longer histories, come straight from Yahoo.
BROKER_MAX_COUNT = 200
BROKER_INTERVALS = ("1m", "1d")
_YAHOO_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "60m": "60m", "120m": "60m", "240m": "60m",
                   "1d": "1d", "1w": "1wk", "1M": "1mo", "1y": "1mo"}
_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "120m": 120, "240m": 240}
# How far back Yahoo keeps intraday bars; the chart says "최근 N일만" when a request runs past it.
HISTORY_LIMIT_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "120m": 730, "240m": 730}
# Short bars move, long ones barely do: cache each for about as long as it stays useful.
_CANDLE_TTL = {"1m": CANDLE_TTL_SEC, "5m": CANDLE_TTL_SEC, "15m": 60.0, "30m": 60.0, "60m": 120.0, "120m": 120.0,
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


def _yahoo_period(interval: str, count: int, ticker: str = "") -> str:
    if interval == "1m":
        return "5d"                       # Yahoo keeps 1-minute bars for about a week
    if interval in _MINUTES:
        # ~6.5 trading hours a day, 5 trading days in 7, plus room for holidays. #579 CME futures
        # trade ~23 hours: read on a stock's day, NQ=F's 2,000 5-minute bars fetched 52 days, not ~2 weeks.
        session = 1380 if ticker.upper().endswith("=F") else 390
        days = math.ceil(count * _MINUTES[interval] / session * 7 / 5 * 1.3) + 5
        return f"{min(days, HISTORY_LIMIT_DAYS[interval] - 1)}d"
    if interval == "1w":
        return f"{count * 7 + 14}d"
    if interval == "1M":
        return f"{count * 31 + 62}d"
    if interval == "1y":
        return "max"
    return f"{max(count * 2, 30)}d"


def _yahoo_rows(ticker: str, period: str, interval: str) -> list[tuple[object, Bar]]:
    """yfinance's bars as (index in the exchange's own time zone, Bar stamped UTC), oldest first."""
    import yfinance as yf

    frame = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    rows = []
    for index, row in frame.iterrows():
        volume = row["Volume"]
        rows.append((index, Bar(at=index.to_pydatetime().astimezone(UTC).isoformat(timespec="seconds"),
                                open=float(row["Open"]), high=float(row["High"]), low=float(row["Low"]),
                                close=float(row["Close"]), volume=0.0 if math.isnan(volume) else float(volume))))
    return rows


def yahoo_candles(ticker: str, interval: str, count: int) -> list[Bar]:
    rows = _yahoo_rows(ticker, _yahoo_period(interval, count, ticker), _YAHOO_INTERVAL[interval])
    # The index is in the exchange's own time zone, so .date() is the trading day there.
    if interval in ("120m", "240m"):
        bars = group_bars([(index.date(), bar) for index, bar in rows], size=_MINUTES[interval] // 60)
    elif interval == "1y":
        bars = group_bars([(index.year, bar) for index, bar in rows])
    else:
        bars = [bar for _, bar in rows]
    if interval == "1d":
        bars = with_finished_session(ticker, bars, datetime.now(UTC),
                                     lambda: [bar for _, bar in _yahoo_rows(ticker, "5d", "1h")])
    return bars[-count:]


# #747 CME futures: the bar dated D trades from 18:00 New York on D-1 to 17:00 on D (desk.session_progress).
CME_REOPENS = clock_time(18)
_FINISHED: dict[tuple[str, date], Bar] = {}     # a finished session's bar never changes: built once
_FINISHED_MAX = 64


def _ny_midnight(day: date) -> str:
    """A US daily bar's stamp: midnight New York, in UTC (04:00 / 05:00)."""
    return datetime.combine(day, clock_time(0), market_calendar.ET).astimezone(UTC).isoformat(timespec="seconds")


def with_finished_session(ticker: str, bars: list[Bar], now: datetime, hourly: Callable[[], list[Bar]]) -> list[Bar]:
    """#747 Monday to Thursday from 18:00 New York, Yahoo dates the next session's live daily row with that
    evening's date, and yfinance keeps only the newer of two same-date rows (fix_Yahoo_returning_live_separate):
    the session that ended at 17:00 is gone and the live one sits in its place, right after the session before
    it - a chart missing a day, and a whole day's move read as a gap (#741). Put both back: the finished session
    rebuilt from the hourly bars (18:00 the evening before to 17:00), the live row moved to the next weekday,
    the session it is. Sunday evening drops nothing (no session is dated Sunday). Without the hourly bars the
    daily ones stay as they came (desk.gap_fill still keeps the live row from opening a gap)."""
    if not ticker.upper().endswith("=F") or not bars:
        return bars
    local = now.astimezone(market_calendar.ET)
    day = local.date()
    if local.weekday() > 3 or local.time() < CME_REOPENS or _bar_date(bars[-1]) != day:
        return bars
    key = (ticker.upper(), day)
    finished = _FINISHED.get(key)
    if finished is None:
        try:
            got = hourly()
        except Exception as exc:  # noqa: BLE001 - the daily bars stand as they came
            log.warning("hourly bars for %s's %s session failed: %s", ticker, day, type(exc).__name__)
            return bars
        start = datetime.combine(day - timedelta(days=1), CME_REOPENS, market_calendar.ET)
        end = datetime.combine(day, market_calendar.CME_CLOSES, market_calendar.ET)
        inside = [b for b in got if start <= _at(b.at) < end]
        if not inside:
            return bars
        finished = Bar(at=_ny_midnight(day), open=inside[0].open, high=max(b.high for b in inside),
                       low=min(b.low for b in inside), close=inside[-1].close, volume=sum(b.volume for b in inside))
        if len(_FINISHED) >= _FINISHED_MAX:
            _FINISHED.pop(next(iter(_FINISHED)))
        _FINISHED[key] = finished
    if math.isclose(bars[-1].open, finished.open, rel_tol=1e-9):
        return bars           # the finished session itself: no live row yet (or Yahoo not rolled over)
    live = replace(bars[-1], at=_ny_midnight(market_calendar.next_weekday(day)))
    return [*bars[:-1], finished, live]


def _bar_date(bar: Bar) -> date:
    """A US daily bar's own date (stamped midnight New York, so its UTC date)."""
    return _at(bar.at).date()


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


KST = timezone(timedelta(hours=9))
KRX_OPEN_MIN, KRX_CLOSE_MIN = 9 * 60, 15 * 60 + 30   # KRX regular session, minutes after midnight KST


def is_krx(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ"))


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(UTC)


def krx_bucket(at: datetime, minutes: int) -> datetime | None:
    """Start of the ``minutes`` bar that holds ``at`` - counted from 09:00 KST each day, like
    Yahoo's KRX bars (and so 120m / 240m: 09:00 · 11:00 · 13:00 · 15:00 / 09:00 · 13:00).
    None outside the regular session (NXT pre / after market): Yahoo has no bars there.
    The 15:30 closing print belongs to the bar before it."""
    kst = at.astimezone(KST)
    minute = kst.hour * 60 + kst.minute
    if not KRX_OPEN_MIN <= minute <= KRX_CLOSE_MIN:
        return None
    minute = min(minute, KRX_CLOSE_MIN - 1)   # the 15:30 closing print ends the last bar
    start = KRX_OPEN_MIN + (minute - KRX_OPEN_MIN) // minutes * minutes
    return kst.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0).astimezone(UTC)


def fold_minutes(bars: list[Bar], minutes: int) -> list[Bar]:
    """1-minute bars -> ``minutes`` bars stamped with their bucket start (see krx_bucket)."""
    keyed = [(start, bar) for bar in bars if (start := krx_bucket(_at(bar.at), minutes)) is not None]
    folded = group_bars(keyed)
    starts = list(dict.fromkeys(start for start, _ in keyed))
    return [Bar(at=start.isoformat(timespec="seconds"), open=b.open, high=b.high, low=b.low,
                close=b.close, volume=b.volume) for start, b in zip(starts, folded, strict=True)]


def fill_recent(history: list[Bar], recent: list[Bar], minutes: int) -> list[Bar]:
    """Yahoo's KRX minute bars run 15+ minutes late: from the first bucket the broker's recent
    1-minute bars reach, use those (folded) instead. When they reach only the tail of that first
    bucket, Yahoo's bar for it keeps its open and the two share high / low (volume: the larger,
    as neither side knows the other's minutes)."""
    inside = [b for b in recent if krx_bucket(_at(b.at), minutes) is not None]
    folded = fold_minutes(inside, minutes)
    if not folded:
        return history
    first = _at(folded[0].at)
    if _at(inside[0].at) > first:
        old = next((b for b in history if _at(b.at) == first), None)
        if old is not None:
            head = folded[0]
            folded[0] = Bar(at=head.at, open=old.open, high=max(old.high, head.high), low=min(old.low, head.low),
                            close=head.close, volume=max(old.volume, head.volume))
    return [b for b in history if _at(b.at) < first] + folded


def close_tail(history: list[Bar], daily: list[Bar], minutes: int, now: datetime) -> list[Bar]:
    """Yahoo's KRX minute bars stop at 14:59 every day - 15:00 to the 15:30 closing print is not
    late there, it is missing (only its 1m with prepost has the closing print). So for each day
    whose session is over, the bucket from 15:00 is made from that day's daily bar: open = the
    last minute close, close = the official close, volume = the day's volume less the minutes'
    (0 when the window starts that day after 09:00) (#379, seen after the close in production). The broker's recent 1-minute bars still replace
    it while they reach the session (fill_recent)."""
    official = {_at(d.at).astimezone(KST).date(): d for d in daily}
    now_kst = now.astimezone(KST)
    days: dict = {}
    for bar in history:
        days.setdefault(_at(bar.at).astimezone(KST).date(), []).append(bar)
    out: list[Bar] = []
    for day, bars in days.items():
        out.extend(bars)
        d = official.get(day)
        over = day < now_kst.date() or (day == now_kst.date() and now_kst.hour * 60 + now_kst.minute > KRX_CLOSE_MIN)
        tail_at = datetime(day.year, day.month, day.day, 15, 0, tzinfo=KST)
        last = bars[-1]
        if d is None or not over or _at(last.at) >= tail_at.astimezone(UTC):
            continue
        start = krx_bucket(tail_at, minutes)
        opened = datetime(day.year, day.month, day.day, 9, 0, tzinfo=KST).astimezone(UTC)
        # the volume left over is only known when the day's minutes are all here (from 09:00)
        rest = max(d.volume - sum(b.volume for b in bars), 0.0) if _at(bars[0].at) <= opened else 0.0
        if _at(last.at) == start:       # the last bucket already started before 15:00 (240m: 13:00)
            out[-1] = Bar(at=last.at, open=last.open, high=max(last.high, d.close), low=min(last.low, d.close),
                          close=d.close, volume=last.volume + rest)
        else:
            out.append(Bar(at=start.isoformat(timespec="seconds"), open=last.close, high=max(last.close, d.close),
                           low=min(last.close, d.close), close=d.close, volume=rest))
    return out


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
        self._inflight: dict[tuple[str, str, int, tuple[str, ...]], asyncio.Future] = {}   # #536 single-flight

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
        # #536: one fetch per key at a time - a page's cards (and the playbooks' bars and the levels) asking
        # for the same bars together share it. A caller that gives up does not cancel it for the others.
        pending = self._inflight.get(key)
        if pending is not None and pending.get_loop() is asyncio.get_running_loop():
            return await asyncio.shield(pending)
        task = asyncio.ensure_future(self._fetch_candles(ticker, interval, count, indicators, key, ttl))
        self._inflight[key] = task

        def done(t: asyncio.Future) -> None:
            if self._inflight.get(key) is t:
                self._inflight.pop(key, None)
            if not t.cancelled():
                t.exception()                  # retrieved: nobody may be left waiting for it

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _fetch_candles(self, ticker: str, interval: str, count: int, indicators: tuple[str, ...],
                             key: tuple, ttl: float) -> dict:
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
            if interval in _MINUTES and is_krx(ticker):
                if interval != "1m":
                    bars = await self._close_krx(ticker, interval, bars)
                bars, source = await self._fill_krx(ticker, interval, bars)
        rows = [asdict(bar) for bar in bars]
        annotate(rows, indicators)
        rows = rows[-count:]
        result = {"ticker": ticker, "interval": interval, "source": source, "fetched_at": _now(),
                  "bars": rows, "indicators": list(indicators),
                  "history_limit_days": HISTORY_LIMIT_DAYS.get(interval), "truncated": len(rows) < count}
        _remember(self._candles, key, (self._clock(), result, ttl), ttl, self._clock)
        return result

    async def _close_krx(self, ticker: str, interval: str, bars: list[Bar]) -> list[Bar]:
        """Each finished day's 15:00 - 15:30 bucket from Yahoo's daily bars (close_tail) - Yahoo,
        not the broker, so the volume left over is counted the same way as the minutes'."""
        if not bars:
            return bars
        span = len({_at(b.at).astimezone(KST).date() for b in bars})
        try:
            daily = await asyncio.to_thread(self._yahoo_candles, ticker, "1d", span + 5)
        except Exception as exc:  # noqa: BLE001 - the minutes still stand
            log.info("daily bars for %s failed, no closing bucket: %s", ticker, exc)
            return bars
        return close_tail(bars, daily, _MINUTES[interval], datetime.now(UTC))

    async def _fill_krx(self, ticker: str, interval: str, bars: list[Bar]) -> tuple[list[Bar], str]:
        """KRX minute bars from Yahoo, their late tail replaced by the broker's 1-minute bars
        (#379) - the live chart folds its stream into the same buckets. Yahoo alone on a miss."""
        try:
            recent = await self.candles(ticker, "1m", BROKER_MAX_COUNT)
        except Exception as exc:  # noqa: BLE001 - the history still stands
            log.info("recent 1m for %s failed, Yahoo only: %s", ticker, exc)
            return bars, "yahoo"
        if recent["source"] != self.broker.name:
            return bars, "yahoo"                 # Yahoo's own 1m lags just the same
        filled = fill_recent(bars, [Bar(**b) for b in recent["bars"]], _MINUTES[interval])
        return filled, ("yahoo" if filled is bars else f"yahoo+{self.broker.name}")


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
