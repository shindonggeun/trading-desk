"""Read-model assembly for confirmed Korean stock-flow observations (#916)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TypeVar

from . import market_calendar
from .universe import KR_SUFFIX

MAX_DAYS = 22
RANK_LIMIT = 50
_VENUE = "krx"
_CURRENCY = "KRW"
_SUFFIX = {market.lower(): suffix for market, suffix in KR_SUFFIX.items()}
_TICKER = re.compile(r"([0-9][0-9A-Z]{5})(\.KS|\.KQ)?")


@dataclass(frozen=True)
class Turnover:
    ticker: str
    market: str
    as_of: date
    source: str
    value_krw: int | None
    name: str | None = None
    venue: str = _VENUE
    unit: str = _CURRENCY
    published_at: datetime | None = None
    confirmed: bool = True


@dataclass(frozen=True)
class ForeignFlow:
    ticker: str
    market: str
    as_of: date
    source: str
    buy_krw: int | None
    sell_krw: int | None
    net_krw: int | None = None
    venue: str = _VENUE
    unit: str = _CURRENCY
    published_at: datetime | None = None
    confirmed: bool = True


@dataclass(frozen=True)
class Ownership:
    ticker: str
    market: str
    as_of: date
    source: str
    ratio_pct: float | None
    published_at: datetime | None = None
    confirmed: bool = True


@dataclass(frozen=True)
class RankingDay:
    """Evidence that the stated turnover source covers a complete market on this date."""

    market: str
    as_of: date
    source: str
    published_at: datetime | None = None
    confirmed: bool = True


@dataclass(frozen=True)
class Publication:
    """Normalized snapshots. None means the series is unavailable, empty means no observations."""

    turnover: Sequence[Turnover] | None = None
    foreign_flow: Sequence[ForeignFlow] | None = None
    ownership: Sequence[Ownership] | None = None
    rankings: Sequence[RankingDay] | None = None


def sessions(now: datetime, days: int = MAX_DAYS) -> tuple[date, ...]:
    """Last closed regular KRX sessions in chronological order."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError("days must be between 1 and 22")
    day = market_calendar.local_today("kr", now)
    if not market_calendar.is_open("kr", day) or now < market_calendar.session_close("kr", day):
        day = market_calendar.previous_open("kr", day)
    result = [day]
    for _ in range(days - 1):
        result.append(market_calendar.previous_open("kr", result[-1]))
    return tuple(reversed(result))


def _ticker(raw: str, market: str, *, allow_bare: bool = True) -> str | None:
    match = _TICKER.fullmatch(raw.strip().upper())
    if not match:
        return None
    code, suffix = match.groups()
    if suffix == _SUFFIX[market] or (allow_bare and suffix is None):
        return code + _SUFFIX[market]
    return None


_Row = TypeVar("_Row", Turnover, ForeignFlow, Ownership)


def _publication_status(row: Turnover | ForeignFlow | Ownership | RankingDay, now: datetime) -> str:
    at = row.published_at
    if not row.confirmed or (at is not None and (at.utcoffset() is None or at > now)):
        return "not_published"
    return "available"


def _money_status(row: Turnover | ForeignFlow, now: datetime) -> str:
    status = _publication_status(row, now)
    if status != "available":
        return status
    if row.venue != _VENUE:
        return "scope_mismatch"
    return "available" if row.unit == _CURRENCY else "unit_mismatch"


def _amount(value: int | None) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _empty_status(rows: Sequence | None) -> str:
    return "source_unavailable" if rows is None else "no_data"


def _metadata(row: Turnover | ForeignFlow | Ownership | RankingDay | None, status: str) -> dict:
    at = row.published_at if row else None
    return {
        "status": status,
        "as_of": row.as_of.isoformat() if row else None,
        "source": row.source if row else None,
        "published_at": at.isoformat() if at and at.utcoffset() is not None and status != "not_published" else None,
    }


def _turnover(row: Turnover | None, now: datetime, empty: str) -> dict:
    status = _money_status(row, now) if row else empty
    value = _amount(row.value_krw) if row and status == "available" else None
    if status == "available" and value is None:
        status = "no_data"
    return {**_metadata(row, status), "venue": row.venue if row else None, "unit": row.unit if row else None,
            "value_krw": value}


