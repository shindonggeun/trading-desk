"""Starred tickers per account (#134). The list is ordered by ``position`` (0 = first) and
capped at MAX_FAVORITES. Tickers arrive already cleaned (api.clean_ticker).

Writes of one account take that account's ``users`` row FOR UPDATE first, so two tabs (or two
quick reorders) run one after the other on PostgreSQL; SQLite's single writer does the same.
What still collides (a lock wait that ends in a conflict) raises Conflict for the client to retry."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from .db import favorites, now, users

MAX_FAVORITES = 50


class TooMany(ValueError):
    pass


class Conflict(RuntimeError):
    """Another write of the same account got in the way; the client should read and retry."""


def _lock(conn: Connection, user_id: int) -> None:
    conn.execute(select(users.c.id).where(users.c.id == user_id).with_for_update())


class Favorites:
    def __init__(self, engine: Engine, name_of: Callable[[str], str | None]) -> None:
        self.engine = engine
        self.name_of = name_of   # the listing's display name for a ticker, None when unknown

    def list(self, user_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(favorites.c.ticker, favorites.c.name, favorites.c.position)
                .where(favorites.c.user_id == user_id)
                .order_by(favorites.c.position, favorites.c.ticker)
            ).mappings().all()
        return [{"ticker": r["ticker"], "name": r["name"], "position": i} for i, r in enumerate(rows)]

    def add(self, user_id: int, ticker: str, name: str | None = None) -> bool:
        """Append ``ticker`` at the end. False when it was already there (left as is).
        Raises TooMany at the cap."""
        name = _name(name) or self.name_of(ticker)
        try:
            with self.engine.begin() as conn:
                _lock(conn, user_id)
                mine = favorites.c.user_id == user_id
                if conn.execute(select(favorites.c.ticker).where(mine, favorites.c.ticker == ticker)).first():
                    return False
                count, last = conn.execute(
                    select(func.count(), func.max(favorites.c.position)).where(mine)).one()
                if count >= MAX_FAVORITES:
                    raise TooMany(f"at most {MAX_FAVORITES} favorites")
                conn.execute(favorites.insert().values(
                    user_id=user_id, ticker=ticker, name=name,
                    position=0 if last is None else last + 1, created_at=now()))
        except IntegrityError:
            return False   # a concurrent add of the same ticker won; the primary key kept one row
        except OperationalError as exc:
            raise Conflict("favorites changed meanwhile") from exc
        return True

    def replace(self, user_id: int, items: list[tuple[str, str | None]]) -> list[dict]:
        """Make the list exactly ``items`` (ticker, name) in that order — reorder, bulk import.
        A repeated ticker keeps its first place. Rows that stay keep their name and created_at
        unless a new name is given."""
        seen: dict[str, str | None] = {}
        for ticker, name in items:
            seen.setdefault(ticker, _name(name))
        if len(seen) > MAX_FAVORITES:
            raise TooMany(f"at most {MAX_FAVORITES} favorites")
        # listing names for tickers that bring none and had none: looked up before the write
        # transaction, which then holds one connection only
        named = {f["ticker"] for f in self.list(user_id) if f["name"]}
        listed = {t: self.name_of(t) for t, name in seen.items() if not name and t not in named}
        try:
            with self.engine.begin() as conn:
                _lock(conn, user_id)
                mine = favorites.c.user_id == user_id
                old = {r["ticker"]: r for r in conn.execute(
                    select(favorites.c.ticker, favorites.c.name, favorites.c.created_at).where(mine)).mappings()}
                conn.execute(delete(favorites).where(mine))
                stamp = now()
                rows = [{
                    "user_id": user_id, "ticker": ticker,
                    "name": name or (old[ticker]["name"] if ticker in old else None) or listed.get(ticker),
                    "position": i,
                    "created_at": old[ticker]["created_at"] if ticker in old else stamp,
                } for i, (ticker, name) in enumerate(seen.items())]
                if rows:
                    conn.execute(favorites.insert(), rows)
        except (IntegrityError, OperationalError) as exc:
            raise Conflict("favorites changed meanwhile") from exc
        return self.list(user_id)

    def remove(self, user_id: int, ticker: str) -> bool:
        with self.engine.begin() as conn:
            return bool(conn.execute(
                delete(favorites).where(favorites.c.user_id == user_id, favorites.c.ticker == ticker)).rowcount)


def _name(name: str | None) -> str | None:
    """Display text only: control and format characters (bidi overrides among them) dropped."""
    name = "".join(c for c in (name or "") if unicodedata.category(c) not in ("Cc", "Cf")).strip()
    return name[:200] or None
