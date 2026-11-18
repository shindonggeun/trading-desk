"""One instrument's card (#295): basic facts, the last ten sessions, indicators against the price,
the support / resistance steps around it, and short interest where a free source has it.

Everything comes from sources the desk already reads:

- Yahoo daily bars through desk.prices (260 sessions: the 52-week range and the 250-day average).
  That is more than the broker's 200-bar cap, so the indicators always come from Yahoo's bars
  (``bars_source``) - a Korean chart's own daily bars may be Toss's; the levels below are the chart's.
- The support / resistance levels of the chart itself - desk.sr.support_resistance with the default
  horizon, the very call (and cache entry) behind GET /api/levels - plus the volume profile's thick
  nodes the chart draws (매물대). The steps are therefore the chart's lines, never a second opinion.
- Yahoo ``info`` (cached a day) for the name, market cap, exchange, first trade and - US only -
  short interest. The KRX short-balance pages need a login (checked 2026-11-12: the data portal
  answers "LOGOUT"), so a Korean stock carries no short interest and says why.

Each section fails on its own: a missing source leaves its key null with the reason under
``errors`` and the rest of the card stands.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from statistics import fmean

from .indicators import bollinger as indicators_bollinger
from .indicators import rsi as indicators_rsi
from .sr import _local_day, support_resistance
from .ttl_cache import TTLCache

log = logging.getLogger(__name__)

DAILY_COUNT = 260               # 52 weeks + the 250-day average
YEAR_BARS = 252
RECENT_DAYS = 10
MOVING_AVERAGES = (5, 20, 60, 120, 250)
BB_N, BB_MULT = 20, 2.0
RSI_N = 14
RSI_HIGH, RSI_LOW = 70.0, 30.0
SUPPORT_STEPS = 3
RESISTANCE_STEPS = 10
SAME_STEP = 0.005               # a volume node within 0.5% of a chart level is that level
INFO_TTL_SEC = 24 * 3600.0
INFO_RETRY_SEC = 10 * 60.0      # a failed or empty Yahoo answer is asked again after this, not a day
PARALLEL = 4                    # cards built at once for ?tickers= (Yahoo and the shared thread pool)
INFO_CACHE_MAX = 512
MAX_TICKERS = 30
KR_SHORT_NOTE = "한국 공매도 잔고는 KRX 정보데이터시스템이 로그인을 요구해 무료로 받을 수 없습니다"

_INFO: TTLCache[str, object] = TTLCache(INFO_CACHE_MAX, INFO_TTL_SEC)   # the info, or a recent failure


def _pct(price: float | None, base: float | None) -> float | None:
    if price is None or not base:
        return None
    return round((price / base - 1) * 100, 2)


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4) if abs(value) < 1 else round(value, 2)


def korean(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ"))


# --- the bars ---------------------------------------------------------------------------------

def _day(at, ticker: str) -> str:
    """The market's own date of a bar (a Korean bar's UTC stamp is the evening before)."""
    try:
        return _local_day(str(at), ticker).isoformat()
    except (TypeError, ValueError):
        return str(at)[:10]


def recent(bars: list[dict], ticker: str = "", days: int = RECENT_DAYS) -> dict:
    """The last ``days`` sessions, newest first, each with its change on the session before; the
    window's highest and lowest close are flagged (closes, not intraday highs and lows)."""
    rows = []
    for i in range(max(len(bars) - days, 0), len(bars)):
        close = bars[i]["close"]
        prev = bars[i - 1]["close"] if i > 0 else None
        rows.append({"date": _day(bars[i]["at"], ticker), "close": _round(close), "change_pct": _pct(close, prev)})
    rows.reverse()
    if rows:
        top = max(rows, key=lambda r: r["close"])
        low = min(rows, key=lambda r: r["close"])
        for r in rows:
            r["highest_close"], r["lowest_close"] = r is top, r is low
    return {"days": rows, "high_close": top["close"] if rows else None, "low_close": low["close"] if rows else None}


def rsi(closes: list[float]) -> float | None:
    """RSI(14) of the last close - desk.indicators.rsi, the line the chart draws."""
    values = indicators_rsi(closes, RSI_N) if len(closes) > RSI_N else []
    last = values[-1] if values else None
    return None if last is None else round(last, 1)