def _foreign_flow(row: ForeignFlow | None, now: datetime, empty: str) -> dict:
    status = _money_status(row, now) if row else empty
    buy = sell = net = None
    if row and status == "available":
        buy, sell = _amount(row.buy_krw), _amount(row.sell_krw)
        net = row.net_krw if type(row.net_krw) is int else None
        if buy is not None and sell is not None:
            net = buy - sell
        if buy is None or sell is None:
            status = "partial" if any(value is not None for value in (buy, sell, net)) else "no_data"
    return {**_metadata(row, status), "venue": row.venue if row else None, "unit": row.unit if row else None,
            "buy_krw": buy, "sell_krw": sell, "net_krw": net}


def _ownership(row: Ownership | None, now: datetime, empty: str) -> dict:
    status = _publication_status(row, now) if row else empty
    ratio = row.ratio_pct if row and status == "available" else None
    if status == "available" and (type(ratio) not in (int, float) or not 0 <= ratio <= 100):
        ratio, status = None, "no_data"
    return {**_metadata(row, status), "unit": "percent", "ratio_pct": ratio}


def _ratios(turnover: dict, foreign_flow: dict) -> dict:
    total, buy, sell = turnover["value_krw"], foreign_flow["buy_krw"], foreign_flow["sell_krw"]
    participation = imbalance = None
    inconsistent = total is not None and buy is not None and sell is not None and max(buy, sell) > total
    if buy is None or sell is None:
        imbalance_status = foreign_flow["status"] if foreign_flow["status"] != "partial" else "missing_input"
    elif inconsistent:
        imbalance_status = "inconsistent_amounts"
    elif buy + sell == 0:
        imbalance_status = "zero_denominator"
    else:
        imbalance, imbalance_status = (buy - sell) / (buy + sell), "available"

    if total is None:
        participation_status = turnover["status"]
    elif total == 0:
        participation_status = "zero_denominator"
    elif buy is None or sell is None:
        participation_status = imbalance_status
    elif inconsistent:
        participation_status = "inconsistent_amounts"
    else:
        participation, participation_status = (buy + sell) / (2 * total) * 100, "available"
    return {"participation_pct": participation, "participation_status": participation_status,
            "buy_sell_imbalance": imbalance, "imbalance_status": imbalance_status}


def _index(rows: Sequence[_Row] | None, market: str, latest: date, now: datetime) -> dict[tuple[str, date], _Row]:
    by_key: dict[tuple[str, date], _Row] = {}
    for row in rows or ():
        if row.market.lower() != market or row.as_of > latest or not market_calendar.is_open("kr", row.as_of):
            continue
        ticker = _ticker(row.ticker, market)
        if ticker:
            key = (ticker, row.as_of)
            old = by_key.get(key)
            if old is None or _preference(row, now) > _preference(old, now):
                by_key[key] = row
    return by_key


def _preference(row: Turnover | ForeignFlow | Ownership, now: datetime) -> tuple[bool, datetime, str]:
    at = row.published_at
    status = _publication_status(row, now) if isinstance(row, Ownership) else _money_status(row, now)
    return status == "available", at if at and at.utcoffset() is not None else datetime.min.replace(tzinfo=UTC), row.source


def _ranking(
    publication: Publication, market: str, latest: date, now: datetime,
) -> tuple[dict, list[tuple[str, Turnover]]]:
    candidates = [
        row for row in publication.rankings or ()
        if row.market.lower() == market and row.as_of <= latest and market_calendar.is_open("kr", row.as_of)
        and _publication_status(row, now) == "available"
    ]
    ranked_by_day: dict[tuple[date, str], dict[str, Turnover]] = {(row.as_of, row.source): {} for row in candidates}
    for row in publication.turnover or ():
        by_ticker = ranked_by_day.get((row.as_of, row.source))
        if row.market.lower() != market or by_ticker is None:
            continue
        ticker = _ticker(row.ticker, market)
        if not ticker or _money_status(row, now) != "available" or _amount(row.value_krw) is None:
            continue
        previous = by_ticker.get(ticker)
        if previous is None or _preference(row, now) > _preference(previous, now):
            by_ticker[ticker] = row

    candidates = [row for row in candidates if ranked_by_day[(row.as_of, row.source)]]
    if not candidates:
        status = "source_unavailable" if publication.turnover is None else _empty_status(publication.rankings)
        return _metadata(None, status), []
    day = max(candidates, key=lambda row: (row.as_of, row.published_at or datetime.min.replace(tzinfo=UTC), row.source))
    ranked = sorted(ranked_by_day[(day.as_of, day.source)].items(), key=lambda pair: (-(pair[1].value_krw or 0), pair[0]))
    return _metadata(day, "available" if day.as_of == latest else "stale"), ranked[:RANK_LIMIT]


