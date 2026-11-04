"""Off-hours price estimates from Binance stock perpetuals (issue #27).

Binance lists perpetual futures on stocks that trade around the clock
(``contractType=TRADIFI_PERPETUAL``): about 200 US names and ETFs
(``underlyingType=EQUITY``, base asset = ticker) and a handful of Korean ones
(``KR_EQUITY``, named SAMSUNG, SKHYNIX ...). Like kospilab.com and korulive.com, the
estimate anchors on the last regular-session close and moves it by what the contract
has done since that close:

    estimate = last close x (contract mark price now / contract price at that close)

The ratio cancels the contract's basis to the stock and the KRW/USD rate at the anchor,
so no FX feed is needed. Public endpoints only (no key). Binance refuses US IPs; the
homelab in Korea is fine.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class Session:
    zone: ZoneInfo
    opens: clock_time
    closes: clock_time


KRX = Session(ZoneInfo("Asia/Seoul"), clock_time(9, 0), clock_time(15, 30))
US = Session(ZoneInfo("America/New_York"), clock_time(9, 30), clock_time(16, 0))

# (session date, close) pairs, oldest first, from daily bars.
DailyCloses = Callable[[str], list[tuple[date, float]]]


def yahoo_daily_closes(ticker: str) -> list[tuple[date, float]]:
    import yfinance as yf

    frame = yf.Ticker(ticker).history(period="15d", interval="1d", auto_adjust=False)
    return [(index.date(), float(row["Close"])) for index, row in frame.iterrows()]


def session_of(ticker: str) -> Session:
    return KRX if ticker.upper().endswith((".KS", ".KQ")) else US


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
    contract_price_at_close: float
    market_state: str              # open | closed
    fetched_at: str
    supported: bool = True
    method: str = "binance_perp"

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
        self._cache: dict[str, tuple[float, Estimate]] = {}
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
        bars = self._daily_closes(ticker)
        local = now.astimezone(session.zone)
        finished = [(day, close) for day, close in bars
                    if datetime.combine(day, session.closes, session.zone) <= now]
        if not finished:
            raise ValueError(f"no finished daily bar for {ticker}")
        day, close = finished[-1]
        # Open = a weekday inside the hours with a bar for today (a holiday has none).
        is_open = (local.weekday() < 5 and session.opens <= local.time() < session.closes
                   and any(d == local.date() for d, _ in bars))
        return day, close, datetime.combine(day, session.closes, session.zone), is_open

    async def _contract_at(self, contract: str, moment: datetime) -> float:
        """Close of the 1-minute bar that ends at ``moment``."""
        key = (contract, int(moment.timestamp()))
        if key not in self._anchors:
            start_ms = int(moment.timestamp() * 1000) - 60_000
            rows = await self._get("/fapi/v1/klines", {"symbol": contract, "interval": "1m",
                                                       "startTime": start_ms, "limit": 1})
            if not rows or int(rows[0][0]) != start_ms:
                raise ValueError(f"Binance has no {contract} bar at {_iso(moment)}")
            if len(self._anchors) >= CACHE_MAX:
                self._anchors.clear()
            self._anchors[key] = float(rows[0][4])
        return self._anchors[key]

    async def estimate(self, ticker: str) -> Estimate | None:
        """None when Binance has no contract for the ticker."""
        ticker = ticker.upper()
        cached = self._cache.get(ticker)
        if cached and self._clock() - cached[0] < ESTIMATE_TTL_SEC:
            return cached[1]
        contract = await self.contract_for(ticker)
        if contract is None:
            return None
        now = self._now()
        session = session_of(ticker)
        day, close, close_at, is_open = await asyncio.to_thread(self._anchor_close, ticker, session, now)
        anchor = await self._contract_at(contract, close_at)
        mark = float((await self._get("/fapi/v1/premiumIndex", {"symbol": contract}))["markPrice"])
        ratio = mark / anchor
        result = Estimate(
            ticker=ticker, estimate=_round(close * ratio), change_pct=round((ratio - 1) * 100, 2),
            last_close=_round(close), last_close_date=day.isoformat(), close_at=_iso(close_at),
            contract=contract, contract_price=mark, contract_price_at_close=anchor,
            market_state="open" if is_open else "closed", fetched_at=_iso(now),
        )
        if len(self._cache) >= CACHE_MAX:
            self._cache.clear()
        self._cache[ticker] = (self._clock(), result)
        return result


__all__ = ["KR_CONTRACTS", "BinanceEstimator", "Estimate"]
