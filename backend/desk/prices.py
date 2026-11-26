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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as clock_time

from . import demand, market_bus, market_calendar, pools, shared_cache
from .broker.base import Bar, Broker
from .indicators import WARMUP, annotate, normalize
from .single_flight import Flights

log = logging.getLogger(__name__)

QUOTE_TTL_SEC = 10.0
DEMAND_TTL_SEC = 30.0  # #689 desk.demand: the collector shares a quote this long, the api takes one this old
# #994 a refresh_quotes call waits this share of desk.demand.HANDLER_SEC (from its start) for the reads under way
# it joined: one still running then (a read a demand timeout cut, Toss still not answering) fails only its own
# tickers here - the call answers before HANDLER_SEC would cut it and fail the tickers it read itself too
JOIN_SHARE = 0.75
SHARED_NS = "quote"   # #689 desk.shared_cache: a Quote dict per ticker, QUOTE_TTL_SEC
CANDLE_TTL_SEC = 30.0
# #858 how far past their TTL a caller takes the bars it had, at once, while a background read brings new ones.
# 0 - wait for new bars - everywhere but the one caller that says so with ``stale_ok``: /api/batch/candles (the
# dashboard's tiles and sparks, desk.api BATCH_STALE_SEC). The levels, playbooks and projections built on bars,
# the charts, alerts and analyses always wait. Inside a read (its own task) it is 0 again, so the bars a read takes
# from another key (a KRX minute chart's 1m tail) are never old ones kept as new. Background reads run
# REFRESH_PARALLEL at a time; a caller that would wait for one still in that queue reads at once instead.
STALE_OK: ContextVar[float] = ContextVar("desk_prices_stale_ok", default=0.0)
REFRESH_PARALLEL = 4
# #874 a fresh entry read in the last fifth of its TTL answers at once and is read again in the background (the
# REFRESH_PARALLEL gate, as a stale one): a page polling the bars every half TTL or so never meets an expired one.
# Four fifths: early enough that the read (a source's 0.2-1 s) lands before the TTL, late enough that a key read
# once is not read twice
REFRESH_AHEAD = 0.8
# #874 the same bars asked with different counts (the market board's 1d 63, the levels' 1d 200, the bottom zone's
# 1d 5) are one read: a family - ticker, interval, indicators and the source its count goes to (``lane``: the
# broker for 1m / 1d up to BROKER_MAX_COUNT, else Yahoo - their bars differ, adjusted or not, so a count never
# changes its source by riding another's read) - is read with the widest count asked of it in the last
# MERGE_REMEMBER_SEC, and each asker takes its last ``count`` bars. A count past MERGE_MAX_COUNT (the chart
# scrolling back to MAX_COUNT) is a family of its own: the dashboard's small reads never carry 2,000 bars. 500 is
# the batch's own cap (desk.api BATCH_MAX_COUNT) and the widest count the screens' levels and channels ask.
# Remembered five minutes: past the screens' one-minute polls and the levels' 300 s, so a polled family keeps
# one read; a wide count asked once stops widening the reads after that
MERGE_MAX_COUNT = 500
MERGE_REMEMBER_SEC = 300.0
# #874 rows x (indicators + 1) past this are built on desk.pools.COMPUTE, not the event loop: a 2,000-bar chart
# with every indicator takes ~25 ms; the dashboard's 63 / 78 bars without one ~0.4 ms (not worth a thread hop)
COMPUTE_MIN_CELLS = 2000
# #858 (#391) an empty answer when bars are kept: the kept bars answer, and are asked for again this soon
CANDLES_EMPTY_RETRY_SEC = 30.0
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


@contextmanager
def stale_ok(seconds: float) -> Iterator[None]:
    """#858 inside: bars up to ``seconds`` past their TTL are answered at once (STALE_OK)."""
    token = STALE_OK.set(seconds)
    try:
        yield
    finally:
        STALE_OK.reset(token)


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

    with pools.yahoo_slot():                  # #874 fast_info reads on first use
        info = yf.Ticker(ticker).fast_info
        price = info.get("lastPrice") or info.get("last_price")
        currency = info.get("currency")
    if not price:
        raise ValueError(f"Yahoo has no price for {ticker}")
    return float(price), currency


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


