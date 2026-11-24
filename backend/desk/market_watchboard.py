"""Completed daily observations for fixed, public market watchboards."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from types import MappingProxyType
from urllib.parse import quote
from zoneinfo import ZoneInfo

import holidays

from . import market_calendar as cal

_DAY = timedelta(days=1)
_MIDNIGHT = time()
_TOKYO_BREAK = (time(11, 30), time(12, 30))
_TOKYO = ZoneInfo("Asia/Tokyo")
_TAIPEI = ZoneInfo("Asia/Taipei")
_ZONES = {"kr": cal.KST, "us": cal.ET, "jp": _TOKYO, "tw": _TAIPEI, "cme": cal.ET,
          "crypto": ZoneInfo("UTC")}
# Published Yahoo quote delays, or a conservative floor for the US instruments.
# These are not measurements of when the provider last updated a particular bar.
_LAGS = {"kr": 20, "us": 15, "jp": 20, "tw": 20, "cme": 10, "crypto": 0}
_EXCHANGES = {"jp": "XJPX", "tw": "XTAI"}


@dataclass(frozen=True)
class Instrument:
    ticker: str
    name: str
    market: str
    currency: str
    kind: str
    relation: str
    compare: bool = False


GROUPS = MappingProxyType({
    "semiconductors": (
        Instrument("^KS11", "코스피", "kr", "KRW", "index", "benchmark", True),
        Instrument("NQ=F", "나스닥100 E-mini 연속 선물", "cme", "USD", "future", "benchmark", True),
        Instrument("^SOX", "필라델피아 반도체지수", "us", "USD", "index", "benchmark", True),
        Instrument("^N225", "닛케이225", "jp", "JPY", "index", "benchmark", True),
        Instrument("^TWII", "대만 가권지수", "tw", "TWD", "index", "benchmark", True),
        Instrument("005930.KS", "삼성전자", "kr", "KRW", "stock", "related"),
        Instrument("000660.KS", "SK하이닉스", "kr", "KRW", "stock", "related"),
        Instrument("NVDA", "엔비디아", "us", "USD", "stock", "related"),
        Instrument("285A.T", "키옥시아", "jp", "JPY", "stock", "related"),
        Instrument("2330.TW", "TSMC 대만 본주", "tw", "TWD", "stock", "related"),
        Instrument("TSM", "TSMC 미국 ADR", "us", "USD", "adr", "reference"),
    ),
    "crypto": (
        Instrument("BTC-USD", "비트코인", "crypto", "USD", "crypto", "benchmark", True),
        Instrument("ETH-USD", "이더리움", "crypto", "USD", "crypto", "benchmark", True),
    ),
    "gold_miners": (
        Instrument("GDX", "금광주 ETF", "us", "USD", "etf", "benchmark", True),
        Instrument("NEM", "뉴몬트", "us", "USD", "stock", "related", True),
        Instrument("AEM", "애그니코 이글 마인스", "us", "USD", "stock", "related", True),
    ),
})

_Fetch = Callable[[str], Awaitable[dict]]


@dataclass(frozen=True)
class _Session:
    open: datetime
    close: datetime


def _utc(stamp: datetime) -> str:
    return stamp.astimezone(UTC).isoformat(timespec="seconds")


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None and 1970 <= stamp.year <= 2100 else None


@lru_cache(maxsize=32)
def _financial_holidays(market: str, year: int) -> frozenset[date] | None:
    try:
        return frozenset(holidays.financial_holidays(_EXCHANGES[market], years=year))
    except NotImplementedError:  # older environments may not provide one exchange calendar
        return None


@lru_cache(maxsize=4096)
def _session(market: str, day: date) -> tuple[str, _Session | None]:
    zone = _ZONES[market]
    if market == "crypto":
        return "trading", _Session(datetime.combine(day, _MIDNIGHT, zone),
                                   datetime.combine(day + _DAY, _MIDNIGHT, zone))
    if day.weekday() > 4:
        return "holiday", None
    if market == "cme":
        # The shared calendar has normal Globex hours only. An NYSE holiday or
        # half day flags a possible exceptional Globex session, without assuming
        # the two exchanges have the same closure.
        if not cal.is_open("us", day) or cal.us_early_close(day) is not None:
            return "unknown", None
        return "trading", _Session(datetime.combine(day - _DAY, cal.CME_OPENS, zone),
                                   datetime.combine(day, cal.CME_CLOSES, zone))
    if market in ("kr", "us"):
        return ("trading", _Session(*cal.session_bounds(market, day))) if cal.is_open(market, day) else ("holiday", None)
    holidays_for_year = _financial_holidays(market, day.year)
    if holidays_for_year is None:
        return "unknown", None
    if day in holidays_for_year:
        return "holiday", None
    # TWSE individual stocks can finish the final auction at 13:33. Record the
    # scheduled close at 13:30 and apply an extra completion guard separately.
    close = time(15, 30) if market == "jp" else time(13, 30)
    return "trading", _Session(datetime.combine(day, time(9), zone), datetime.combine(day, close, zone))


def _market_state(market: str, now: datetime) -> str:
    local = now.astimezone(_ZONES[market])
    day, wall = local.date(), local.time()
    if market == "cme":
        if day.weekday() == 5 or (day.weekday() == 6 and wall < cal.CME_OPENS):
            return "closed"
        if day.weekday() == 4 and wall >= cal.CME_CLOSES:
            return "closed"
        if wall >= cal.CME_OPENS:
            day += _DAY
    state, session = _session(market, day)
    if session is None:
        return state
    if market == "jp" and _TOKYO_BREAK[0] <= wall < _TOKYO_BREAK[1]:
        return "break"
    if market == "cme" and cal.CME_CLOSES <= wall < cal.CME_OPENS:
        return "break"
    return "open" if session.open <= now < session.close else "closed"


def _ready_at(market: str, session: _Session) -> datetime:
    return session.close + timedelta(minutes=_LAGS[market] + (3 if market == "tw" else 0))


def _expected_day(market: str, today: date, *, as_of: datetime | None = None) -> date | None:
    """Last expected trading day, stopping on an unverified exchange session."""
    for offset in range(16):
        day = today - offset * _DAY
        state, session = _session(market, day)
        if state == "unknown":
            return None
        if session is not None and (as_of is None or _ready_at(market, session) <= as_of):
            return day
    return None


def _rows(market: str, rows: list, checked: datetime, now: datetime) -> dict[date, dict]:
    zone = _ZONES[market]
    by_day, duplicates = {}, set()
    for row in rows:
        stamp = _timestamp(row.get("at")) if isinstance(row, dict) else None
        if stamp is None:
            continue
        local = stamp.astimezone(zone)
        close = row.get("close")
        if local.time() != _MIDNIGHT or type(close) not in (float, int) or not 1e-100 <= close <= 1e100:
            continue
        day = local.date()
        if day in by_day:
            duplicates.add(day)
        by_day[day] = {"trade_date": day.isoformat(), "at": _utc(stamp), "close": close}
    for day in duplicates:
        del by_day[day]
    if market == "cme":
        local_check = checked.astimezone(zone)
        day = local_check.date()
        # Around the rollover Yahoo can stamp a new, still-forming session with
        # the previous day's label. The existing price resource repairs it when
        # both sessions can be distinguished. Otherwise leave that day out.
        if local_check.weekday() < 4 and local_check.time() >= cal.CME_OPENS and day + _DAY not in by_day:
            by_day.pop(day, None)
    completed = {}
    for day, row in sorted(by_day.items()):
        _, session = _session(market, day)
        if session is not None and _ready_at(market, session) <= min(now, checked):
            completed[day] = {**row, "closed_at": _utc(session.close)}
    return completed


def _percent(close: float, baseline: float) -> float:
    return round((close / baseline - 1) * 100, 6)


def _item(instrument: Instrument, data: object, now: datetime, window_start: date) -> dict:
    ticker, market = instrument.ticker, instrument.market
    item = {
        "ticker": ticker, "name": instrument.name, "market": market, "timezone": _ZONES[market].key,
        "currency": instrument.currency, "unit": "points" if instrument.kind in ("index", "future") else "currency",
        "kind": instrument.kind, "relation": instrument.relation,
        "chart_href": f"/#/charts?t={quote(ticker, safe='')}&iv=1d", "market_state": _market_state(market, now),
        "status": "missing", "reason": "source_error", "source": None, "fetched_at": None, "source_delay_min": None,
        "latest": None, "change_pct": None, "history": [],
    }
    if not isinstance(data, dict):
        return item
    if data.get("ticker") != ticker or data.get("interval") != "1d":
        return {**item, "reason": "wrong_instrument"}
    if data.get("source") != "yahoo":
        return {**item, "reason": "source_unverified"}
    item.update(source="yahoo", source_delay_min=_LAGS[market])
    checked = _timestamp(data.get("fetched_at"))
    if checked is None or checked > now + timedelta(seconds=5):
        return {**item, "reason": "timestamp_unverified"}
    item["fetched_at"] = _utc(checked)
    raw_rows = data.get("bars")
    rows = _rows(market, raw_rows, checked, now) if isinstance(raw_rows, list) else {}
    local_day = now.astimezone(_ZONES[market]).date()
    expected = _expected_day(market, local_day, as_of=now)
    if not rows:
        return {**item, "reason": "session_unverified" if expected is None else "no_completed_bar"}
    latest_day = max(rows)
    latest = rows[latest_day]
    previous = rows.get(_expected_day(market, latest_day - _DAY))
    reason = ("session_unverified" if expected is None else
              "latest_session_missing" if latest_day < expected else None)
    return {
        **item, "status": "delayed" if reason else "available", "reason": reason,
        "latest": latest, "change_pct": _percent(latest["close"], previous["close"]) if previous else None,
        "history": [row for day, row in rows.items() if day >= window_start],
    }


def _comparison(instruments: tuple[Instrument, ...], items: list[dict]) -> dict:
    benchmarks = [item for instrument, item in zip(instruments, items, strict=True) if instrument.compare]
    histories = {item["ticker"]: {row["trade_date"]: row["close"] for row in item["history"]}
                 for item in benchmarks if item["history"]}
    common = set.intersection(*(set(rows) for rows in histories.values())) if len(histories) >= 2 else set()
    if not common:
        return {"status": "unavailable", "base_date": None, "series": [],
                "excluded": [item["ticker"] for item in benchmarks]}
    base = min(common)
    excluded = [item["ticker"] for item in benchmarks if item["ticker"] not in histories]
    series = [{"ticker": ticker, "points": [{"trade_date": day, "return_pct": _percent(close, rows[base])}
                                            for day, close in rows.items() if day >= base]}
              for ticker, rows in histories.items()]
    partial = excluded or any(item["status"] != "available" for item in benchmarks)
    return {"status": "partial" if partial else "available", "base_date": base, "series": series, "excluded": excluded}


async def read(group: str, days: int, fetch: _Fetch, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict:
    """Read one fixed group using the caller's bounded, cached daily price resource."""
    instruments = GROUPS[group]

    async def one(instrument: Instrument) -> dict | None:
        try:
            return await fetch(instrument.ticker)
        except Exception:  # noqa: BLE001 - a single source failure must not fail other instruments
            return None

    fetched = await asyncio.gather(*(one(instrument) for instrument in instruments))
    now = clock().astimezone(UTC)
    window_start = now.date() - (days - 1) * _DAY
    items = [_item(instrument, data, now, window_start) for instrument, data in zip(instruments, fetched, strict=True)]
    return {"group": group, "days": days, "as_of": _utc(now), "items": items,
            "comparison": _comparison(instruments, items)}
