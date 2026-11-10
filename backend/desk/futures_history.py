"""KOSPI200 futures investor flows over time (#254): a snapshot of the front month every few minutes
of the session, kept in the database, read back as a day's intraday line and as daily rows.

desk.futures_flow answers "now" only (KIS gives the session's running totals, not a history). So
while the derivatives session is open (08:45-15:45 KST) the server stores the cached view every
``DESK_FUTURES_SNAPSHOT_MIN`` minutes: the investors' net contracts and value (running totals for
the day), the price, open interest and basis. The day's last snapshot is its daily row. History
starts the day this runs - there is no backfill.

Intraday snapshots older than ``KEEP_INTRADAY_DAYS`` are thinned to the last one of their day.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Engine

from .db import futures_snapshots
from .futures_flow import KST, futures_open

log = logging.getLogger(__name__)

KEEP_INTRADAY_DAYS = 60
MAX_DAYS = 250
CLASSES = ("foreign", "individual", "institution")


def row_of(view: dict, now: datetime) -> dict | None:
    """The snapshot row for a futures view (desk.futures_flow), or None when it has no price or
    no investors (a failed fetch is not history)."""
    price, investors = view.get("price") or {}, view.get("investors") or {}
    if price.get("price") is None or not investors:
        return None
    row = {"at": now.astimezone(UTC), "trade_date": now.astimezone(KST).date().isoformat(),
           "code": str(price.get("code") or (view.get("front") or {}).get("code") or ""),
           "price": price.get("price"), "open_interest": price.get("open_interest"),
           "market_basis": price.get("market_basis")}
    for c in CLASSES:
        side = investors.get(c) or {}
        row[f"{c}_contracts"] = side.get("net_contracts")
        row[f"{c}_value_mn"] = side.get("net_value_mn")
    return row


def _point(r) -> dict:
    return {"at": r["at"].astimezone(UTC).isoformat(timespec="seconds") if r["at"].tzinfo else
            r["at"].replace(tzinfo=UTC).isoformat(timespec="seconds"),
            "date": r["trade_date"], "code": r["code"], "price": r["price"], "open_interest": r["open_interest"],
            "market_basis": r["market_basis"],
            "investors": {c: {"net_contracts": r[f"{c}_contracts"], "net_value_mn": r[f"{c}_value_mn"]}
                          for c in CLASSES}}


class FuturesHistory:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(self, view: dict, now: datetime | None = None) -> bool:
        """Store the view as a snapshot when the session is open and the view is whole."""
        now = now or datetime.now(UTC)
        if not futures_open(now):
            return False
        row = row_of(view, now)
        if row is None:
            return False
        with self.engine.begin() as conn:
            conn.execute(insert(futures_snapshots).values(**row))
        return True

    def intraday(self, day: date) -> list[dict]:
        """A trading day's snapshots, oldest first."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(futures_snapshots).where(futures_snapshots.c.trade_date == day.isoformat())
                                .order_by(futures_snapshots.c.at)).mappings().all()
        return [_point(r) for r in rows]

    def daily(self, days: int = 20) -> list[dict]:
        """The last snapshot of each of the latest ``days`` trading days, newest first."""
        days = max(1, min(days, MAX_DAYS))
        last = (select(futures_snapshots.c.trade_date, func.max(futures_snapshots.c.at).label("at"))
                .group_by(futures_snapshots.c.trade_date)
                .order_by(futures_snapshots.c.trade_date.desc()).limit(days).subquery())
        q = (select(futures_snapshots)
             .join(last, (futures_snapshots.c.trade_date == last.c.trade_date) & (futures_snapshots.c.at == last.c.at))
             .order_by(futures_snapshots.c.trade_date.desc(), futures_snapshots.c.id.desc()))
        with self.engine.connect() as conn:
            rows = conn.execute(q).mappings().all()
        out, seen = [], set()
        for r in rows:                       # two snapshots in the same instant: the later id
            if r["trade_date"] not in seen:
                seen.add(r["trade_date"])
                out.append(_point(r))
        return out

    def prune(self, now: datetime | None = None, keep_days: int = KEEP_INTRADAY_DAYS) -> int:
        """Thin days older than ``keep_days`` to their last snapshot; returns rows deleted."""
        cutoff = ((now or datetime.now(UTC)).astimezone(KST).date() - timedelta(days=keep_days)).isoformat()
        last = (select(func.max(futures_snapshots.c.id))
                .where(futures_snapshots.c.trade_date < cutoff)
                .group_by(futures_snapshots.c.trade_date))
        with self.engine.begin() as conn:
            keep = {r[0] for r in conn.execute(last)}
            q = delete(futures_snapshots).where(futures_snapshots.c.trade_date < cutoff)
            if keep:
                q = q.where(futures_snapshots.c.id.notin_(keep))
            return conn.execute(q).rowcount or 0