YAHOO_EMPTY_RETRY_SEC = 0.5   # #861 the pause before asking Yahoo once more after an empty answer
_YAHOO_LONG = ("1d", "5d", "1wk", "1mo", "3mo")   # yfinance intervals an empty answer is asked again for


def _yahoo_rows(ticker: str, period: str, interval: str) -> list[tuple[object, Bar]]:
    """yfinance's bars as (index in the exchange's own time zone, Bar stamped UTC), oldest first."""
    import yfinance as yf

    with pools.yahoo_slot():                  # #874 the process's Yahoo reads at once (desk.pools.YAHOO)
        frame = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    if frame.empty and interval in _YAHOO_LONG:
        # #861 Yahoo now and then answers a live symbol with nothing ("possibly delisted; no price data found"),
        # right after yfinance's cookie fetch failed - once more after a breath; still nothing: no bars, as before.
        # Daily and longer only: days of them are never empty for a real symbol, while minute bars outside the
        # session are (asking again there would only slow every such read)
        log.info("Yahoo returned no %s bars for %s; asking once more", interval, ticker)
        time.sleep(YAHOO_EMPTY_RETRY_SEC)
        with pools.yahoo_slot():
            frame = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
        log.info("Yahoo %s bars for %s on the second ask: %s", interval, ticker,
                 "still none" if frame.empty else f"{len(frame)} row(s)")
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
        # #874 a family (family_key) -> (when read, result, TTL, the count it was read with)
        self._candles: dict[tuple, tuple[float, dict, float, int]] = {}
        self._inflight: dict[tuple, asyncio.Future] = {}   # #536 single-flight, per family
        self._widths: dict[asyncio.Future, int] = {}         # #874 the count each read in flight reads
        self._asked: dict[tuple, dict[int, float]] = {}      # #874 a family's counts asked lately -> when
        # #874 review: a family's read came back empty or failed - no read ahead of its TTL before this (clock)
        self._quiet: dict[tuple, float] = {}
        # #874 review: a read wider than the kept bars came back empty: (until, kept count, its count, answer) -
        # asks between the two counts take that answer until then instead of asking the source each time
        self._empty: dict[tuple, tuple[float, int, int, dict]] = {}
        self._quote_reads = Flights()     # #858 the bottom zone, the playbooks and the board asking one ticker at once
        self._refresh_gate: asyncio.Semaphore | None = None    # #858 background reads of stale bars, per loop
        self._refresh_loop: asyncio.AbstractEventLoop | None = None
        self._queued: set[asyncio.Future] = set()     # background reads still waiting for the gate
        self._collecting: dict[str, asyncio.Future] = {}   # #994 ticker -> the refresh_quotes read reading it

    async def quote(self, ticker: str) -> Quote:
        tick = self.stream.latest(ticker) if self.stream is not None else None
        if tick is not None:
            return kis_quote(ticker, tick)
        cached = self._quotes.get(ticker)
        if cached and self._clock() - cached[0] < QUOTE_TTL_SEC:
            return cached[1]
        return await self._quote_reads.run(ticker, lambda: self._read_quote(ticker))

    async def refresh_quote(self, ticker: str) -> None:
        """#689 the collector's ``demand`` job (desk.demand.serve): a quote the api's screens read, from the broker,
        shared for DEMAND_TTL_SEC. Never reads the shared tier (it would read its own value back) and never falls
        back to Yahoo: a failure propagates - demand.serve backs the ticker off and the api reads it itself.

        #994 a ticker a refresh_quotes read is still reading (one a demand timeout cut, the batch since off) is
        not read again: that read's answer is this one's - a failure when it failed, or failed this ticker."""
        reading = self._reading(ticker)
        if reading is not None:
            if ticker in await _outcome(reading):
                raise LookupError("the batch reading it got no quote")
            return

        async def fetch() -> None:
            if off_broker(ticker):
                raise ValueError("not a broker symbol")
            price, currency = await self.broker.quote(broker_symbol(ticker))
            quote = Quote(ticker, _tidy(price), currency, self.broker.name, _now())   # fetched_at: this read
            _remember(self._quotes, ticker, (self._clock(), quote), QUOTE_TTL_SEC, self._clock)
            await shared_cache.aput(SHARED_NS, ticker, quote.to_dict(), DEMAND_TTL_SEC)
            market_bus.publish("quote", ticker, quote.to_dict(), quote.source)   # #690 open market streams

        # its own flight: joining a quote() read in flight could take a shared value and write it back as new
        await self._quote_reads.run(("demand", ticker), fetch)

    async def refresh_quotes(self, tickers: list[str]) -> set[str]:
        """#994 refresh_quote for many tickers in one broker call (desk.demand.serve's ``quote`` batch; Toss: one
        GET per 200 symbols): each quote read is kept, shared for DEMAND_TTL_SEC and published as refresh_quote's.
        Returns the tickers that failed - off-broker ones, and those the broker left out (Broker.quotes: read
        alone as well, so never read again here). The broker call failing propagates, as one ticker's read does:
        demand.serve backs them all off.

        A ticker another of these reads is still reading (one a demand timeout left running) is not read again:
        this call waits for that read, and the ticker fails when it fails it, fails as a whole or is cancelled -
        or is still reading JOIN_SHARE of HANDLER_SEC after this call started: those tickers fail (backed off
        further), the ones this call read itself count as they came. The trade-off: a joined read answering
        between then and HANDLER_SEC shares its quotes all the same, but its tickers here count as failed."""
        deadline = asyncio.get_running_loop().time() + JOIN_SHARE * demand.HANDLER_SEC
        mine: list[str] = []
        joined: dict[asyncio.Future, list[str]] = {}       # another read under way -> the tickers it reads
        for ticker in dict.fromkeys(tickers):
            reading = self._reading(ticker)
            if reading is None:
                mine.append(ticker)
            else:
                joined.setdefault(reading, []).append(ticker)
        failed: set[str] = set()
        if mine:
            # its own task, never a quote() read's flight (whose shared value it would write back as new),
            # shielded: a demand timeout leaves it running, so what it reads is still shared
            task = asyncio.ensure_future(self._refresh_many(mine))
            for ticker in mine:
                self._collecting[ticker] = task

            def done(t: asyncio.Future, names: list[str] = mine) -> None:
                for name in names:
                    if self._collecting.get(name) is t:
                        del self._collecting[name]
                if not t.cancelled():
                    t.exception()                  # retrieved: the caller may have left
            task.add_done_callback(done)
            failed |= await _outcome(task)         # its failure propagates: demand backs every ticker off
        if joined:
            # never cancels them (asyncio.wait): a read left running still shares what it reads; this call's own
            # cancellation (demand's timeout, the job stopped) is raised
            left = max(0.0, deadline - asyncio.get_running_loop().time())
            done, _ = await asyncio.wait(joined, timeout=left)
            for reading, names in joined.items():
                if reading not in done or reading.cancelled() or reading.exception() is not None:
                    failed.update(names)           # still reading, cancelled or failed as a whole
                else:
                    failed.update(t for t in names if t in reading.result())
        return failed

    def _reading(self, ticker: str) -> asyncio.Future | None:
        """#994 the refresh_quotes read under way for ``ticker`` on this event loop, else None - as Flights, a
        read of another loop (tests run one asyncio.run after another) is never joined."""
        task = self._collecting.get(ticker)
        if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
            return None
        return task

    async def _refresh_many(self, tickers: list[str]) -> set[str]:
        failed = {t for t in tickers if off_broker(t)}
        by_symbol: dict[str, list[str]] = {}       # the broker's symbol -> its tickers (BRK-B, BRK.B: one symbol)
        for ticker in tickers:
            if ticker not in failed:
                by_symbol.setdefault(broker_symbol(ticker), []).append(ticker)
        if not by_symbol:
            return failed
        got = await self.broker.quotes(list(by_symbol))
        failed.update(t for s, names in by_symbol.items() if s not in got for t in names)
        read = [(t, got[s]) for s, names in by_symbol.items() if s in got for t in names]
        # two shared-tier writes at once: past shared_cache.TIMEOUT_SEC one would take the tier down for a while
        gate = asyncio.Semaphore(2)

        async def keep(ticker: str, price: float, currency: str | None) -> None:
            # refresh_quote's fetch, for a quote the batch read
            quote = Quote(ticker, _tidy(price), currency, self.broker.name, _now())   # fetched_at: this read
            _remember(self._quotes, ticker, (self._clock(), quote), QUOTE_TTL_SEC, self._clock)
            async with gate:
                await shared_cache.aput(SHARED_NS, ticker, quote.to_dict(), DEMAND_TTL_SEC)
            market_bus.publish("quote", ticker, quote.to_dict(), quote.source)   # #690 open market streams

        results = await asyncio.gather(*(keep(t, *hit) for t, hit in read), return_exceptions=True)
        failed.update(t for (t, _), result in zip(read, results, strict=True) if isinstance(result, BaseException))
        return failed

    def batch_ready(self) -> bool:
        """#994 the broker reads many quotes in one call now: desk.demand's ``quote`` batch is worth it (else
        each ticker's refresh_quote, a pass at a time)."""
        return self.broker.batching()

    async def _read_quote(self, ticker: str) -> Quote:
        # #689 a quote another process (collector · analyst · api) read in the last QUOTE_TTL_SEC - in the api of
        # split roles (desk.demand) up to DEMAND_TTL_SEC: the collector keeps fresh what the api marks here, on
        # every read past the tick and memory (a hit too - marking only misses would stop the collection)
        if not off_broker(ticker):
            demand.want(SHARED_NS, ticker)
        hit = await shared_cache.aget(SHARED_NS, ticker)
        if hit is not None:
            quote = _shared_quote(ticker, hit[0])
            if quote is not None and hit[1] <= (DEMAND_TTL_SEC if demand.enabled() else QUOTE_TTL_SEC):
                # kept until QUOTE_TTL_SEC after its fetch (or the shared value's end), not for a longer ttl's rest
                life = max(1.0, min(QUOTE_TTL_SEC - hit[1], hit[2]))
                _remember(self._quotes, ticker, (self._clock() - (QUOTE_TTL_SEC - life), quote),
                          QUOTE_TTL_SEC, self._clock)
                return quote
        try:
            if off_broker(ticker):
                raise NotImplementedError
            price, currency = await self.broker.quote(broker_symbol(ticker))
            quote = Quote(ticker, _tidy(price), currency, self.broker.name, _now())
        except Exception as exc:  # noqa: BLE001 - any broker miss falls back to Yahoo
            if not isinstance(exc, NotImplementedError):
                log.info("broker quote for %s failed, using Yahoo: %s", ticker, exc)
            price, currency = await pools.run(pools.SOURCE, pools.yahoo, self._yahoo_quote, ticker)
            quote = Quote(ticker, _tidy(price), currency, "yahoo", _now())
        _remember(self._quotes, ticker, (self._clock(), quote), QUOTE_TTL_SEC, self._clock)
        # the collector's own reads (holdings) keep the collected lifetime: a 10 s write over its 30 s value
        # would leave the api a gap before the next collection
        await shared_cache.aput(SHARED_NS, ticker, quote.to_dict(),
                                DEMAND_TTL_SEC if demand.serving() else QUOTE_TTL_SEC)
        market_bus.publish("quote", ticker, quote.to_dict(), quote.source)   # #690 open market streams
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
        key = family_key(ticker, interval, count, indicators)
        ttl = _CANDLE_TTL[interval]
        now = self._clock()
        width = self._width(key, count, now)
        cached = self._candles.get(key)
        if cached and cached[3] >= count:
            age = now - cached[0]
            if age < ttl:
                # #874 nearly stale: read again behind this answer - as wide as the entry (a narrower read would not
                # take its place), and not while a read just came back empty or failed (it is asked again then)
                if age >= ttl * REFRESH_AHEAD and now >= self._quiet.get(key, 0.0):
                    self._refresh(key, ticker, interval, max(width, cached[3]), indicators, ttl)
                return cut(cached[1], count)
            if age < ttl + STALE_OK.get():
                # #858 a screen's batch read: the bars it had, at once; a background read brings the new ones
                self._refresh(key, ticker, interval, width, indicators, ttl)
                return cut(cached[1], count)
        empty = self._empty.get(key)
        if empty is not None and now < empty[0] and empty[1] < count <= empty[2]:
            return cut(empty[3], count)          # #874 review: what a read this wide got a moment ago (no bars)
        # #536: one fetch per family at a time - a page's cards (and the playbooks' bars and the levels) asking
        # for the same bars together share it. A caller that gives up does not cancel it for the others.
        pending = self._pending(key)
        if pending is not None and pending in self._queued:
            # #858 a background read still waiting for its turn: nobody waits on it (its askers took the old
            # bars), so it gives way to a read now instead of keeping this caller in the queue
            self._queued.discard(pending)
            pending.cancel()
            pending = None
        if pending is not None and self._widths.get(pending, 0) < count:
            pending = None           # #874 a narrower read under way: this one reads wider (and the family's next)
        if pending is None:
            pending = self._start(key, width, self._fetch_candles(ticker, interval, width, indicators, key, ttl))
        got = await asyncio.shield(pending)
        if not got["bars"]:
            kept = self._candles.get(key)
            if kept and kept[3] >= count and kept[1].get("bars"):
                got = kept[1]          # #874 review: a wider read came back empty; the kept bars cover this count
        return cut(got, count)

    def _width(self, key: tuple, count: int, now: float) -> int:
        """#874 the count a read of ``key``'s family reads: the widest asked of it in the last
        MERGE_REMEMBER_SEC, this ask included."""
        if not isinstance(key[2], str):               # past MERGE_MAX_COUNT: a family of one count
            return count
        asked = self._asked.get(key)
        if asked is None:
            if len(self._asked) >= CACHE_MAX * 4:      # forget the families nobody asked lately
                for k in [k for k, a in self._asked.items() if now - max(a.values()) >= MERGE_REMEMBER_SEC]:
                    del self._asked[k]
                if len(self._asked) >= CACHE_MAX * 4:
                    self._asked.clear()
            asked = self._asked[key] = {}
        asked[count] = now
        for c in [c for c, at in asked.items() if now - at >= MERGE_REMEMBER_SEC]:
            del asked[c]
        return max(asked)

    def _refresh(self, key: tuple, ticker: str, interval: str, width: int, indicators: tuple[str, ...],
                 ttl: float) -> None:
        """A background read of ``key`` unless one is under way (queued from the start: until it holds the gate)."""
        if self._pending(key) is None:
            self._queued.add(self._start(key, width, self._refresh_candles(ticker, interval, width, indicators,
                                                                           key, ttl)))

    def _pending(self, key: tuple) -> asyncio.Future | None:
        pending = self._inflight.get(key)
        return pending if pending is not None and pending.get_loop() is asyncio.get_running_loop() else None

    def _start(self, key: tuple, width: int, fetch) -> asyncio.Future:
        task = asyncio.ensure_future(fetch)
        self._inflight[key] = task
        self._widths[task] = width

        def done(t: asyncio.Future) -> None:
            if self._inflight.get(key) is t:
                self._inflight.pop(key, None)
            self._queued.discard(t)
            self._widths.pop(t, None)
            if not t.cancelled():
                t.exception()                  # retrieved: nobody may be left waiting for it

        task.add_done_callback(done)
        return task

    async def _refresh_candles(self, ticker: str, interval: str, count: int, indicators: tuple[str, ...],
                               key: tuple, ttl: float) -> dict:
        """#858 a stale entry's background read, REFRESH_PARALLEL at a time per loop (a page's burst of stale
        bars must not take every worker thread at once)."""
        loop = asyncio.get_running_loop()
        if self._refresh_loop is not loop:
            self._refresh_gate, self._refresh_loop = asyncio.Semaphore(REFRESH_PARALLEL), loop
        try:
            await self._refresh_gate.acquire()
        finally:
            self._queued.discard(asyncio.current_task())     # holding the gate (or given way): not queued
        try:
            return await self._fetch_candles(ticker, interval, count, indicators, key, ttl)
        except Exception as exc:  # the kept bars stand; the next ask past the TTL reads again
            log.info("background candles for %s %s failed: %s", ticker, interval, type(exc).__name__)
            self._hush(key)                            # #874 review: not read ahead again at every ask
            raise
        finally:
            self._refresh_gate.release()

    async def _fetch_candles(self, ticker: str, interval: str, count: int, indicators: tuple[str, ...],
                             key: tuple, ttl: float) -> dict:
        # #858 a read always runs in its own task (_start), so this stays inside it: the bars it takes from
        # another key (_fill_krx's 1m) are new ones, never the old bars a batch caller may take
        STALE_OK.set(0.0)
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
            bars = await pools.run(pools.SOURCE, pools.yahoo, self._yahoo_candles, ticker, interval, fetch)
            source = "yahoo"
            if interval in _MINUTES and is_krx(ticker):
                if interval != "1m":
                    bars = await self._close_krx(ticker, interval, bars)
                bars, source = await self._fill_krx(ticker, interval, bars)
        if len(bars) * (len(indicators) + 1) >= COMPUTE_MIN_CELLS:
            rows = await pools.run(pools.COMPUTE, _rows, bars, indicators)
        else:
            rows = _rows(bars, indicators)
        rows = rows[-count:]
        result = {"ticker": ticker, "interval": interval, "source": source, "fetched_at": _now(),
                  "bars": rows, "indicators": list(indicators),
                  "history_limit_days": HISTORY_LIMIT_DAYS.get(interval), "truncated": len(rows) < count}
        kept = self._candles.get(key)
        now = self._clock()
        if not rows and kept and kept[1].get("bars"):
            # #858 (#391) an empty answer never replaces kept bars: they answer, and stay fresh for
            # CANDLES_EMPTY_RETRY_SEC - not every ask past the TTL asking Yahoo again. #874 review: never older than
            # they are (a narrower read under way may come back empty just after a wider one landed), and not read
            # ahead before that retry (at a long TTL that age is past REFRESH_AHEAD: a read at every ask)
            stamp = max(kept[0], now - max(0.0, ttl - CANDLES_EMPTY_RETRY_SEC))
            _remember(self._candles, key, (stamp, kept[1], ttl, kept[3]), ttl, self._clock)
            self._hush(key)
            if kept[3] >= count:
                return kept[1]
            # #874 review: wider than the kept bars, and empty: its askers get no bars, as before #874 - never fewer
            # bars passing for the count they asked (levels on 63 days as if on 200); the narrower ones still get
            # the kept bars (candles), and the counts between take this answer until the retry
            if len(self._empty) >= CACHE_MAX:
                self._empty = {k: e for k, e in self._empty.items() if e[0] > now}
            self._empty[key] = (now + CANDLES_EMPTY_RETRY_SEC, kept[3], count, result)
            return result
        if kept is None or kept[3] <= count or now - kept[0] >= ttl:
            # #874 a narrower read never takes the place of a wider one still fresh (its askers have their bars)
            _remember(self._candles, key, (now, result, ttl, count), ttl, self._clock)
            if rows:
                self._quiet.pop(key, None)
                if (empty := self._empty.get(key)) is not None and count >= empty[2]:
                    del self._empty[key]
        return result

    def _hush(self, key: tuple) -> None:
        """#874 review: no read of ``key`` ahead of its TTL for CANDLES_EMPTY_RETRY_SEC."""
        now = self._clock()
        if len(self._quiet) >= CACHE_MAX:
            self._quiet = {k: until for k, until in self._quiet.items() if until > now}
        self._quiet[key] = now + CANDLES_EMPTY_RETRY_SEC

    async def _close_krx(self, ticker: str, interval: str, bars: list[Bar]) -> list[Bar]:
        """Each finished day's 15:00 - 15:30 bucket from Yahoo's daily bars (close_tail) - Yahoo,
        not the broker, so the volume left over is counted the same way as the minutes'."""
        if not bars:
            return bars
        span = len({_at(b.at).astimezone(KST).date() for b in bars})
        try:
            daily = await pools.run(pools.SOURCE, pools.yahoo, self._yahoo_candles, ticker, "1d", span + 5)
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