def indicators(bars: list[dict], price: float) -> dict:
    """52-week range, Bollinger (20, 2), the moving averages and RSI(14) - each against ``price``.
    A history too short for one leaves that one null."""
    closes = [b["close"] for b in bars]
    year = bars[-YEAR_BARS:]
    out: dict = {"price": _round(price), "bars": len(bars)}
    if year:
        hi = max(b["high"] for b in year)
        lo = min(b["low"] for b in year)
        out["week52"] = {"high": _round(hi), "low": _round(lo), "from_high_pct": _pct(price, hi),
                         "from_low_pct": _pct(price, lo), "full": len(year) >= YEAR_BARS}
    else:
        out["week52"] = None
    band = indicators_bollinger(closes, BB_N, BB_MULT)[-1] if len(closes) >= BB_N else None
    if band:
        upper, mid, lower = band["upper"], band["mid"], band["lower"]
        out["bollinger"] = {"upper": _round(upper), "mid": _round(mid), "lower": _round(lower),
                            "to_upper_pct": _pct(price, upper), "to_lower_pct": _pct(price, lower)}
    else:
        out["bollinger"] = None
    out["moving_averages"] = [
        {"days": n, "value": _round(fmean(closes[-n:])) if len(closes) >= n else None,
         "vs_pct": _pct(price, fmean(closes[-n:])) if len(closes) >= n else None}
        for n in MOVING_AVERAGES]
    value = rsi(closes)
    out["rsi"] = None if value is None else {
        "value": value, "zone": "overbought" if value >= RSI_HIGH else "oversold" if value <= RSI_LOW else "neutral"}
    return out


# --- support / resistance steps ----------------------------------------------------------------

def steps(levels: dict, price: float) -> dict:
    """The chart's levels and thick volume nodes as steps away from ``price``: supports below
    (nearest first, up to SUPPORT_STEPS), resistances above (up to RESISTANCE_STEPS). A volume node
    on top of a chart level is that level, not a step of its own."""
    found = [{"price": lv["price"], "source": "level", "strength": lv.get("strength"), "basis": lv.get("basis"),
              "timeframe": lv.get("timeframe")} for lv in levels.get("levels") or []]
    vp = levels.get("volume_profile") or {}
    for node in vp.get("nodes") or []:
        if node.get("kind") != "thick":
            continue
        if any(abs(node["price"] / s["price"] - 1) <= SAME_STEP for s in found if s["source"] == "level"):
            continue
        found.append({"price": node["price"], "source": "volume", "share": node.get("share"),
                      "interval": vp.get("interval"), "bars": vp.get("bars")})
    below = sorted((s for s in found if s["price"] < price), key=lambda s: -s["price"])[:SUPPORT_STEPS]
    above = sorted((s for s in found if s["price"] >= price), key=lambda s: s["price"])[:RESISTANCE_STEPS]
    for n, s in enumerate(below, 1):
        s.update(step=n, distance_pct=_pct(s["price"], price))
    for n, s in enumerate(above, 1):
        s.update(step=n, distance_pct=_pct(s["price"], price))
    return {"supports": below, "resistances": above, "reference_price": levels.get("reference_price"),
            "as_of": levels.get("as_of"), "poc": vp.get("poc"),
            "volume_bars": vp.get("bars"), "volume_interval": vp.get("interval")}


# --- facts and short interest (Yahoo info) -----------------------------------------------------

def yahoo_info(ticker: str) -> dict:
    import yfinance as yf

    return yf.Ticker(ticker).info or {}


def _date(value) -> str | None:
    try:
        if value is None:
            return None
        seconds = float(value) / (1000 if abs(float(value)) > 1e11 else 1)
        # epoch + delta: a listing before 1970 is a negative stamp (fromtimestamp refuses it on Windows)
        return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def facts(info: dict, ticker: str, listing: dict | None) -> dict:
    name = (listing or {}).get("name") or info.get("longName") or info.get("shortName")
    cap = info.get("marketCap")
    return {"name": name, "market_cap": cap if isinstance(cap, (int, float)) and cap > 0 else None,
            "currency": info.get("currency"), "exchange": info.get("fullExchangeName") or info.get("exchange"),
            "market": (listing or {}).get("market"),
            # Yahoo's first trade in its own data: a Korean listing before 2000 shows 2000-01-04
            "first_trade_date": _date(info.get("firstTradeDateMilliseconds") or info.get("firstTradeDateEpochUtc")),
            "first_trade_note": "야후 기록의 첫 거래일(2000년 이전 상장 국내 종목은 2000-01-04)" if korean(ticker) else None}


