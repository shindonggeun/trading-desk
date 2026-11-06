"""Sector tabs per account (#156): named, ordered groups of tickers for the sector screen (#157).

An account with no sectors gets DEFAULTS on its first read (so deleting every sector brings the
defaults back next time). The screen reads before it writes; a create on an account that was never
read starts it with that one sector instead. Writes take the account's ``users`` row first, like favorites (#134), so
two tabs editing at once run one after the other; a collision that still happens is Conflict (409).
Tickers arrive cleaned (api.clean_ticker); names are display text only.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from .db import now, sector_groups
from .favorites import Conflict, TooMany, _lock, _name

MAX_SECTORS = 20
MAX_TICKERS = 30
NAME_MAX = 40


class NotFound(LookupError):
    pass


class Duplicate(ValueError):
    pass


def _t(ticker: str, name: str) -> dict:
    return {"ticker": ticker, "name": name}


DEFAULTS: list[tuple[str, list[dict]]] = [
    ("반도체(국내)", [_t("005930.KS", "삼성전자"), _t("000660.KS", "SK하이닉스"), _t("042700.KS", "한미반도체"),
                    _t("403870.KQ", "HPSP"), _t("058470.KQ", "리노공업")]),
    ("반도체(미국)", [_t("NVDA", "엔비디아"), _t("AMD", "AMD"), _t("AVGO", "브로드컴"), _t("TSM", "TSMC"),
                    _t("MU", "마이크론"), _t("ASML", "ASML"), _t("QCOM", "퀄컴")]),
    ("빅테크", [_t("AAPL", "애플"), _t("MSFT", "마이크로소프트"), _t("GOOGL", "알파벳"), _t("AMZN", "아마존"),
              _t("META", "메타"), _t("TSLA", "테슬라"), _t("NVDA", "엔비디아")]),
    ("소프트웨어", [_t("MSFT", "마이크로소프트"), _t("ORCL", "오라클"), _t("CRM", "세일즈포스"), _t("ADBE", "어도비"),
               _t("NOW", "서비스나우"), _t("PLTR", "팔란티어"), _t("SNOW", "스노우플레이크")]),
    ("2차전지", [_t("373220.KS", "LG에너지솔루션"), _t("006400.KS", "삼성SDI"), _t("247540.KQ", "에코프로비엠"),
              _t("086520.KQ", "에코프로"), _t("003670.KS", "포스코퓨처엠")]),
    ("경기방어", [_t("JNJ", "존슨앤드존슨"), _t("PG", "P&G"), _t("KO", "코카콜라"), _t("PEP", "펩시코"),
              _t("WMT", "월마트"), _t("NEE", "넥스트에라 에너지"), _t("UNH", "유나이티드헬스")]),
    ("금융", [_t("JPM", "JP모건"), _t("BAC", "뱅크오브아메리카"), _t("GS", "골드만삭스"), _t("MS", "모건스탠리"),
            _t("105560.KS", "KB금융"), _t("055550.KS", "신한지주")]),
    ("에너지", [_t("XOM", "엑슨모빌"), _t("CVX", "셰브론"), _t("COP", "코노코필립스"), _t("OXY", "옥시덴탈"),
             _t("SLB", "SLB")]),
]


def clean_name(name: str | None) -> str:
    text = (_name(name) or "")[:NAME_MAX].strip()
    if not text:
        raise ValueError("sector name is empty")
    return text


def clean_tickers(rows: list[tuple[str, str | None]], name_of=None) -> list[dict]:
    """[(cleaned ticker, display name)] -> [{ticker, name}], first place kept for a repeat, capped.
    A missing name comes from ``name_of`` (the listing) and stays null when the listing has none."""
    seen: dict[str, str | None] = {}
    for ticker, name in rows:
        seen.setdefault(ticker, _name(name))
    if len(seen) > MAX_TICKERS:
        raise TooMany(f"at most {MAX_TICKERS} tickers in a sector")
    return [{"ticker": t, "name": n or (name_of(t) if name_of else None)} for t, n in seen.items()]


def _row(r) -> dict:
    return {"id": r["id"], "name": r["name"], "position": r["position"], "tickers": list(r["tickers"] or [])}


class Sectors:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _rows(self, conn, user_id: int) -> list[dict]:
        rows = conn.execute(select(sector_groups).where(sector_groups.c.user_id == user_id)
                            .order_by(sector_groups.c.position, sector_groups.c.id)).mappings().all()
        return [{**_row(r), "position": i} for i, r in enumerate(rows)]

    def list(self, user_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = self._rows(conn, user_id)
        if rows:
            return rows
        try:
            return self._seed(user_id)
        except Conflict:
            # two first reads at once (SQLite has no row lock): the other one seeded - read it
            with self.engine.connect() as conn:
                return self._rows(conn, user_id)

    def _seed(self, user_id: int) -> list[dict]:
        try:
            with self.engine.begin() as conn:
                _lock(conn, user_id)
                if not conn.execute(select(func.count()).select_from(sector_groups)
                                    .where(sector_groups.c.user_id == user_id)).scalar_one():
                    stamp = now()
                    conn.execute(sector_groups.insert(), [
                        {"user_id": user_id, "name": name, "position": i, "tickers": tickers,
                         "created_at": stamp, "updated_at": stamp} for i, (name, tickers) in enumerate(DEFAULTS)])
                return self._rows(conn, user_id)
        except (IntegrityError, OperationalError) as exc:
            raise Conflict("sectors changed meanwhile") from exc

    def create(self, user_id: int, name: str, tickers: list[dict]) -> dict:
        try:
            with self.engine.begin() as conn:
                _lock(conn, user_id)
                mine = sector_groups.c.user_id == user_id
                count, last = conn.execute(select(func.count(), func.max(sector_groups.c.position)).where(mine)).one()
                if count >= MAX_SECTORS:
                    raise TooMany(f"at most {MAX_SECTORS} sectors")
                if conn.execute(select(sector_groups.c.id).where(mine, sector_groups.c.name == name)).first():
                    raise Duplicate(f"a sector named {name} exists")
                stamp = now()
                new_id = conn.execute(sector_groups.insert().values(
                    user_id=user_id, name=name, position=0 if last is None else last + 1, tickers=tickers,
                    created_at=stamp, updated_at=stamp)).inserted_primary_key[0]
                return next(r for r in self._rows(conn, user_id) if r["id"] == new_id)
        except IntegrityError as exc:
            raise Duplicate(f"a sector named {name} exists") from exc
        except OperationalError as exc:
            raise Conflict("sectors changed meanwhile") from exc

    def update(self, user_id: int, sector_id: int, name: str | None, tickers: list[dict] | None) -> dict:
        values: dict = {"updated_at": now()}
        if name is not None:
            values["name"] = name
        if tickers is not None:
            values["tickers"] = tickers
        try:
            with self.engine.begin() as conn:
                _lock(conn, user_id)
                mine = sector_groups.c.user_id == user_id
                # ownership first: another account's id is "not found" whatever the body says
                if not conn.execute(select(sector_groups.c.id).where(mine, sector_groups.c.id == sector_id)).first():
                    raise NotFound(f"no sector {sector_id}")
                if name is not None and conn.execute(select(sector_groups.c.id).where(
                        mine, sector_groups.c.name == name, sector_groups.c.id != sector_id)).first():
                    raise Duplicate(f"a sector named {name} exists")
                if not conn.execute(update(sector_groups).where(mine, sector_groups.c.id == sector_id)
                                    .values(**values)).rowcount:
                    raise NotFound(f"no sector {sector_id}")
                return next(r for r in self._rows(conn, user_id) if r["id"] == sector_id)
        except IntegrityError as exc:
            raise Duplicate(f"a sector named {name} exists") from exc
        except OperationalError as exc:
            raise Conflict("sectors changed meanwhile") from exc

    def remove(self, user_id: int, sector_id: int) -> bool:
        try:
            with self.engine.begin() as conn:
                _lock(conn, user_id)
                return bool(conn.execute(delete(sector_groups).where(
                    sector_groups.c.user_id == user_id, sector_groups.c.id == sector_id)).rowcount)
        except OperationalError as exc:
            raise Conflict("sectors changed meanwhile") from exc

    def reorder(self, user_id: int, ids: list[int]) -> list[dict]:
        """``ids`` must be exactly the account's sectors, in the new order."""
        try:
            with self.engine.begin() as conn:
                _lock(conn, user_id)
                mine = sector_groups.c.user_id == user_id
                have = set(conn.execute(select(sector_groups.c.id).where(mine)).scalars())
                if len(ids) != len(set(ids)) or set(ids) != have:
                    raise ValueError("ids must list every sector of the account once")
                for i, sector_id in enumerate(ids):
                    conn.execute(update(sector_groups).where(mine, sector_groups.c.id == sector_id).values(position=i))
                return self._rows(conn, user_id)
        except OperationalError as exc:
            raise Conflict("sectors changed meanwhile") from exc