def family_key(ticker: str, interval: str, count: int, indicators: tuple[str, ...] = ()) -> tuple:
    """#874 the cache key ``count`` bars of ``ticker`` are read and kept under (``indicators`` normalized):
    (ticker, interval, lane, indicators) - lane "broker" or "yahoo", the source that count goes to first
    (_fetch_candles), or the count itself past MERGE_MAX_COUNT."""
    if count > MERGE_MAX_COUNT:
        lane: str | int = count
    elif interval in BROKER_INTERVALS and count <= BROKER_MAX_COUNT and not off_broker(ticker):
        lane = "broker"
    else:
        lane = "yahoo"
    return (ticker, interval, lane, indicators)


def cut(result: dict, count: int) -> dict:
    """#874 a family's bars as one asker wants them: the last ``count`` (``truncated`` for that count)."""
    bars = result["bars"]
    rows = bars[-count:] if len(bars) > count else bars
    truncated = len(rows) < count
    if rows is bars and result.get("truncated") == truncated:
        return result
    return {**result, "bars": rows, "truncated": truncated}


def _rows(bars: list[Bar], indicators: tuple[str, ...]) -> list[dict]:
    rows = [asdict(bar) for bar in bars]
    annotate(rows, indicators)
    return rows


def kis_quote(ticker: str, tick) -> Quote:
    """A KIS realtime trade (desk.broker.kis_stream.Tick) as a quote."""
    currency = "KRW" if ticker.upper().endswith((".KS", ".KQ")) else "USD"
    return Quote(ticker, _tidy(tick.price), currency, "kis", tick.received_at)


