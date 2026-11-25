"""The "내 투자" panel: holdings valued at live prices and the live USD/KRW rate, in both
currencies, for GET /api/portfolio and its SSE stream.

Prices come from desk.prices (Toss, else Yahoo, cached ~10 s); the rate from the broker
(Toss /api/v1/exchange-rate, the mid rate) else Yahoo ``KRW=X``, cached the same way.
One Portfolio object serves every open tab, so N streams cost the same Toss calls as one.

#689 The rate is market data, so it goes through the shared tier (desk.shared_cache, ``fx``) like the prices:
in split roles the collector's demand job (``refresh_fx``) keeps it fresh while a screen reads it, and a
process's own read shares it too. The holdings are account data and stay each process's own call.

When a price, the rate or the holdings call fails, the last good value stays and the
snapshot carries ``error`` - the panel keeps its numbers and says why they stopped moving.

Cost is converted at today's rate too: Toss reports no purchase-time rate, so the KRW
P/L of a US holding is the USD P/L at today's rate (the rate's own move is not in it), and
the P/L rate is the same in both currencies.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

from . import demand, pools, shared_cache
from .broker.base import Broker, Holding
from .prices import PriceSource

log = logging.getLogger(__name__)

FX_TTL_SEC = 10.0
# #689 desk.demand · desk.shared_cache: the rate as an FxRate dict under one key, and how long the collector shares
# it (twice its period demand.EVERY["fx"], 30 s; Toss itself renews it once a minute) - the api of split roles
# takes one this old, any other process one up to FX_TTL_SEC, as its own would be
FX_SHARED_NS = "fx"
FX_SHARED_KEY = "usd_krw"
FX_SHARED_TTL_SEC = 60.0
HOLDINGS_TTL_SEC = 10.0
SNAPSHOT_TTL_SEC = 1.0     # concurrent streams in the same second share one snapshot
YAHOO_FX_TICKER = "KRW=X"  # KRW per 1 USD


@dataclass(frozen=True)
class FxRate:
    usd_krw: float
    source: str        # toss | yahoo
    at: str            # when the desk read it (UTC)

    def to_dict(self) -> dict:
        return asdict(self)


def yahoo_usd_krw() -> float:
    import yfinance as yf

    with pools.yahoo_slot():                  # #874 the process's Yahoo reads at once (desk.pools.YAHOO)
        info = yf.Ticker(YAHOO_FX_TICKER).fast_info
        rate = info.get("lastPrice") or info.get("last_price")
    if not rate:
        raise ValueError("Yahoo has no USD/KRW rate")
    return float(rate)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _shared_fx(raw) -> FxRate | None:
    """#689 a shared rate only when it is one - a real number, a source and when it was read - a miss otherwise."""
    try:
        fx = FxRate(**raw)
    except TypeError:   # not a dict, or another shape
        return None
    ok = (isinstance(fx.usd_krw, int | float) and not isinstance(fx.usd_krw, bool) and math.isfinite(fx.usd_krw)
          and fx.usd_krw > 0 and isinstance(fx.source, str) and isinstance(fx.at, str))
    return fx if ok else None


def _krw(value: float) -> float:
    return float(round(value))


def _usd(value: float) -> float:
    return round(value, 2)


def _rate(pnl: float | None, cost: float | None) -> float | None:
    if pnl is None or not cost:
        return None
    return round(pnl / cost * 100, 2)


def value_row(holding: Holding, price: float | None, usd_krw: float | None) -> dict:
    """One holding in both currencies. Amounts are rounded per row (whole won, cents) so
    the totals are exactly the sum of what the panel shows."""
    native_value = price * holding.quantity if price is not None else None
    native_cost = holding.average_price * holding.quantity if holding.average_price is not None else None
    usd_native = holding.currency.upper() == "USD"

    def both(amount: float | None) -> dict:
        if amount is None:
            return {"krw": None, "usd": None}
        if usd_native:
            return {"krw": _krw(amount * usd_krw) if usd_krw else None, "usd": _usd(amount)}
        return {"krw": _krw(amount), "usd": _usd(amount / usd_krw) if usd_krw else None}

    value, cost = both(native_value), both(native_cost)
    pnl = {k: (round(value[k] - cost[k], 2) if value[k] is not None and cost[k] is not None else None)
           for k in ("krw", "usd")}
    native_pnl = native_value - native_cost if native_value is not None and native_cost is not None else None
    return {
        **holding.to_dict(),
        "last_price": price,
        "profit_rate": _rate(native_pnl, native_cost),
        "value": value,
        "cost": cost,
        "pnl": pnl,
        "pnl_rate": _rate(native_pnl, native_cost),
    }


def totals(rows: list[dict]) -> dict:
    def total(field: str, currency: str) -> float | None:
        parts = [row[field][currency] for row in rows]
        if any(part is None for part in parts):
            return None
        amount = sum(parts)
        return _krw(amount) if currency == "krw" else _usd(amount)

    out = {"krw": total("value", "krw"), "usd": total("value", "usd"),
           "pnl_krw": total("pnl", "krw"), "pnl_usd": total("pnl", "usd")}
    cost_krw, cost_usd = total("cost", "krw"), total("cost", "usd")
    out["pnl_rate"] = _rate(out["pnl_krw"], cost_krw) if cost_krw is not None else _rate(out["pnl_usd"], cost_usd)
    return out


