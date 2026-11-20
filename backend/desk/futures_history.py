"""KOSPI200 futures investor flows over time (#254): a snapshot of the front month every few minutes
of the session, kept in the database, read back as a day's intraday line and as daily rows.

desk.futures_flow answers "now" only (KIS gives the session's running totals, not a history). So
while the derivatives session is open (08:45-15:45 KST) the server stores the cached view every
``DESK_FUTURES_SNAPSHOT_MIN`` minutes: the investors' net contracts and value (running totals for
the day), the price, open interest and basis. The day's last snapshot is its daily row. History
starts the day this runs - there is no backfill.

Intraday snapshots older than ``KEEP_INTRADAY_DAYS`` are thinned to the last one of their day.

#677 the pre-market (``PREOPEN`` 08:00 ~ 08:45, weekdays): a spot-only row a minute - Toss' today record
already carries the trades before the bell (NXT pre-market, the pre-open off-hours; #673) - with the
futures columns left empty (before 08:45 KIS' futures figures are the day before's: their reset at the
open would read as turns). ``intraday`` leaves those rows out, so the bars, turns, shifts and warnings
read the session as before; ``premarket`` reads them (with the session's first quarter hour to 09:00).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Engine

from .db import futures_snapshots
from .futures_flow import KST, futures_open

log = logging.getLogger(__name__)

KEEP_INTRADAY_DAYS = 60
OPEN, CLOSE = time(8, 45), time(15, 45)
PREOPEN = time(8, 0)             # #677 the spot pre-market snapshots from here
SPOT_OPEN = time(9, 0)           # ``premarket`` reads up to the spot bell
CLOSING_UNTIL = time(16, 0)      # the closing snapshot is taken once between CLOSE and this
CLOSING_AT = time(15, 46)
MAX_SLEEP_SEC = 3600.0           # outside the session the loop still wakes hourly (clock changes, restarts)
MAX_DAYS = 250
CLASSES = ("foreign", "individual", "institution")
SPOT_MARKETS = ("kospi", "kosdaq")
SPOT_CLASSES = ("foreign", "institution", "individual")
SPOT_SOURCE = {"foreign": "foreigner", "institution": "institution", "individual": "individual"}   # Toss keys


def spot_cols(spot: dict | None, today: str) -> dict:
    """#359: today's spot investor net per market (desk.market_flow 1d views keyed kospi/kosdaq),
    in 100 million KRW; a record of another day (before the open) is not today's flow."""
    out = {}
    for m in SPOT_MARKETS:
        record = (((spot or {}).get(m) or {}).get("records") or [None])[0] or {}
        same_day = record.get("date") == today
        for c in SPOT_CLASSES:
            net = ((record.get("investors") or {}).get(SPOT_SOURCE[c]) or {}).get("net") if same_day else None
            out[f"{m}_{c}_100m"] = round(net / 1e8, 2) if isinstance(net, (int, float)) else None
    return out


def row_of(view: dict, now: datetime, spot: dict | None = None) -> dict | None:
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
    row.update(spot_cols(spot, row["trade_date"]))
    return row


def premarket_window(now: datetime) -> bool:
    """#677 weekdays PREOPEN ~ OPEN KST: the spot-only snapshots."""
    local = now.astimezone(KST)
    return local.weekday() < 5 and PREOPEN <= local.time() < OPEN


def spot_row(now: datetime, spot: dict | None) -> dict | None:
    """#677 a pre-market row: the spot columns only, None when no market has today's figures yet."""
    row = {"at": now.astimezone(UTC), "trade_date": now.astimezone(KST).date().isoformat(), "code": "",
           "price": None, "open_interest": None, "market_basis": None,
           **{f"{c}_{k}": None for c in CLASSES for k in ("contracts", "value_mn")}}
    cols = spot_cols(spot, row["trade_date"])
    if all(v is None for v in cols.values()):
        return None
    row.update(cols)
    return row


def _point(r) -> dict:
    return {"at": r["at"].astimezone(UTC).isoformat(timespec="seconds") if r["at"].tzinfo else
            r["at"].replace(tzinfo=UTC).isoformat(timespec="seconds"),
            "date": r["trade_date"], "code": r["code"], "price": r["price"], "open_interest": r["open_interest"],
            "market_basis": r["market_basis"],
            "investors": {c: {"net_contracts": r[f"{c}_contracts"], "net_value_mn": r[f"{c}_value_mn"]}
                          for c in CLASSES},
            "spot": {m: {c: r.get(f"{m}_{c}_100m") for c in SPOT_CLASSES} for m in SPOT_MARKETS}}


def interval_min(setting: float) -> float:
    """0 = off; anything else at least a minute (the view is cached 30 s, KIS is rate limited)."""
    return 0.0 if setting <= 0 else max(1.0, float(setting))


