"""Starred tickers per account (#134). The list is ordered by ``position`` (0 = first) and
capped at MAX_FAVORITES. Tickers arrive already cleaned (api.clean_ticker)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .db import favorites, now

MAX_FAVORITES = 50


class TooMany(ValueError):
    pass


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
        with self.engine.begin() as conn:
            mine = favorites.c.user_id == user_id
            old = {r["ticker"]: r for r in conn.execute(
                select(favorites.c.ticker, favorites.c.name, favorites.c.created_at).where(mine)).mappings()}
            conn.execute(delete(favorites).where(mine))
            stamp = now()
            rows = [{
                "user_id": user_id, "ticker": ticker,
                "name": name or (old[ticker]["name"] if ticker in old else None) or self.name_of(ticker),
                "position": i,
                "created_at": old[ticker]["created_at"] if ticker in old else stamp,
            } for i, (ticker, name) in enumerate(seen.items())]
            if rows:
                conn.execute(favorites.insert(), rows)
        return self.list(user_id)

    def remove(self, user_id: int, ticker: str) -> bool:
        with self.engine.begin() as conn:
            return bool(conn.execute(
                delete(favorites).where(favorites.c.user_id == user_id, favorites.c.ticker == ticker)).rowcount)


def _name(name: str | None) -> str | None:
    name = (name or "").strip()
    return name[:200] or None
