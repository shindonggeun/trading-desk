"""Off-hours price estimates from Binance stock perpetuals (issue #27).

Binance lists perpetual futures on stocks that trade around the clock
(``contractType=TRADIFI_PERPETUAL``): about 200 US names and ETFs
(``underlyingType=EQUITY``, base asset = ticker) and a handful of Korean ones
(``KR_EQUITY``, named SAMSUNG, SKHYNIX ...). Like kospilab.com and korulive.com, the
estimate anchors on the last regular-session close and moves it by what the contract
has done since that close:

    estimate = last close x (mark price now / mark price at that close)

Both ends are the contract's mark price (the 1-minute mark-price bar that ends at the
close, and the current mark), so the ratio cancels the contract's basis to the stock
and the KRW/USD rate at the anchor; no FX feed is needed. Public endpoints only (no
key). Binance refuses US IPs; the homelab in Korea is fine.

Beyond the stock contracts (issue #123):

- Commodities (``underlyingType=COMMODITY``): Yahoo futures CL=F, GC=F ... map to CLUSDT,
  XAUUSDT ... and anchor on the CME session end (17:00 New York).
- Proxies: an index with no contract moves with an ETF that has one (^NDX / NQ=F with QQQ,
  ^GSPC / ES=F with SPY, SOXX with SMH, KOSPI 200 with KODEX 200) — the "weekend Nasdaq".
- Leveraged and inverse ETFs Binance does not list (SOXS, SQQQ ...) are the base's move times
  the daily multiple (desk.related.LEVERAGED). Over a long close (a weekend) that is an
  approximation: the fund resets daily, the estimate does not.

``candles`` turns the contract's mark-price bars since the anchor into estimated bars of the
ticker (5m / 10m / 15m / 1h / 1d / 1w), the same ratio bar by bar, so a chart can continue past
the last real bar.

Limits: only tickers Binance lists; thin overnight books can make the mark jumpy; days
with unusual hours need an entry in SPECIAL_HOURS or the anchor lands on the wrong minute.
To add a Korean name: look for ``underlyingType=KR_EQUITY`` in /fapi/v1/exchangeInfo and add
its KRX code to KR_CONTRACTS.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

BINANCE_URL = "https://fapi.binance.com"
ESTIMATE_TTL_SEC = 15.0
CONTRACTS_TTL_SEC = 24 * 3600.0
CACHE_MAX = 256

# Korean contracts carry names, not KRX codes, so they are paired by hand. Check
# exchangeInfo (underlyingType=KR_EQUITY) when Binance lists more.
KR_CONTRACTS = {
    "005930": "SAMSUNGUSDT",        # 삼성전자
    "000660": "SKHYNIXUSDT",        # SK하이닉스
    "005380": "HYUNDAIUSDT",        # 현대차
    "009150": "SAMSUNGEMUSDT",      # 삼성전기
    "042700": "HANMIUSDT",          # 한미반도체
    "066570": "LGELECTRONICSUSDT",  # LG전자
    "035420": "NAVERUSDT",          # NAVER
    "069500": "KODEX200USDT",       # KODEX 200
}


# Yahoo futures -> Binance commodity perpetuals.
COMMODITY_CONTRACTS = {
    "CL=F": "CLUSDT", "BZ=F": "BZUSDT", "NG=F": "NATGASUSDT", "HG=F": "COPPERUSDT",
    "GC=F": "XAUUSDT", "SI=F": "XAGUSDT", "PL=F": "XPTUSDT", "PA=F": "XPDUSDT",
}
# No contract of its own: moves with this ticker's contract (an index with its ETF).
PROXIES = {
    "^NDX": "QQQ", "NQ=F": "QQQ", "^IXIC": "QQQ", "^GSPC": "SPY", "ES=F": "SPY", "^RUT": "IWM",
    "SOXX": "SMH", "^SOX": "SMH", "^KS200": "069500.KS", "^KS11": "069500.KS",
}
CANDLE_INTERVALS = {"5m": "5m", "10m": "5m", "15m": "15m", "1h": "1h", "1d": "1d", "1w": "1w"}
CANDLES_TTL_SEC = 60.0
CANDLE_LIMIT = 1000


@dataclass(frozen=True)
class Session:
    zone: ZoneInfo
    opens: clock_time
    closes: clock_time
    # Days that open or close at other times: date -> (opens, closes). Keep it current —
    # a missing early close anchors on minutes the stock never traded.
    special: dict[date, tuple[clock_time, clock_time]] = field(default_factory=dict)
    # CME Globex: Sunday 18:00 to Friday 17:00 New York, an hour's break each day at 17:00
    futures: bool = False

    def hours(self, day: date) -> tuple[clock_time, clock_time]:
        return self.special.get(day, (self.opens, self.closes))

    def close_moment(self, day: date) -> datetime:
        return datetime.combine(day, self.hours(day)[1], self.zone)


KRX = Session(ZoneInfo("Asia/Seoul"), clock_time(9, 0), clock_time(15, 30), {
    date(2026, 11, 19): (clock_time(10, 0), clock_time(16, 30)),   # 수능일: 한 시간 늦게 열고 닫는다
    date(2027, 1, 4): (clock_time(10, 0), clock_time(15, 30)),     # 새해 첫 거래일: 10시 개장
})
US = Session(ZoneInfo("America/New_York"), clock_time(9, 30), clock_time(16, 0), {
    date(2026, 11, 27): (clock_time(9, 30), clock_time(13, 0)),    # 추수감사절 다음 날
    date(2026, 12, 24): (clock_time(9, 30), clock_time(13, 0)),    # 크리스마스 이브
    date(2027, 11, 26): (clock_time(9, 30), clock_time(13, 0)),    # 추수감사절 다음 날
})

# (session date, close) pairs, oldest first, from daily bars.
DailyCloses = Callable[[str], list[tuple[date, float]]]


def yahoo_daily_closes(ticker: str) -> list[tuple[date, float]]:
    import yfinance as yf

    frame = yf.Ticker(ticker).history(period="15d", interval="1d", auto_adjust=False)
    return [(index.date(), float(row["Close"])) for index, row in frame.iterrows()]


FUTURES = Session(ZoneInfo("America/New_York"), clock_time(18, 0), clock_time(17, 0), futures=True)


def session_of(ticker: str) -> Session:
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")) or t.startswith(("^KS", "^KQ")):
        return KRX
    return FUTURES if t.endswith("=F") else US


def futures_open(now: datetime) -> bool:
    ny = now.astimezone(FUTURES.zone)
    day, hour = ny.weekday(), ny.hour
    if day == 5 or (day == 6 and hour < 18) or (day == 4 and hour >= 17):
        return False
    return hour != 17


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Estimate:
    ticker: str
    estimate: float
    change_pct: float              # contract move since the anchor close, %
    last_close: float
    last_close_date: str
    close_at: str                  # the anchor close, ISO UTC
    contract: str                  # Binance symbol
    contract_price: float          # mark price now
    contract_price_at_close: float # mark price at the anchor close
    market_state: str              # open | closed
    fetched_at: str
    supported: bool = True
    method: str = "binance_perp_mark"   # binance_perp_mark | proxy | leveraged
    basis: str | None = None            # the ticker whose contract moved it (proxy / leveraged base)
    multiple: float = 1.0               # leveraged: the daily multiple applied to the base's move

    def to_dict(self) -> dict:
        return asdict(self)


class BinanceEstimator:
    def __init__(self, http: httpx.AsyncClient | None = None, daily_closes: DailyCloses = yahoo_daily_closes,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._http = http or httpx.AsyncClient(base_url=BINANCE_URL, timeout=10)
        self._daily_closes = daily_closes
        self._now = now
        self._clock = clock
        self._us: dict[str, str] = {}
        self._us_loaded = -CONTRACTS_TTL_SEC
        self._contracts_lock = asyncio.Lock()
        # ticker -> (stamp, Estimate | None for "no contract" | Exception for a recent failure)
        self._cache: dict[str, tuple[float, object]] = {}
        self._anchors: dict[tuple[str, int], float] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict | None = None):
        r = await self._http.get(path, params=params)
        if r.status_code != 200:
            raise RuntimeError(f"Binance {path} {r.status_code}")
        return r.json()

    async def contract_for(self, ticker: str) -> str | None:
        """The Binance perpetual tracking this Yahoo ticker, or None."""
        ticker = ticker.upper()
        if ticker.endswith((".KS", ".KQ")):
            return KR_CONTRACTS.get(ticker[:-3])
        if ticker in COMMODITY_CONTRACTS:
            return COMMODITY_CONTRACTS[ticker]
        if ticker.startswith("^") or "=" in ticker:
            return None
        async with self._contracts_lock:
            if self._clock() - self._us_loaded >= CONTRACTS_TTL_SEC:
                info = await self._get("/fapi/v1/exchangeInfo")
                self._us = {
                    s["baseAsset"]: s["symbol"] for s in info.get("symbols", [])
                    if s.get("contractType") == "TRADIFI_PERPETUAL" and s.get("underlyingType") == "EQUITY"
                    and s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
                }
                self._us_loaded = self._clock()
        # BRK-B (Yahoo) / BRK.B (Toss) is BRKB on Binance.
        return self._us.get(ticker.replace("-", "").replace(".", ""))

    def _anchor_close(self, ticker: str, session: Session, now: datetime) -> tuple[date, float, datetime, bool]:
        """(session date, close, close moment, open now) for the last session that has ended."""
        # Yahoo sometimes returns a row with a NaN close (KR listings especially): not a bar.
        bars = [(day, close) for day, close in self._daily_closes(ticker) if math.isfinite(close) and close > 0]
        finished = [(day, close) for day, close in bars if session.close_moment(day) <= now]
        if not finished:
            raise ValueError(f"no finished daily bar for {ticker}")
        day, close = finished[-1]
        if session.futures:
            return day, close, session.close_moment(day), futures_open(now)
        local = now.astimezone(session.zone)
        opens, closes = session.hours(local.date())
        # Open = a weekday inside today's hours with a bar for today (a holiday has none).
        is_open = (local.weekday() < 5 and opens <= local.time() < closes
                   and any(d == local.date() for d, _ in bars))
        return day, close, session.close_moment(day), is_open

    async def _mark_at(self, contract: str, moment: datetime) -> float:
        """Close of the 1-minute mark-price bar that ends at ``moment``."""
        key = (contract, int(moment.timestamp()))
        if key not in self._anchors:
            start_ms = int(moment.timestamp() * 1000) - 60_000
            rows = await self._get("/fapi/v1/markPriceKlines", {"symbol": contract, "interval": "1m",
                                                                "startTime": start_ms, "limit": 1})
            if not rows or int(rows[0][0]) != start_ms:
                raise ValueError(f"Binance has no {contract} mark price at {_iso(moment)}")
            if len(self._anchors) >= CACHE_MAX:
                self._anchors.clear()
            self._anchors[key] = float(rows[0][4])
        return self._anchors[key]

    async def estimate(self, ticker: str) -> Estimate | None:
        """None when Binance has no contract for the ticker. A failure is remembered for the
        cache window too, so a page polling a broken ticker does not hammer Binance/Yahoo."""
        ticker = ticker.upper()
        cached = self._cache.get(ticker)
        if cached and self._clock() - cached[0] < ESTIMATE_TTL_SEC:
            if isinstance(cached[1], Exception):
                raise cached[1]
            return cached[1]
        try:
            result = await self._compute(ticker)
        except Exception as exc:
            self._remember(ticker, exc)
            raise
        self._remember(ticker, result)
        return result

    def _remember(self, ticker: str, value: object) -> None:
        if len(self._cache) >= CACHE_MAX:
            self._cache.clear()
        self._cache[ticker] = (self._clock(), value)

    async def resolve(self, ticker: str) -> tuple[str, str, str | None, float] | None:
        """(contract, method, basis ticker, multiple) for ``ticker``, or None: its own contract,
        else a proxy's, else (a leveraged ETF) its base's — own or proxy — with the multiple."""
        from .related import LEVERAGED

        ticker = ticker.upper()
        own = await self.contract_for(ticker)
        if own:
            return own, "binance_perp_mark", None, 1.0
        proxy = PROXIES.get(ticker)
        if proxy:
            contract = await self.contract_for(proxy)
            return (contract, "proxy", proxy, 1.0) if contract else None
        lever = LEVERAGED.get(ticker)
        if lever:
            base, multiple = lever
            contract = await self.contract_for(base)
            if not contract and PROXIES.get(base):
                contract = await self.contract_for(PROXIES[base])
            return (contract, "leveraged", base, float(multiple)) if contract else None
        return None

    async def _compute(self, ticker: str) -> Estimate | None:
        found = await self.resolve(ticker)
        if found is None:
            return None
        contract, method, basis, multiple = found
        now = self._now()
        session = session_of(ticker)
        day, close, close_at, is_open = await asyncio.to_thread(self._anchor_close, ticker, session, now)
        anchor = await self._mark_at(contract, close_at)
        mark = float((await self._get("/fapi/v1/premiumIndex", {"symbol": contract}))["markPrice"])
        move = multiple * (mark / anchor - 1)
        return Estimate(
            ticker=ticker, estimate=_round(close * (1 + move)), change_pct=round(move * 100, 2),
            last_close=_round(close), last_close_date=day.isoformat(), close_at=_iso(close_at),
            contract=contract, contract_price=mark, contract_price_at_close=anchor,
            market_state="open" if is_open else "closed", fetched_at=_iso(now),
            method=method, basis=basis, multiple=multiple,
        )

    async def candles(self, ticker: str, interval: str) -> dict | None:
        """Estimated bars of ``ticker`` since its last close: the contract's mark-price bars,
        each moved by the same anchor ratio (times the multiple for a leveraged ETF). None when
        there is no contract. 10-minute bars are two 5-minute ones."""
        if interval not in CANDLE_INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(CANDLE_INTERVALS)}")
        ticker = ticker.upper()
        key = f"candles:{ticker}:{interval}"
        cached = self._cache.get(key)
        if cached and self._clock() - cached[0] < CANDLES_TTL_SEC:
            if isinstance(cached[1], Exception):
                raise cached[1]
            return cached[1]
        try:
            result = await self._candles(ticker, interval)
        except Exception as exc:
            self._remember(key, exc)
            raise
        self._remember(key, result)
        return result

    async def _candles(self, ticker: str, interval: str) -> dict | None:
        found = await self.resolve(ticker)
        if found is None:
            return None
        contract, method, basis, multiple = found
        now = self._now()
        day, close, close_at, is_open = await asyncio.to_thread(self._anchor_close, ticker, session_of(ticker), now)
        anchor = await self._mark_at(contract, close_at)
        rows = await self._get("/fapi/v1/markPriceKlines", {
            "symbol": contract, "interval": CANDLE_INTERVALS[interval],
            "startTime": int(close_at.timestamp() * 1000), "limit": CANDLE_LIMIT})

        def price(p: str) -> float:
            return close * (1 + multiple * (float(p) / anchor - 1))

        bars = []
        for r in rows:
            o, h, lo, c = price(r[1]), price(r[2]), price(r[3]), price(r[4])
            bars.append({"t": int(r[0]), "open": o, "high": max(h, lo), "low": min(h, lo), "close": c})
        if interval == "10m":   # two 5-minute bars into one, on 10-minute boundaries
            merged: dict[int, dict] = {}
            for b in bars:
                k = b["t"] - b["t"] % 600_000
                m = merged.get(k)
                if m is None:
                    merged[k] = {**b, "t": k}
                else:
                    m.update(high=max(m["high"], b["high"]), low=min(m["low"], b["low"]), close=b["close"])
            bars = list(merged.values())
        out = [{"at": _iso(datetime.fromtimestamp(b["t"] / 1000, UTC)), "open": _round(b["open"]),
                "high": _round(b["high"]), "low": _round(b["low"]), "close": _round(b["close"]),
                "volume": 0, "estimated": True} for b in bars]
        return {"ticker": ticker, "interval": interval, "close": _round(close), "close_date": day.isoformat(),
                "close_at": _iso(close_at), "market_state": "open" if is_open else "closed",
                "contract": contract, "method": method, "basis": basis, "multiple": multiple, "bars": out}


__all__ = ["COMMODITY_CONTRACTS", "FUTURES", "KRX", "KR_CONTRACTS", "PROXIES", "US", "BinanceEstimator", "Estimate"]