def _shared_quote(ticker: str, raw) -> Quote | None:
    """#689 a shared quote only when it is one for this ticker with a real price - a miss otherwise."""
    try:
        quote = Quote(**raw)
    except TypeError:   # not a dict, or a shape from another version of this class
        return None
    ok = (quote.ticker == ticker and isinstance(quote.price, int | float) and not isinstance(quote.price, bool)
          and math.isfinite(quote.price) and quote.price > 0
          and isinstance(quote.source, str) and isinstance(quote.fetched_at, str)
          and (quote.currency is None or isinstance(quote.currency, str)))
    return quote if ok else None


async def _outcome(reading: asyncio.Future) -> set[str]:
    """#994 the tickers a refresh_quotes read under way (``reading``: a refresh_quotes call's own, or the one a
    refresh_quote joins) failed, waited for shielded: this call cut by demand's HANDLER_SEC leaves that read
    running. Its failure as a whole is raised. Its cancellation is a LookupError only when another cancelled it
    and this call was not cancelled (a failure demand.serve counts for these keys); this call cancelled as well
    (with it - the loop closing, the demand job stopped) raises its own CancelledError, so the cancellation
    goes on out of demand.serve."""
    try:
        return await asyncio.shield(reading)
    except asyncio.CancelledError:
        me = asyncio.current_task()
        if reading.cancelled() and not (me is not None and me.cancelling()):
            raise LookupError("the batch reading it was cancelled") from None
        raise


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
