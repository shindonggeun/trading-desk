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
                        "earnings": {"refreshed_at": earn.refreshed_at, "error": earn.error}},
            "notes": [("시각은 한국 시간(KST)이며 미국 서머타임을 반영합니다 — 미국 일정은 미국 날짜에 두고 "
                       "한국 시간으로 다음 날이면 next_day_kst 로 표시합니다"),
                      "실적 장 전 = 미국 개장 전(한국 밤), 장 후 = 미국 마감 뒤(한국 다음 날 아침)",
                      "한국 발표 일정(금통위)은 손으로 관리하는 목록입니다 — 빠진 날짜가 있을 수 있습니다"]}
