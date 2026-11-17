"""Find an instrument by Korean name or ticker.

Toss lists every active stock and ETF per market with a Korean name — US listings
included (NVDA → 엔비디아). The listing is copied into the ``instruments`` table once
a day and searched there, so typing in the search box never calls Toss.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.engine import Engine

from .broker.base import MARKETS, Broker, Listing
from .db import aware, instruments, now

log = logging.getLogger(__name__)

MAX_AGE = timedelta(hours=20)
KR_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
# Plain stocks first, then funds: "삼성" should show 삼성전자 before KODEX 삼성그룹.
TYPE_ORDER = {"STOCK": 0, "DEPOSITARY_RECEIPT": 1, "FOREIGN_STOCK": 1, "ETF": 2, "FOREIGN_ETF": 2, "ETN": 3}


def yahoo_symbol(symbol: str, market: str) -> str:
    """Toss symbol → the Yahoo ticker TradingAgents prices with."""
    if market in KR_SUFFIX:
        return symbol + KR_SUFFIX[market]
    return symbol.replace(".", "-")          # BRK.B → BRK-B


def name_key(text: str) -> str:
    return "".join(text.split()).lower()


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


class Universe:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.last_error: str | None = None

    async def refresh(self, broker: Broker) -> int:
        """Replace the table with the broker's listing. A market that fails keeps nothing
        half-written: the swap happens only when every market came back."""
        listings: list[Listing] = []
        for market in MARKETS:
            listings.extend(await broker.list_stocks(market))
        if not listings:
            raise RuntimeError(f"{broker.name} listed no instruments")
        stamp = now()
        rows = {l.symbol: {"symbol": l.symbol, "yahoo": yahoo_symbol(l.symbol, l.market), "name": l.name,
                           "name_key": name_key(l.name), "market": l.market,
                           "security_type": l.security_type, "updated_at": stamp}
                for l in listings}
        with self.engine.begin() as conn:
            conn.execute(delete(instruments))
            conn.execute(insert(instruments), list(rows.values()))
        self.last_error = None
        return len(rows)

    async def refresh_if_stale(self, broker: Broker) -> None:
        status = self.status()
        updated = status["updated_at_dt"]
        if status["count"] and updated and now() - updated < MAX_AGE:
            return
        try:
            count = await self.refresh(broker)
            log.info("instrument list refreshed from %s: %d", broker.name, count)
        except Exception as exc:  # noqa: BLE001 - search keeps the old list; the panel shows why
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("instrument list refresh failed: %s", self.last_error)

    def status(self) -> dict:
        with self.engine.connect() as conn:
            count, updated = conn.execute(
                select(func.count(), func.max(instruments.c.updated_at))).one()
        updated = aware(updated)
        return {"count": count, "updated_at_dt": updated, "error": self.last_error}

    def search(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip()
        if not q:
            return []
        key, upper = name_key(q), q.upper()
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(instruments).where(or_(
                    instruments.c.symbol.ilike(_like(upper) + "%", escape="\\"),
                    instruments.c.yahoo.ilike(_like(upper) + "%", escape="\\"),
                    instruments.c.name_key.like("%" + _like(key) + "%", escape="\\"),
                )).limit(500)
            ).mappings().all()

        def rank(row) -> tuple:
            sym, yahoo, nkey = row["symbol"].upper(), row["yahoo"].upper(), row["name_key"]
            if upper in (sym, yahoo) or key == nkey:
                tier = 0
            elif sym.startswith(upper) or yahoo.startswith(upper):
                tier = 1
            elif nkey.startswith(key):
                tier = 2
            else:
                tier = 3
            return (tier, TYPE_ORDER.get(row["security_type"], 4), len(row["name"]), row["symbol"])

        ranked = sorted(rows, key=rank)[:limit]
        return [{"symbol": r["symbol"], "ticker": r["yahoo"], "name": r["name"], "market": r["market"],
                 "security_type": r["security_type"]} for r in ranked]

    def lookup(self, ticker: str) -> dict | None:
        """The listing a Yahoo ticker came from, if known (to name a run started by ticker)."""
        with self.engine.connect() as conn:
            row = conn.execute(select(instruments).where(
                or_(instruments.c.yahoo == ticker.upper(), instruments.c.symbol == ticker.upper()))
            ).mappings().first()
        return {"name": row["name"], "market": row["market"]} if row else None

    def market_of(self, symbol: str) -> str | None:
        """#477: the market of a broker symbol (a bare KRX code), by the primary key - cheap enough for
        api.clean_ticker on every request that carries a code."""
        with self.engine.connect() as conn:
            return conn.execute(select(instruments.c.market).where(instruments.c.symbol == symbol.upper())).scalar()
