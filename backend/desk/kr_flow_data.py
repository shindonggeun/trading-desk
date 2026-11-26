"""Read pre-existing public market snapshots for the Korean stock flow board, plus #916 KIS per-stock rows."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from math import isfinite
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from . import krx, market_bus, market_flow, shared_cache
from .db import instruments, krx_daily, krx_days
from .kr_flow_board import Publication, RankingDay, Turnover, build_board, ranked_tickers, sessions
from .krx_etp import isin
from .universe import KR_SUFFIX

if TYPE_CHECKING:
    from .kis_stock_flow import KisStockFlow

# A general KRX collector may drop malformed rows. These keys are written only by the
# whole-publication validator, before a day is eligible for a market-wide ranking.
STOCK_APIS = {"kospi": "kr_flow_board_stk_v1", "kosdaq": "kr_flow_board_ksq_v1"}
INDEX_APIS = {"kospi": "kospi_dd_trd", "kosdaq": "kosdaq_dd_trd"}
INDEX_NAMES = {"kospi": {"KOSPI", "코스피"}, "kosdaq": {"KOSDAQ", "코스닥"}}
MARKET_NAMES = {"kospi": {"KOSPI", "유가증권", "유가증권시장"}, "kosdaq": {"KOSDAQ", "코스닥", "코스닥시장"}}
MAX_LISTINGS = 5000
_CODE = re.compile(r"[0-9][0-9A-Z]{5}")
_WON = re.compile(r"[0-9]{1,16}|[0-9]{1,3}(?:,[0-9]{3}){1,5}")
_INDEX_RATE = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")


def _stock_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if _CODE.fullmatch(value):
        return value
    if len(value) == 12 and value.startswith("KR7") and _CODE.fullmatch(value[3:9]):
        code = value[3:9]
        if value == isin(code):
            return code
    return None


def parsed_stock(payload: object, market: str, day: date) -> Turnover | None:
    if not isinstance(payload, dict) or payload.get("BAS_DD") not in (
        day.strftime("%Y%m%d"), day.isoformat(),
    ):
        return None
    code = _stock_code(payload.get("ISU_CD"))
    board = payload.get("MKT_NM")
    if code is None or not isinstance(board, str) or board.upper() not in MARKET_NAMES[market]:
        return None
    raw = payload.get("ACC_TRDVAL")
    amount = int(raw.replace(",", "")) if isinstance(raw, str) and _WON.fullmatch(raw) else None
    title = payload.get("ISU_NM")
    name = title if isinstance(title, str) and 0 < len(title) <= 120 and title.isprintable() else None
    return Turnover(code, market, day, "krx_open_api", amount, name)


def _turnover(row, market: str) -> Turnover | None:
    record = parsed_stock(row.payload, market, row.bas_dd)
    return record if record is not None and record.ticker == _stock_code(row.code) else None


def publication(engine: Engine, market: str, dates: Sequence[date], codes: Sequence[str]) -> Publication:
    """Use a batch read for history and one marked, complete market day for the ranking."""
    api = STOCK_APIS[market]
    base = select(krx_daily.c.bas_dd, krx_daily.c.code, krx_daily.c.payload).where(krx_daily.c.api == api)
    matches = {value for code in codes if (value := _stock_code(code)) is not None}
    match_keys = matches | {isin(code) for code in matches}
    try:
        with engine.connect() as conn:
            markers = conn.execute(select(krx_days.c.bas_dd, krx_days.c.rows).where(
                krx_days.c.api == api, krx_days.c.bas_dd.in_(dates),
            ).order_by(krx_days.c.bas_dd.desc())).all()
            if not markers:
                return Publication()
            turnovers = []
            if match_keys:
                history = conn.execute(base.where(krx_daily.c.bas_dd.in_(dates), krx_daily.c.code.in_(match_keys)))
                turnovers = [item for row in history if (item := _turnover(row, market)) is not None]
            for marker in markers:
                if not 0 < marker.rows <= MAX_LISTINGS:
                    continue
                rows = conn.execute(base.where(krx_daily.c.bas_dd == marker.bas_dd).limit(marker.rows + 1)).all()
                ranked = [item for row in rows if (item := _turnover(row, market)) is not None]
                if (len(ranked) != marker.rows or len(rows) != marker.rows
                        or len({item.ticker for item in ranked}) != marker.rows
                        or any(item.value_krw is None for item in ranked)):
                    continue
                turnovers = [item for item in turnovers if item.as_of != marker.bas_dd] + ranked
                return Publication(turnover=turnovers, rankings=[RankingDay(market, marker.bas_dd, "krx_open_api")])
            return Publication(turnover=turnovers, rankings=[])
    except SQLAlchemyError:
        return Publication()


def _market_flow(market: str, dates: Sequence[date], page: dict) -> dict | None:
    if (page.get("market") != market.upper() or page.get("interval") != "1d" or page.get("error")
            or not isinstance(page.get("source"), str) or not isinstance(page.get("records"), list)):
        return None
    fetched_at = market_flow._parse_time(page.get("fetched_at"))
    if fetched_at is None:
        return None
    fetched_day = fetched_at.astimezone(market_flow.KST).date()
    sessions = {day.isoformat() for day in dates}
    for row in page["records"]:
        if (not market_flow._valid_flow_record(row, "1d", fetched_day) or row["provisional"]
                or row["date"] not in sessions):
            continue
        return {"as_of": row["date"], "source": row.get("source") or page["source"],
                "published_at": row["updated_at"], "unit": "KRW", "investors": row["investors"]}
    return None


def index_snapshot(engine: Engine, market: str, dates: Sequence[date]) -> dict | None:
    """Read the close and published change for the same completed index session."""
    try:
        with engine.connect() as conn:
            row = conn.execute(select(krx_daily.c.bas_dd, krx_daily.c.payload).where(
                krx_daily.c.api == INDEX_APIS[market], krx_daily.c.code.in_(INDEX_NAMES[market]),
                krx_daily.c.bas_dd.in_(dates),
            ).order_by(krx_daily.c.bas_dd.desc(), krx_daily.c.code).limit(1)).first()
    except SQLAlchemyError:
        return None
    if row is None or not isinstance(row.payload, dict):
        return None
    level = krx.num(row.payload.get("CLSPRC_IDX"))
    if level is None or not isfinite(level) or level <= 0:
        return None
    as_of = row.bas_dd.isoformat()
    change = None
    raw_change = row.payload.get("FLUC_RT")
    if (row.payload.get("BAS_DD") in (row.bas_dd.strftime("%Y%m%d"), as_of)
            and isinstance(raw_change, str) and _INDEX_RATE.fullmatch(raw_change)):
        # Use the percentage KRX published on this row, without inferring it from other prices.
        change = float(raw_change)
    return {"as_of": as_of, "source": "krx_open_api", "published_at": None, "unit": "index_points",
            "value": level, "change_pct": change if change is not None and isfinite(change) else None}


async def market_summary(market: str, dates: Sequence[date], now: datetime, index: dict | None) -> dict:
    """Reuse already observed market-wide flow. A cache miss never triggers a provider request."""
    key = (market.upper(), "1d")
    hit = market_flow._shared_flow(await shared_cache.aget(market_flow.SHARED_NS, key), *key, now)
    if hit is None:
        sub = market_bus.Subscription(("flow",), frozenset(), owner=False)
        event = next((event for event in market_bus.BUS.snapshot(sub) if event["key"] == ":".join(key)), None)
        page = event["payload"] if event else {}
    else:
        page = hit[0]
    flow = _market_flow(market, dates, page)
    if flow and index:
        status = "available" if flow["as_of"] == index["as_of"] == dates[-1].isoformat() else "stale"
    else:
        status = "partial" if flow or index else "source_unavailable"
    return {"status": status,
            "scope": "market", "flow": flow, "index": index}


def _sections(engine: Engine, market: str, dates: Sequence[date], favorites: Sequence[dict]):
    suffix = KR_SUFFIX[market.upper()]
    raw = [favorite["ticker"].strip().upper() for favorite in favorites]
    codes = {value for ticker in raw if ticker.endswith(suffix)
             and (value := _stock_code(ticker.removesuffix(suffix))) is not None}
    bare = {value for ticker in raw if (value := _stock_code(ticker)) == ticker}
    confirmed = set()
    if bare:
        try:
            with engine.connect() as conn:
                rows = conn.execute(select(instruments.c.symbol).where(
                    instruments.c.symbol.in_(bare), instruments.c.market == market.upper(),
                ))
                confirmed = set(rows.scalars())
        except SQLAlchemyError:
            # An unknown bare code cannot be assigned to a market without the listing.
            pass
    sources = publication(engine, market, dates, sorted(codes | confirmed))
    return sources, dict.fromkeys(confirmed, market), index_snapshot(engine, market, dates)


def _favorite_codes(favorites: Sequence[dict], market: str, favorite_markets: Mapping[str, str]) -> list[str]:
    """The account's codes in this market, in its own order (bare codes only once the listing confirmed them)."""
    suffix = KR_SUFFIX[market.upper()]
    codes = []
    for favorite in favorites:
        raw = favorite["ticker"].strip().upper()
        code = raw.removesuffix(suffix) if raw.endswith(suffix) else raw if raw in favorite_markets else None
        if code is not None and _CODE.fullmatch(code):
            codes.append(code)
    return codes