def short_interest(info: dict, ticker: str) -> dict:
    if korean(ticker):
        return {"available": False, "reason": KR_SHORT_NOTE}
    shares = info.get("sharesShort")
    if not isinstance(shares, (int, float)) or shares <= 0:
        return {"available": False, "reason": "야후에 공매도 잔고가 없습니다"}
    pct = info.get("shortPercentOfFloat")
    return {"available": True, "shares": int(shares),
            "percent_of_float": round(pct * 100, 2) if isinstance(pct, (int, float)) else None,
            "days_to_cover": info.get("shortRatio"), "as_of": _date(info.get("dateShortInterest")),
            "source": "yahoo (FINRA 격주 보고)"}


class Profiles:
    """GET /api/stock/profile. ``info`` and ``levels`` are injectable (tests, no network)."""

    def __init__(self, prices, listing: Callable[[str], dict | None] = lambda t: None,
                 info: Callable[[str], dict] = yahoo_info, levels=support_resistance,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.prices, self.listing, self.info, self.levels, self.clock = prices, listing, info, levels, clock

    async def _info(self, ticker: str) -> dict:
        cached = _INFO.get(ticker, now=self.clock())
        if cached is not None:
            if isinstance(cached, Exception):
                raise cached
            return cached
        try:
            found = await asyncio.to_thread(self.info, ticker)
        except Exception as exc:
            self._keep(ticker, exc, INFO_RETRY_SEC)   # a refusing Yahoo is not asked on every refresh
            raise
        whole = bool(found.get("marketCap") or found.get("longName") or found.get("shortName"))
        self._keep(ticker, found, INFO_TTL_SEC if whole else INFO_RETRY_SEC)
        return found

    def _keep(self, ticker: str, value, ttl: float) -> None:
        _INFO.put(ticker, value, ttl, now=self.clock())

    async def one(self, ticker: str) -> dict:
        errors: dict[str, str] = {}

        async def part(name: str, coro):
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001 - one source down leaves the rest of the card
                errors[name] = type(exc).__name__
                log.info("stock profile %s for %s failed: %s", name, ticker, exc)
                return None

        candles, info, levels = await asyncio.gather(
            part("bars", self.prices.candles(ticker, "1d", DAILY_COUNT)),
            part("info", self._info(ticker)),
            part("levels", self.levels(self.prices, ticker)))
        quote = await part("quote", self.prices.quote(ticker))
        listing = await part("listing", asyncio.to_thread(self.listing, ticker))
        bars = (candles or {}).get("bars") or []
        price = quote.price if quote is not None else (bars[-1]["close"] if bars else None)

        def section(name: str, build):
            try:
                return build()
            except Exception as exc:  # noqa: BLE001 - an odd input breaks its own section only
                errors[name] = type(exc).__name__
                log.info("stock profile %s for %s failed: %s", name, ticker, exc)
                return None

        return {"ticker": ticker, "price": _round(price),
                # live: the broker's (or Yahoo's) quote now, see quote_source; close: the last bar
                "price_source": "live" if quote is not None else "close" if bars else None,
                "quote_source": getattr(quote, "source", None),
                "facts": section("facts", lambda: facts(info or {}, ticker, listing)),
                "recent": section("recent", lambda: recent(bars, ticker)) if bars else None,
                "indicators": section("indicators", lambda: indicators(bars, price)) if bars and price else None,
                "levels": section("levels", lambda: steps(levels, price)) if levels and price else None,
                "short": section("short", lambda: short_interest(info or {}, ticker))
                if info is not None or korean(ticker) else None,
                "bars_source": (candles or {}).get("source"),
                "computed_at": datetime.now(UTC).isoformat(timespec="seconds"), "errors": errors}

    async def many(self, tickers: list[str]) -> list[dict]:
        """Several cards, PARALLEL at a time; one that fails outright is {"ticker", "error"}."""
        gate = asyncio.Semaphore(PARALLEL)

        async def card(ticker: str) -> dict:
            async with gate:
                try:
                    return await self.one(ticker)
                except Exception as exc:  # noqa: BLE001 - never the whole answer for one card
                    log.warning("stock profile for %s failed: %s", ticker, exc)
                    return {"ticker": ticker, "error": type(exc).__name__}

        return list(await asyncio.gather(*(card(t) for t in tickers)))