class Portfolio:
    def __init__(self, broker: Broker, prices: PriceSource, yahoo_fx=yahoo_usd_krw,
                 clock=time.monotonic) -> None:
        self.broker = broker
        self.prices = prices
        self._yahoo_fx = yahoo_fx
        self._clock = clock
        self._lock = asyncio.Lock()
        self._fx: tuple[float, FxRate] | None = None
        self._holdings: tuple[float, list[Holding]] | None = None
        self._last_price: dict[str, tuple[float, str, str]] = {}   # ticker -> (price, source, at)
        self._snapshot: tuple[float, dict] | None = None

    async def usd_krw(self) -> FxRate:
        if self._fx and self._clock() - self._fx[0] < FX_TTL_SEC:
            return self._fx[1]
        # #689 a rate another process read (its ``at`` says when): in the api of split roles up to FX_SHARED_TTL_SEC
        # old - marked on every read past memory, a hit too, so the collector keeps it fresh - else FX_TTL_SEC
        demand.want(FX_SHARED_NS, FX_SHARED_KEY)
        hit = await shared_cache.aget(FX_SHARED_NS, FX_SHARED_KEY)
        if hit is not None:
            fx = _shared_fx(hit[0])
            if fx is not None and hit[1] <= (FX_SHARED_TTL_SEC if demand.enabled() else FX_TTL_SEC):
                # kept until FX_TTL_SEC after its read (or the shared value's end), at least a second
                life = max(1.0, min(FX_TTL_SEC - hit[1], hit[2]))
                self._fx = (self._clock() - (FX_TTL_SEC - life), fx)
                return fx
        fx = await self._read_fx()
        self._fx = (self._clock(), fx)
        # the collector's own reads (its holding watch) keep the collected lifetime: a 10 s write over its 60 s value
        # would leave the api a gap before the next collection
        await shared_cache.aput(FX_SHARED_NS, FX_SHARED_KEY, fx.to_dict(),
                                FX_SHARED_TTL_SEC if demand.serving() else FX_TTL_SEC)
        return fx

    async def refresh_fx(self, key: str) -> None:
        """#689 the collector's ``demand`` job (desk.demand.serve) for FX_SHARED_NS: the rate read now, the same way
        the api would (the broker, else Yahoo), shared for FX_SHARED_TTL_SEC. Never reads the shared tier (it would
        read its own value back); a failure propagates - demand.serve backs it off and the api reads it itself."""
        if key != FX_SHARED_KEY:
            raise ValueError("not a rate this desk reads")
        fx = await self._read_fx()
        self._fx = (self._clock(), fx)
        await shared_cache.aput(FX_SHARED_NS, FX_SHARED_KEY, fx.to_dict(), FX_SHARED_TTL_SEC)

    async def _read_fx(self) -> FxRate:
        try:
            return FxRate(round(await self.broker.usd_krw(), 4), self.broker.name, _now())
        except Exception as exc:  # noqa: BLE001 - any broker miss falls back to Yahoo
            if not isinstance(exc, NotImplementedError):
                log.info("broker USD/KRW failed, using Yahoo: %s", exc)
            return FxRate(round(await asyncio.to_thread(self._yahoo_fx), 4), "yahoo", _now())

    async def snapshot(self) -> dict:
        async with self._lock:
            if self._snapshot and self._clock() - self._snapshot[0] < SNAPSHOT_TTL_SEC:
                return self._snapshot[1]
            snap = await self._build()
            self._snapshot = (self._clock(), snap)
            return snap

    async def _build(self) -> dict:
        errors: list[str] = []

        # The holdings are always this process's own call to the broker, never the shared tier (#689): quantities,
        # averages and the account are account data, and the shared Redis is a server other projects use on the same
        # password - docs/security.md §5.2 keeps only market data there (the rate and the prices, not these).
        if self._holdings and self._clock() - self._holdings[0] < HOLDINGS_TTL_SEC:
            holdings = self._holdings[1]
        else:
            try:
                holdings = await self.broker.holdings()
                self._holdings = (self._clock(), holdings)
            except Exception as exc:  # noqa: BLE001 - an outage shows in the panel, not as a 500
                errors.append(f"holdings: {type(exc).__name__}: {exc}")
                holdings = self._holdings[1] if self._holdings else []

        fx: FxRate | None
        try:
            fx = await self.usd_krw()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fx: {type(exc).__name__}: {exc}")
            fx = self._fx[1] if self._fx else None

        quotes = await asyncio.gather(*(self.prices.quote(h.yahoo_ticker) for h in holdings),
                                      return_exceptions=True)
        rows, groups = [], {}
        for holding, quote in zip(holdings, quotes, strict=True):
            if isinstance(quote, BaseException):
                errors.append(f"price {holding.yahoo_ticker}: {type(quote).__name__}: {quote}")
                last = self._last_price.get(holding.yahoo_ticker)
                price, source, at = last if last else (holding.last_price, self.broker.name, None)
            else:
                price, source, at = quote.price, quote.source, quote.fetched_at
                self._last_price[holding.yahoo_ticker] = (price, source, at)
            row = value_row(replace(holding, last_price=price), price, fx.usd_krw if fx else None)
            row["price_source"], row["price_at"] = source, at
            rows.append(row)
            groups.setdefault(holding.market or "ETC", []).append(row)

        return {
            "broker": self.broker.name,
            "fx": fx.to_dict() if fx else None,
            "total": totals(rows),
            "groups": groups,
            "holdings": rows,      # flat list, the shape the panel read before groups existed
            "updated_at": _now(),
            "error": "; ".join(errors) or None,
        }