async def _with_stock_flow(
    stock_flow: KisStockFlow, sources: Publication, market: str, latest: date, now: datetime,
    favorites: Sequence[dict], favorite_markets: Mapping[str, str],
) -> Publication:
    """#916 KIS per-stock rows for the favorites first, then the ranking's top stocks."""
    ranked = [ticker[:6] for ticker in ranked_tickers(sources, market, latest, now)]
    codes = list(dict.fromkeys(_favorite_codes(favorites, market, favorite_markets) + ranked))
    stock = await stock_flow.collect(codes, market, latest)
    if stock is None:
        return sources
    turnover, foreign_flow, ownership = stock
    if turnover is not None:
        turnover = [*(sources.turnover or ()), *turnover]
    return replace(sources, turnover=sources.turnover if turnover is None else turnover,
                   foreign_flow=foreign_flow, ownership=ownership)


async def read(engine: Engine, market: str, days: int, favorites: Sequence[dict], now: datetime,
               stock_flow: KisStockFlow | None = None) -> dict:
    dates = sessions(now)
    sources, favorite_markets, index = await asyncio.to_thread(_sections, engine, market, dates, favorites)
    if stock_flow is not None:
        sources = await _with_stock_flow(stock_flow, sources, market, dates[-1], now, favorites, favorite_markets)
    result = build_board(market, dates[-days:], publication=sources, now=now,
                         favorites=favorites, favorite_markets=favorite_markets)
    result["market_summary"] = await market_summary(market, dates, now, index)
    return result