def _section(points: Sequence[dict], latest_ownership: Sequence[dict]) -> str:
    statuses = [point[key]["status"] for point in points for key in ("turnover", "foreign_flow", "ownership")]
    statuses.extend(row["status"] for row in latest_ownership)
    if not any(status in ("available", "partial") for status in statuses):
        return "no_data"
    complete = all(status == "available" for status in statuses) and all(
        point["imbalance_status"] != "inconsistent_amounts" for point in points
    )
    return "available" if complete else "partial"


def build_board(
    market: str, dates: Sequence[date], *, publication: Publication, now: datetime,
    favorites: Sequence[dict] = (), favorite_markets: Mapping[str, str] | None = None,
) -> dict:
    """Combine only same-session KRX amounts and the calling account's supplied favorites."""
    market = market.lower()
    if market not in _SUFFIX:
        raise ValueError("market must be kospi or kosdaq")
    latest = dates[-1]
    turnover = _index(publication.turnover, market, latest, now)
    foreign = _index(publication.foreign_flow, market, latest, now)
    ownership = _index(publication.ownership, market, latest, now)
    empty_turnover = _empty_status(publication.turnover)
    empty_foreign = _empty_status(publication.foreign_flow)
    empty_ownership = _empty_status(publication.ownership)

    def point(ticker: str, day: date, ranked: Turnover | None = None) -> dict:
        key = (ticker, day)
        amount = _turnover(ranked or turnover.get(key), now, empty_turnover)
        flow = _foreign_flow(foreign.get(key), now, empty_foreign)
        return {"date": day.isoformat(), "turnover": amount, "foreign_flow": flow,
                "ownership": _ownership(ownership.get(key), now, empty_ownership), **_ratios(amount, flow)}

    def latest_ownership(ticker: str, cutoff: date) -> dict:
        for day, row in ownership_by_ticker.get(ticker, ()):
            if day <= cutoff:
                return _ownership(row, now, empty_ownership)
        return _ownership(None, now, empty_ownership)

    ownership_by_ticker: dict[str, list[tuple[date, Ownership]]] = {}
    for (ticker, day), row in sorted(ownership.items(), key=lambda entry: entry[0][1], reverse=True):
        if _ownership(row, now, empty_ownership)["status"] == "available":
            ownership_by_ticker.setdefault(ticker, []).append((day, row))

    ranking, ranked = _ranking(publication, market, latest, now)
    rank_items = [
        {"ticker": ticker, "name": row.name, "rank": i, "latest": point(ticker, row.as_of, row),
         "latest_ownership": latest_ownership(ticker, row.as_of)}
        for i, (ticker, row) in enumerate(ranked, start=1)
    ]

    favorite_items = []
    seen = set()
    for favorite in favorites:
        raw = favorite["ticker"].strip().upper()
        allow_bare = bool(favorite_markets and favorite_markets.get(raw, "").lower() == market)
        ticker = _ticker(raw, market, allow_bare=allow_bare)
        if ticker and ticker not in seen:
            favorite_items.append({"ticker": ticker, "name": favorite.get("name"),
                                   "history": [point(ticker, day) for day in dates],
                                   "latest_ownership": latest_ownership(ticker, latest)})
            seen.add(ticker)

    ranking_status = _section([item["latest"] for item in rank_items], [item["latest_ownership"] for item in rank_items])
    favorites_status = _section([sample for item in favorite_items for sample in item["history"]],
                                [item["latest_ownership"] for item in favorite_items])
    status = "no_data"
    if rank_items or favorites_status != "no_data":
        status = "available" if (ranking["status"] == ranking_status == "available"
                                  and (not favorite_items or favorites_status == "available")) else "partial"
    return {
        "market": market, "venue": _VENUE, "status": status, "latest_session": latest.isoformat(),
        "sessions": [day.isoformat() for day in dates],
        "ranking": {**ranking, "items": rank_items},
        "favorites": {"status": favorites_status, "items": favorite_items},
        "sources": {
            "turnover": {"status": "available" if turnover else empty_turnover},
            "foreign_flow": {"status": "available" if foreign else empty_foreign},
            "ownership": {"status": "available" if ownership else empty_ownership},
        },
    }