def closing_window(now: datetime) -> bool:
    """Weekdays 15:45-16:00 KST: the day's closing snapshot belongs here."""
    local = now.astimezone(KST)
    return local.weekday() < 5 and CLOSE < local.time() < CLOSING_UNTIL


def seconds_to_next(now: datetime, every_min: float) -> float:
    """Until the next snapshot on the wall clock: every ``every_min`` minutes on the pre-market's grid from
    08:00 (#677) up to 08:45, then on the session's grid from 08:45 (08:45, 09:00, ...), then the closing
    snapshot at 15:46, then the next weekday's 08:00 - capped at MAX_SLEEP_SEC."""
    local = now.astimezone(KST)
    day = local.date()
    pre_at = datetime.combine(day, PREOPEN, KST)
    open_at = datetime.combine(day, OPEN, KST)
    close_at = datetime.combine(day, CLOSE, KST)
    if local.weekday() < 5 and local < pre_at:
        target = pre_at
    elif local.weekday() < 5 and local <= close_at:
        base = pre_at if local < open_at else open_at
        step = every_min * 60
        n = math.floor((local - base).total_seconds() / step) + 1
        target = base + timedelta(seconds=n * step)
        if base == pre_at and target > open_at:
            target = open_at
        if target > close_at:
            target = datetime.combine(day, CLOSING_AT, KST)
    elif local.weekday() < 5 and local < datetime.combine(day, CLOSING_AT, KST):
        target = datetime.combine(day, CLOSING_AT, KST)
    else:
        nxt = day + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        target = datetime.combine(nxt, PREOPEN, KST)
    return max(1.0, min((target - local).total_seconds(), MAX_SLEEP_SEC))


class FuturesHistory:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(self, view: dict, now: datetime | None = None, spot: dict | None = None) -> bool:
        """Store the view as a snapshot when the session is open (or in its closing window, for the
        day's final totals) and the view is whole."""
        now = now or datetime.now(UTC)
        if not (futures_open(now) or closing_window(now)):
            return False
        row = row_of(view, now, spot)
        if row is None:
            return False
        with self.engine.begin() as conn:
            conn.execute(insert(futures_snapshots).values(**row))
        return True

    def record_spot(self, spot: dict | None, now: datetime | None = None) -> bool:
        """#677 a pre-market snapshot (spot only) inside ``premarket_window``."""
        now = now or datetime.now(UTC)
        if not premarket_window(now):
            return False
        row = spot_row(now, spot)
        if row is None:
            return False
        with self.engine.begin() as conn:
            conn.execute(insert(futures_snapshots).values(**row))
        return True

    def _day(self, day: date, start: time | None, end: time | None) -> list[dict]:
        q = select(futures_snapshots).where(futures_snapshots.c.trade_date == day.isoformat())
        if start is not None:
            q = q.where(futures_snapshots.c.at >= datetime.combine(day, start, KST).astimezone(UTC))
        if end is not None:
            q = q.where(futures_snapshots.c.at < datetime.combine(day, end, KST).astimezone(UTC))
        with self.engine.connect() as conn:
            rows = conn.execute(q.order_by(futures_snapshots.c.at)).mappings().all()
        return [_point(r) for r in rows]

    def intraday(self, day: date) -> list[dict]:
        """A trading day's snapshots from the 08:45 open, oldest first (#677 the pre-market rows left out)."""
        return self._day(day, OPEN, None)

    def premarket(self, day: date) -> list[dict]:
        """#677 a day's snapshots before the spot bell (08:00 ~ 09:00): the pre-market's spot-only rows and the
        futures session's first quarter hour."""
        return self._day(day, None, SPOT_OPEN)

    def daily(self, days: int = 20) -> list[dict]:
        """The last futures snapshot of each of the latest ``days`` trading days, newest first."""
        days = max(1, min(days, MAX_DAYS))
        last = (select(futures_snapshots.c.trade_date, func.max(futures_snapshots.c.at).label("at"))
                .where(futures_snapshots.c.price.isnot(None))     # #677 not the pre-market's spot-only rows
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
        last = (select(func.max(futures_snapshots.c.id))      # #677 a day of spot-only rows goes whole
                .where(futures_snapshots.c.trade_date < cutoff, futures_snapshots.c.price.isnot(None))
                .group_by(futures_snapshots.c.trade_date))
        with self.engine.begin() as conn:
            keep = {r[0] for r in conn.execute(last)}
            q = delete(futures_snapshots).where(futures_snapshots.c.trade_date < cutoff)
            if keep:
                q = q.where(futures_snapshots.c.id.notin_(keep))
            return conn.execute(q).rowcount or 0
