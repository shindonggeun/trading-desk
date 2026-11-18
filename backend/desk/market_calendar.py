"""A month of the Korean and US markets in Korean time (#243): closed days, early closes, scheduled
releases, option / futures expiries and company earnings - GET /api/calendar.

- Closed days: the ``holidays`` package's exchange calendars (XKRX, NYSE), offline, with names in
  Korean for KRX. NYSE early closes (13:00 ET) follow the exchange's fixed rules: the day after
  Thanksgiving, and July 3 / December 24 on a weekday that is not itself a holiday.
- Releases: the US ones desk.events_calendar already reads (FOMC, CPI, jobs, PPI, JOLTS, ECI) and the
  Bank of Korea's rate decisions from ``data/kr_events.json`` (kept by hand: the bank publishes the
  year's dates once).
- Expiries: US monthly options on the third Friday (the business day before when it is a holiday),
  quarterly futures and options together in March, June, September and December; KOSPI200 options on
  the second Thursday (likewise moved back), with futures in the quarterly months.
- Earnings: desk.earnings.

Every time is stored with its zone and given in Korean time (``at_kst``) - an 08:30 ET release is
22:30 KST in US summer time and 22:30 + 1 h after it ends, which the zones take care of.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays

from . import earnings, events_calendar

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
WEEKDAY_KO = "월화수목금토일"
DATA = Path(__file__).parent / "data" / "kr_events.json"
US_HOLIDAY_KO = {
    "New Year's Day": "새해 첫날", "Martin Luther King Jr. Day": "마틴 루서 킹 데이", "Washington's Birthday": "대통령의 날",
    "Good Friday": "성금요일", "Memorial Day": "메모리얼 데이", "Juneteenth National Independence Day": "준틴스",
    "Independence Day": "독립기념일", "Labor Day": "노동절", "Thanksgiving Day": "추수감사절", "Christmas Day": "크리스마스",
}
US_CLOSE = time(16, 0)
US_EARLY = time(13, 0)
KR_CLOSE = time(15, 30)
KR_EXPIRY_CLOSE = time(15, 20)       # an expiry day's last option trade (KRX)


@lru_cache(maxsize=8)
def _closed(market: str, year: int) -> dict[date, str]:
    if market == "kr":
        return dict(holidays.financial_holidays("XKRX", years=year, language="ko"))
    out = {}
    for d, name in holidays.financial_holidays("NYSE", years=year).items():
        base = name.replace(" (observed)", "")
        out[d] = US_HOLIDAY_KO.get(base, base) + (" (대체)" if "(observed)" in name else "")
    return out


def holiday(market: str, d: date) -> str | None:
    return _closed(market, d.year).get(d)


def is_open(market: str, d: date) -> bool:
    return d.weekday() < 5 and holiday(market, d) is None


def us_early_close(d: date) -> str | None:
    """The NYSE 13:00 ET closes: the day after Thanksgiving, July 3 and December 24 on open weekdays."""
    if not is_open("us", d):
        return None
    if d.month == 11 and d.weekday() == 4 and holiday("us", d - timedelta(days=1)) == US_HOLIDAY_KO["Thanksgiving Day"]:
        return "추수감사절 다음 날"
    if (d.month, d.day) == (7, 3):
        return "독립기념일 전날"
    if (d.month, d.day) == (12, 24):
        return "크리스마스이브"
    return None


# ------------------------------------------------------------------ sessions (#625)
# The one place for the markets' clocks: local dates, trading days, the regular sessions' open and close
# (KRX's late opens, the NYSE's early closes), the US off-hours phases and CME Globex's day. Every other
# module asks here instead of building a market time from an offset.

ZONES = {"kr": KST, "us": ET}
KR_OPEN = time(9, 0)
KR_LATE_OPEN = time(10, 0)           # the year's first trading day and the CSAT day open an hour late ...
KR_CSAT_CLOSE = time(16, 30)         # ... and the CSAT day also closes an hour late (the first day does not)
US_OPEN = time(9, 30)
US_PRE_OPEN = time(4, 0)             # the US off-hours phases KIS streams: pre 04:00-09:30, post 16:00-20:00,
US_POST_CLOSE = time(20, 0)          # overnight 20:00-04:00 (Sunday night to Friday morning)
CME_OPENS = time(18, 0)              # CME Globex: the session dated D runs 18:00 ET on D-1 ...
CME_CLOSES = time(17, 0)             # ... to 17:00 ET on D (equity index futures; a holiday is not modelled)
# CSAT days (Ministry of Education; 2017 was put off a week by the Pohang earthquake, 2020 by COVID-19):
# KRX opens at 10:00 and closes at 16:30 - add each year's when it is announced.
CSAT_DAYS = {date(2017, 11, 23), date(2018, 11, 15), date(2019, 11, 14), date(2020, 12, 3), date(2021, 11, 18),
             date(2022, 11, 17), date(2023, 11, 16), date(2024, 11, 14), date(2025, 11, 13), date(2026, 11, 19)}


def local_today(market: str, at: datetime | None = None) -> date:
    """The market's own date at ``at`` (default now): Korea's or New York's calendar day."""
    return (at or datetime.now(UTC)).astimezone(ZONES[market]).date()


