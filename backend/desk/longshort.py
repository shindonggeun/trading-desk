"""Which way Binance futures traders lean on a ticker (#271): the long / short split of its USDT
perpetual, from Binance's public futures statistics (no key).

- ``globalLongShortAccountRatio``: share of all accounts net long vs net short - the page's default
- ``topLongShortPositionRatio``: the same by position size among the top traders
- ``takerlongshortRatio``: taker buy vs sell volume (who is crossing the spread)

A ticker uses the contract the off-hours estimate uses (``BinanceEstimator.resolve``): its own, a
proxy's (an index via its ETF) or, for a leveraged ETF, its base's. Nothing from another exchange is
mixed in: no Binance contract, ``contract: None``. Each contract is read once per CACHE_TTL_SEC (the
statistics move every 5 minutes); a failure is kept for FAIL_TTL_SEC so a polling page does not
hammer Binance, and it fails that ticker only.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from .estimate import BINANCE_URL
from .ttl_cache import TTLCache

log = logging.getLogger(__name__)

CACHE_TTL_SEC = 300.0
FAIL_TTL_SEC = 60.0
CACHE_MAX = 256
PERIOD = "5m"
DAY_POINTS = 289          # 24 h of 5-minute points plus the one 24 h ago
TAKER_HOUR = 12           # 5-minute points summed for the taker's last hour
PARALLEL = 4              # contracts read at once (three calls each)

Resolve = Callable[[str], Awaitable[tuple[str, str, str | None, float] | None]]


def _iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def split(row: dict | None) -> dict | None:
    """One ratio row -> {long_pct, short_pct, ratio, at}; None when it does not read."""
    if not row:
        return None
    long_, short = _num(row.get("longAccount")), _num(row.get("shortAccount"))
    if long_ is None or short is None or long_ < 0 or short < 0 or long_ + short <= 0:
        return None
    total = long_ + short                         # Binance's two shares add to 1; rounding aside
    return {"long_pct": round(100 * long_ / total, 1), "short_pct": round(100 * short / total, 1),
            "ratio": _num(row.get("longShortRatio")), "at": _iso(row.get("timestamp"))}


def taker(rows: list[dict]) -> dict | None:
    """The last 5-minute ratio and the buy share of the last hour's taker volume."""
    rows = [r for r in rows or [] if _num(r.get("buyVol")) is not None and _num(r.get("sellVol")) is not None]
    if not rows:
        return None
    hour = rows[-TAKER_HOUR:]
    buy, sell = sum(_num(r["buyVol"]) for r in hour), sum(_num(r["sellVol"]) for r in hour)
    return {"buy_sell_ratio": _num(rows[-1].get("buySellRatio")),
            "buy_pct_1h": round(100 * buy / (buy + sell), 1) if buy + sell > 0 else None,
            "at": _iso(rows[-1].get("timestamp"))}


def summarize(accounts: list[dict], top: list[dict], takers: list[dict]) -> dict:
    """The three statistics and the accounts' long share change over 24 h (in points)."""
    now, then = split(accounts[-1] if accounts else None), split(accounts[0] if len(accounts) >= DAY_POINTS else None)
    change = round(now["long_pct"] - then["long_pct"], 1) if now and then else None
    return {"accounts": now, "top_positions": split(top[-1] if top else None), "taker": taker(takers),
            "change_24h": {"long_pct": change}}


class LongShort:
    def __init__(self, resolve: Resolve, http: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._resolve = resolve
        self._http = http or httpx.AsyncClient(base_url=BINANCE_URL, timeout=10)
        self._clock = clock
        # contract -> (stamp, summary | the failure's message, kept as text: a re-raised cached
        # exception would grow its traceback on every hit)
        self._cache: TTLCache[str, dict | str] = TTLCache(CACHE_MAX, CACHE_TTL_SEC, clock=lambda: self._clock())
        self._resolve_failed = -FAIL_TTL_SEC      # the contract list failed: skip it for a while
        self._locks: dict[str, asyncio.Lock] = {}
        self._gate = asyncio.Semaphore(PARALLEL)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, contract: str, limit: int) -> list[dict]:
        r = await self._http.get(path, params={"symbol": contract, "period": PERIOD, "limit": limit})
        if r.status_code != 200:
            raise RuntimeError(f"Binance {path} {r.status_code}")
        rows = r.json()
        if not isinstance(rows, list):
            raise TypeError(f"Binance {path}: not a list")
        return sorted((x for x in rows if isinstance(x, dict)), key=lambda x: _num(x.get("timestamp")) or 0)

    async def _contract(self, contract: str) -> dict:
        lock = self._locks.setdefault(contract, asyncio.Lock())
        async with lock:                          # a board asking twice reads Binance once
            cached = self._cache.get(contract)
            if cached is not None:
                if isinstance(cached, str):
                    raise RuntimeError(cached)
                return cached
            try:
                async with self._gate:
                    accounts, top, takers = await asyncio.gather(
                        self._get("/futures/data/globalLongShortAccountRatio", contract, DAY_POINTS),
                        self._get("/futures/data/topLongShortPositionRatio", contract, 1),
                        self._get("/futures/data/takerlongshortRatio", contract, TAKER_HOUR))
                out = summarize(accounts, top, takers)
                if not out["accounts"]:
                    raise RuntimeError(f"Binance long/short: no data for {contract}")
            except Exception as exc:
                self._remember(contract, f"{type(exc).__name__}: {exc}")
                raise
            self._remember(contract, out)
            return out

    def _remember(self, contract: str, value: dict | str) -> None:
        # #391 / #628: a failure (its message) is kept only FAIL_TTL_SEC
        self._cache.put(contract, value, FAIL_TTL_SEC if isinstance(value, str) else None)

    async def get(self, ticker: str) -> dict:
        """{ticker, contract, basis, multiple, accounts, top_positions, taker, change_24h, source} or
        {ticker, contract: None} without a Binance contract, or with ``error`` when Binance failed."""
        failed = {"ticker": ticker, "contract": None, "error": "바이낸스 계약 목록을 읽지 못했습니다"}
        if self._clock() - self._resolve_failed < FAIL_TTL_SEC:
            return failed                         # a board of 30 does not retry exchangeInfo 30 times
        try:
            found = await self._resolve(ticker)
        except Exception as exc:  # noqa: BLE001 - exchangeInfo down: this ticker only
            self._resolve_failed = self._clock()
            log.warning("long/short: no contract list for %s: %s", ticker, type(exc).__name__)
            return failed
        if not found:
            return {"ticker": ticker, "contract": None}
        contract, method, basis, multiple = found
        head = {"ticker": ticker, "contract": contract, "basis": basis if method != "binance_perp_mark" else None,
                "multiple": multiple, "source": "binance"}
        try:
            return {**head, **await self._contract(contract)}
        except Exception as exc:  # noqa: BLE001 - one contract failing leaves the others
            log.warning("long/short %s failed: %s", contract, exc)
            return {**head, "accounts": None, "top_positions": None, "taker": None,
                    "change_24h": {"long_pct": None}, "error": "바이낸스 롱/숏 통계를 받지 못했습니다"}