def previous_open(market: str, day: date, limit: int = 15) -> date:
    """The market's trading day before ``day``. It looks at most ``limit`` days back and then returns the
    day it reached even if that one is closed - fine for any real calendar (the longest KRX / NYSE break is
    under ten days), so keep ``limit`` above the longest run of closed days."""
    d = day - timedelta(days=1)
    for _ in range(limit):
        if is_open(market, d):
            return d
        d -= timedelta(days=1)
    return d


def next_open(market: str, day: date) -> date:
    """The market's trading day after ``day``."""
    d = day + timedelta(days=1)
    while not is_open(market, d):
        d += timedelta(days=1)
    return d


def next_weekday(day: date) -> date:
    """The weekday after ``day`` - CME's sessions run on US holidays too, so only weekends are skipped."""
    d = day + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def kr_late_open(day: date) -> bool:
    """A KRX session that opens at 10:00: the year's first one, or the CSAT day."""
    if day in CSAT_DAYS:
        return True
    first = date(day.year, 1, 1)
    while not is_open("kr", first):
        first += timedelta(days=1)
    return day == first


def hours(market: str, day: date, early_close: bool = True) -> tuple[time, time]:
    """The regular session's local open and close on ``day``. ``early_close`` False keeps a US half day
    at 16:00 (a daily bar is read as closed at 16:00 either way)."""
    if market == "kr":
        return (KR_LATE_OPEN if kr_late_open(day) else KR_OPEN), (KR_CSAT_CLOSE if day in CSAT_DAYS else KR_CLOSE)
    return US_OPEN, (US_EARLY if early_close and us_early_close(day) else US_CLOSE)


def session_bounds(market: str, day: date, early_close: bool = True) -> tuple[datetime, datetime]:
    """The regular session's open and close on ``day`` as aware times."""
    opens, closes = hours(market, day, early_close)
    zone = ZONES[market]
    return datetime.combine(day, opens, zone), datetime.combine(day, closes, zone)


def session_close(market: str, day: date, early_close: bool = True) -> datetime:
    return session_bounds(market, day, early_close)[1]


def daily_open_offset(market: str) -> timedelta:
    """How long after its local midnight a market's regular session opens (a daily bar is stamped at
    that midnight) - the ordinary day's open."""
    t = KR_OPEN if market == "kr" else US_OPEN
    return timedelta(hours=t.hour, minutes=t.minute)


def bar_day(at: str, korean: bool, naive_is_utc: bool = False) -> date:
    """The market's own date of a daily bar stamped ``at``: a Korean bar opens at local midnight, 15:00
    UTC the day before; a US bar (midnight New York, 04:00/05:00 UTC) already carries its date in UTC. The
    stamps come as UTC (desk.prices) or without a zone: a Korean one without a zone is read as is, or as UTC
    with ``naive_is_utc`` (desk.sr's rule)."""
    stamp = datetime.fromisoformat(at)
    if not korean:
        return stamp.astimezone(UTC).date() if stamp.tzinfo is not None else stamp.date()
    if stamp.tzinfo is None:
        return (stamp.replace(tzinfo=UTC).astimezone(KST) if naive_is_utc else stamp).date()
    return stamp.astimezone(KST).date()


def us_phase(at: datetime | None = None) -> str | None:
    """The US equity session by the New York clock: regular (09:30-16:00), pre (04:00-09:30), post
    (16:00-20:00), overnight (20:00-04:00, Sunday night to Friday morning) or None (weekend). Holidays are
    not known here: on one nothing trades."""
    ny = (at or datetime.now(UTC)).astimezone(ET)
    t, day = ny.time(), ny.weekday()
    if t >= US_POST_CLOSE:
        return "overnight" if day in (6, 0, 1, 2, 3) else None
    if t < US_PRE_OPEN:
        return "overnight" if day in (0, 1, 2, 3, 4) else None
    if day >= 5:
        return None
    if t < US_OPEN:
        return "pre"
    return "regular" if t < US_CLOSE else "post"


def us_phase_day(phase: str, at: datetime) -> date:
    """The New York trading day a US phase belongs to (overnight after 20:00 is the next)."""
    ny = at.astimezone(ET)
    if phase == "overnight" and ny.time() >= US_POST_CLOSE:
        return ny.date() + timedelta(days=1)
    return ny.date()


def cme_session_start(at: datetime) -> datetime:
    """When the CME session holding ``at`` opened: 18:00 New York, that evening or the one before (by the
    wall clock - the weekend and holidays are not modelled here; callers skip them)."""
    local = at.astimezone(ET)
    start = datetime.combine(local.date(), CME_OPENS, ET)
    return start if local >= start else start - timedelta(days=1)


def cme_trade_day(at: datetime) -> date:
    """The date of the CME session trading at ``at``: after 17:00 New York it is the next day's (by the wall
    clock; a Friday evening gives Saturday - callers skip the weekend)."""
    local = at.astimezone(ET)
    return local.date() if local.time() < CME_CLOSES else local.date() + timedelta(days=1)


def _nth(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _back_to_open(market: str, d: date) -> date:
    while not is_open(market, d):
        d -= timedelta(days=1)
    return d


def _at(d: date, t: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=zone)


def _event(id_: str, label: str, market: str, at: datetime, importance: int, kind: str, source: str) -> dict:
    kst = at.astimezone(KST)
    return {"id": id_, "label": label, "market": market, "kind": kind, "importance": importance, "source": source,
            "date": at.date().isoformat(), "next_day_kst": kst.date() > at.date(),
            "at": at.astimezone(UTC).isoformat(), "at_kst": kst.isoformat(), "time_kst": kst.strftime("%H:%M"),
            "time_local": at.strftime("%H:%M ") + ("ET" if market == "us" else "KST")}


def expiries(year: int, month: int) -> list[dict]:
    quarter = month in (3, 6, 9, 12)
    us_day = _back_to_open("us", _nth(year, month, 4, 3))
    kr_day = _back_to_open("kr", _nth(year, month, 3, 2))
    return [
        _event("us_expiry", "미국 선물 · 옵션 동시만기(쿼드러플 위칭)" if quarter else "미국 월물 옵션 만기", "us",
               _at(us_day, US_EARLY if us_early_close(us_day) else US_CLOSE, ET), 2 if quarter else 1, "expiry",
               "rule"),
        _event("kr_expiry", "코스피200 선물 · 옵션 동시만기" if quarter else "코스피200 옵션 만기", "kr",
               _at(kr_day, KR_EXPIRY_CLOSE, KST), 2 if quarter else 1, "expiry", "rule"),
    ]


@lru_cache(maxsize=1)
def _kr_data() -> dict:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def kr_events(first: date, last: date) -> list[dict]:
    out = []
    for row in _kr_data().get("events") or []:
        try:
            d = date.fromisoformat(row["date"])
            t = time.fromisoformat(row.get("time") or "10:00")
        except (KeyError, TypeError, ValueError):
            continue
        if first <= d <= last:
            out.append(_event(row.get("id", "kr"), row.get("label", ""), "kr", _at(d, t, KST),
                              int(row.get("importance", 1)), "release", row.get("source", "manual")))
    return out


def us_events(first: date, last: date, cache: events_calendar.Calendar | None = None) -> list[dict]:
    cache = cache or events_calendar.CACHE
    out = []
    for e in cache.events():
        local = e.at.astimezone(ET)
        if first <= local.date() <= last:
            out.append(_event(e.id, e.label, "us", local, e.importance, "release", e.source))
    return out


def month_view(month: str, watch: set[str] | None = None, *, calendar: events_calendar.Calendar | None = None,
               earn: earnings.Earnings | None = None) -> dict:
    """``month`` "YYYY-MM". An event sits on its own market's date - a US release or expiry on the
    US day, as US reports are dated - with the Korean time beside it; ``next_day_kst`` marks one
    that falls after midnight in Korea (an FOMC decision at 04:00, the US close at 06:00)."""
    first = date.fromisoformat(month + "-01")
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    watch = watch or set()
    earn = earn or earnings.CACHE
    events = sorted(us_events(first, last, calendar) + kr_events(first, last) + expiries(first.year, first.month),
                    key=lambda e: e["at"])
    reports = earn.for_days(first, last, watch)
    days = []
    d = first
    while d <= last:
        iso = d.isoformat()
        days.append({
            "date": iso, "weekday": WEEKDAY_KO[d.weekday()],
            "kr": {"open": is_open("kr", d), "holiday": holiday("kr", d)},
            "us": {"open": is_open("us", d), "holiday": holiday("us", d), "early_close": us_early_close(d),
                   "close_kst": _at(d, US_EARLY if us_early_close(d) else US_CLOSE, ET).astimezone(KST).strftime("%H:%M")
                   if is_open("us", d) else None},
            "events": [e for e in events if e["date"] == iso],
            "earnings": reports.get(iso, []),
        })
        d += timedelta(days=1)
    return {"month": month, "days": days,
            "sources": {"economic": {"refreshed_at": (calendar or events_calendar.CACHE).refreshed_at,
                                     "error": (calendar or events_calendar.CACHE).error},
                        # #308: refreshed_at, a Korean error, stale (older rows shown), failed_sources
                        "earnings": earn.source_status()},
            "notes": [("시각은 한국 시간(KST)이며 미국 서머타임을 반영합니다 — 미국 일정은 미국 날짜에 두고 "
                       "한국 시간으로 다음 날이면 next_day_kst 로 표시합니다"),
                      "실적 장 전 = 미국 개장 전(한국 밤), 장 후 = 미국 마감 뒤(한국 다음 날 아침)",
                      "한국 발표 일정(금통위)은 손으로 관리하는 목록입니다 — 빠진 날짜가 있을 수 있습니다"]}
