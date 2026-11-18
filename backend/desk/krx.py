"""KRX OPEN API daily collector (#601): end-of-day index · derivatives · ETF · bond rows into krx_daily.

KRX (openapi.krx.co.kr) publishes the previous business day's closing values only - this is history,
validation and backfill, never a live source. One GET per (service, day):

    GET https://data-dbg.krx.co.kr/svc/apis/{category}/{api_id}.json?basDd=YYYYMMDD   header AUTH_KEY

Every answer is HTTP 200 with {"OutBlock_1": [...]} (numbers as strings, possibly with commas), an
empty list on a closed day, or {"respCode": "401", "respMsg": ...} when the key is wrong or the service
was never applied for on the portal (each service needs its own approval). Over the daily quota
(10,000 calls per key, reset at midnight) the answer is HTTP 429. Data for day D appears at about
08:00 KST on the next business day; we read it from 08:30 (docs/krx.md).

The key (DESK_KRX_API_KEY) rides only the request header: it is never logged, stored or put in a URL.
Without a key nothing here runs. KRX's terms allow internal display only (no redistribution).
"""

from __future__ import annotations

import logging
import threading
import time as _time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import and_, delete, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from . import market_calendar
from .db import krx_daily, krx_days
from .db import now as db_now

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
TIMEOUT_SEC = 20.0
READY_AT = time(8, 30)            # D's rows are read from this time on the next KR business day
DAILY_LIMIT = 10_000              # KRX's quota per key per day
DAILY_BUDGET = 9_000              # what this process allows itself, leaving room for a manual run
MIN_GAP_SEC = 0.3                 # between two calls
CATCH_UP_DAYS = 14                # the schedule fills missing days this far back (a server that was down)
EMPTY_AFTER_DAYS = 7              # an open day still empty this long after is taken as having no rows
REFUSED_FOR = timedelta(hours=6)  # a service the key may not use is not asked again for this long
LATER_EVERY = timedelta(hours=3)  # a recent open day that came back empty, or failed, is asked again after this
MAX_ERRORS = 3                    # failed days in a row that end a service's pass
TICK_SEC = 15 * 60
EARLIEST = date(2010, 1, 4)       # the first basDd KRX serves for these services


@dataclass(frozen=True)
class Api:
    category: str
    title: str
    code: tuple[str, ...]          # the fields that name a row within one day


# The services the desk reads (docs/krx.md has their fields). The schedule collects all of them; a
# service the key was not approved for answers 401 and is skipped for REFUSED_FOR.
APIS: dict[str, Api] = {
    "kospi_dd_trd": Api("idx", "KOSPI 시리즈 일별시세정보", ("IDX_NM",)),
    "kosdaq_dd_trd": Api("idx", "KOSDAQ 시리즈 일별시세정보", ("IDX_NM",)),
    "drvprod_dd_trd": Api("idx", "파생상품지수 시세정보", ("IDX_NM",)),
    # MKT_NM (the session, e.g. regular / night) can repeat an ISU_CD within a day
    "fut_bydd_trd": Api("drv", "선물 일별매매정보(주식선물 외)", ("ISU_CD", "MKT_NM")),
    "opt_bydd_trd": Api("drv", "옵션 일별매매정보(주식옵션 외)", ("ISU_CD",)),
    "etf_bydd_trd": Api("etp", "ETF 일별매매정보", ("ISU_CD",)),
    "kts_bydd_trd": Api("bon", "국채전문유통시장 일별매매정보", ("ISU_CD", "MKT_NM")),
}


class KrxError(RuntimeError):
    """A call that gave no rows we can keep. The message never holds the key."""


class Refused(KrxError):
    """respCode 401 (or HTTP 401/403): a wrong key, or a service the key was not approved for."""


class QuotaExceeded(KrxError):
    """HTTP 429, or this process's DAILY_BUDGET spent: nothing more until midnight KST."""


def num(value: object) -> float | None:
    """KRX's number strings ("1,234.56", "-12", "", "-") as a float; None when there is no number."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def kst_today(at: datetime) -> date:
    return market_calendar.local_today("kr", at)


def ready_at(day: date) -> datetime:
    """When day's rows can be read: READY_AT on the next KR business day."""
    return datetime.combine(market_calendar.next_open("kr", day), READY_AT, KST)


def open_days(first: date, last: date) -> list[date]:
    out, d = [], first
    while d <= last:
        if market_calendar.is_open("kr", d):
            out.append(d)
        d += timedelta(days=1)
    return out


def row_code(api: str, row: dict) -> str:
    """The row's name within its day; "" when the first code field (the id itself) is missing."""
    parts = [str(row.get(f) or "").strip() for f in APIS[api].code]
    return "|".join(parts)[:64] if parts[0] else ""


class Client:
    """One GET per (service, day) with the quota and pacing guards. Thread-safe enough for one loop."""

    def __init__(self, key: str, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], datetime] = db_now, sleep: Callable[[float], None] = _time.sleep,
                 budget: int = DAILY_BUDGET):
        key = key.strip()
        # a header value must be printable ASCII; anything else would fail inside httpx with an error that
        # quotes a character of the key, so such a key turns the collector off instead
        self._key = key if key.isascii() and key.isprintable() else ""
        if key and not self._key:
            log.warning("DESK_KRX_API_KEY is not printable ASCII - KRX collection off")
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self.budget = budget
        self._spent: tuple[date, int] = (date.min, 0)
        self._last = 0.0
        self._quota_day: date | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    def spent(self) -> int:
        day, n = self._spent
        return n if day == kst_today(self._clock()) else 0

    def fetch(self, api: str, day: date) -> list[dict]:
        """The day's rows ([] on a closed day). Raises Refused / QuotaExceeded / KrxError."""
        if api not in APIS:
            raise KrxError(f"unknown KRX service {api}")
        today = kst_today(self._clock())
        if self._quota_day == today or self.spent() >= self.budget:
            raise QuotaExceeded(f"KRX daily budget spent ({self.budget}); next calls after midnight KST")
        gap = MIN_GAP_SEC - (_time.monotonic() - self._last)
        if gap > 0:
            self._sleep(gap)
        a = APIS[api]
        url = f"{BASE_URL}/{a.category}/{api}.json"
        self._spent = (today, self.spent() + 1)
        self._last = _time.monotonic()
        try:
            with httpx.Client(transport=self._transport, timeout=TIMEOUT_SEC, follow_redirects=False) as client:
                res = client.get(url, params={"basDd": day.strftime("%Y%m%d")},
                                 headers={"AUTH_KEY": self._key, "Accept": "application/json"})
        except Exception as exc:  # noqa: BLE001 - no chaining: the request object carries the header
            raise KrxError(f"KRX {api} {day}: {type(exc).__name__}") from None
        if res.status_code == 429:
            self._quota_day = today
            raise QuotaExceeded(f"KRX {api} {day}: HTTP 429 (daily quota)")
        if res.status_code in (401, 403):
            raise Refused(f"KRX {api} {day}: HTTP {res.status_code} (key or service approval)")
        if res.status_code != 200:
            raise KrxError(f"KRX {api} {day}: HTTP {res.status_code}")
        return self._rows(api, day, res)

    def _rows(self, api: str, day: date, res: httpx.Response) -> list[dict]:
        try:
            body = res.json()
        except ValueError:
            raise KrxError(f"KRX {api} {day}: not JSON") from None
        if not isinstance(body, dict):
            raise KrxError(f"KRX {api} {day}: unexpected {type(body).__name__}")
        code = str(body.get("respCode") or "").strip()
        if code:                           # respMsg stays out: server text could echo the key in any form
            if code == "401":
                raise Refused(f"KRX {api} {day}: respCode 401 (key or service approval)")
            safe = code if code.isalnum() and len(code) <= 8 and code not in self._key else "?"
            raise KrxError(f"KRX {api} {day}: respCode {safe}")
        blocks = [v for k, v in body.items() if k.startswith("OutBlock_") and isinstance(v, list)]
        if len(blocks) != 1 or not all(isinstance(r, dict) for r in blocks[0]):
            raise KrxError(f"KRX {api} {day}: no single OutBlock list")
        return blocks[0]


class KrxDaily:
    """Stores what Client fetches: krx_daily (one row per api · day · code) and krx_days (one per api · day
    asked, the day's row count - 0 for a closed day), so a rerun asks only for the days it lacks."""

    def __init__(self, engine: Engine, client: Client, clock: Callable[[], datetime] = db_now,
                 apis: Sequence[str] = tuple(APIS)):
        self.engine = engine
        self.client = client
        self.clock = clock
        self.apis = tuple(apis)
        self._refused: dict[str, datetime] = {}
        self._later: dict[tuple[str, date], datetime] = {}
        self._running = threading.Lock()   # a pass past the loop's timeout must not overlap the next one

    def done(self, api: str, first: date, last: date, with_rows_only: bool = False) -> set[date]:
        q = select(krx_days.c.bas_dd).where(and_(
            krx_days.c.api == api, krx_days.c.bas_dd >= first, krx_days.c.bas_dd <= last))
        if with_rows_only:
            q = q.where(krx_days.c.rows > 0)
        with self.engine.connect() as conn:
            return set(conn.execute(q).scalars())

    def missing(self, api: str, first: date, last: date, at: datetime, refetch_empty: bool = False) -> list[date]:
        """Open days in [first, last] not stored yet whose rows KRX has published by `at` (refetch_empty:
        also the open days stored with no rows)."""
        have = self.done(api, first, last, with_rows_only=refetch_empty)
        return [d for d in open_days(max(first, EARLIEST), last) if d not in have and ready_at(d) <= at]

    def store(self, api: str, day: date, rows: Iterable[dict], at: datetime | None = None) -> tuple[int, int]:
        """Replace the day's rows for api; returns (kept, dropped). A row without a code, or repeating one
        already kept, is dropped and logged - APIS names the fields that should make each row unique."""
        at = at or self.clock()
        seen: dict[str, dict] = {}
        dropped = 0
        for row in rows:
            code = row_code(api, row)
            if code and code not in seen:
                seen[code] = row
            else:
                dropped += 1
        if dropped:
            log.warning("krx %s %s: %d row(s) without a code or with a repeated one dropped", api, day, dropped)
        with self.engine.begin() as conn:
            conn.execute(delete(krx_daily).where(and_(krx_daily.c.api == api, krx_daily.c.bas_dd == day)))
            conn.execute(delete(krx_days).where(and_(krx_days.c.api == api, krx_days.c.bas_dd == day)))
            if seen:
                conn.execute(insert(krx_daily), [
                    {"api": api, "bas_dd": day, "code": code, "payload": row, "fetched_at": at}
                    for code, row in seen.items()])
            conn.execute(insert(krx_days).values(api=api, bas_dd=day, rows=len(seen), fetched_at=at))
        return len(seen), dropped

    def collect(self, api: str, days: Iterable[date], at: datetime | None = None,
                max_calls: int | None = None) -> dict:
        """Fetch and store each day in turn. A refusal or the quota stops; a failed day is skipped (asked
        again after LATER_EVERY) and MAX_ERRORS in a row stop; an open day that comes back empty while
        still recent is left for later (KRX may not have published it yet)."""
        at = at or self.clock()
        out = {"api": api, "stored": 0, "rows": 0, "dropped": 0, "empty": 0, "later": 0, "failed": 0,
               "calls": 0, "stopped": None}
        errors = 0
        for day in days:
            if max_calls is not None and out["calls"] >= max_calls:
                out["stopped"] = "max_calls"
                break
            out["calls"] += 1
            try:
                rows = self.client.fetch(api, day)
            except Refused as exc:
                self._refused[api] = at
                out["stopped"] = "refused"
                log.warning("%s", exc)
                break
            except QuotaExceeded as exc:
                out["stopped"] = "quota"
                log.warning("%s", exc)
                break
            except KrxError as exc:
                log.warning("%s", exc)
                self._later[(api, day)] = at
                out["failed"] += 1
                errors += 1
                if errors >= MAX_ERRORS:
                    out["stopped"] = "error"
                    break
                continue
            errors = 0
            if not rows and (kst_today(at) - day).days < EMPTY_AFTER_DAYS:
                self._later[(api, day)] = at
                out["later"] += 1
                continue
            try:
                kept, dropped = self.store(api, day, rows, at)
            except IntegrityError:          # the same day stored at the same moment elsewhere (CLI + schedule)
                log.warning("krx %s %s: stored concurrently elsewhere", api, day)
                continue
            out["stored"] += 1
            out["rows"] += kept
            out["dropped"] += dropped
            out["empty"] += kept == 0
        return out

    def run(self, at: datetime | None = None) -> list[dict]:
        """The schedule's pass: every service's missing days of the last CATCH_UP_DAYS, once published."""
        at = at or self.clock()
        if not self.client.enabled or not self._running.acquire(blocking=False):
            return []
        try:
            return self._run(at)
        finally:
            self._running.release()

    def _run(self, at: datetime) -> list[dict]:
        last = kst_today(at) - timedelta(days=1)
        first = last - timedelta(days=CATCH_UP_DAYS)
        results = []
        for api in self.apis:
            refused = self._refused.get(api)
            if refused and at - refused < REFUSED_FOR:
                continue
            days = [d for d in self.missing(api, first, last, at)
                    if not (asked := self._later.get((api, d))) or at - asked >= LATER_EVERY]
            if not days:
                continue
            result = self.collect(api, days, at)
            results.append(result)
            if result["stopped"] == "quota":
                break
        self._later = {k: v for k, v in self._later.items() if at - v < timedelta(days=EMPTY_AFTER_DAYS)}
        return results

    def cooldowns(self, at: datetime) -> list[tuple[str, str, datetime]]:
        """#623 what this collector waits out now: the daily quota (until midnight KST) and each service
        the key was refused for (REFUSED_FOR) - (name, label, until)."""
        out = []
        today = kst_today(at)
        if self.client._quota_day == today or self.client.spent() >= self.client.budget:
            out.append(("krx_quota", "KRX 하루 한도", datetime.combine(today + timedelta(days=1), time(0), KST)))
        refused_now = dict(self._refused)          # a copy (one C call): the collector's thread adds to it
        for api, refused in sorted(refused_now.items()):
            if at - refused < REFUSED_FOR:
                out.append((f"krx_refused_{api}", f"KRX {api} 거절(키 · 이용 신청)", refused + REFUSED_FOR))
        return out

    def rows(self, api: str, day: date) -> list[dict]:
        with self.engine.connect() as conn:
            return [r.payload for r in conn.execute(
                select(krx_daily.c.payload).where(and_(krx_daily.c.api == api, krx_daily.c.bas_dd == day))
                .order_by(krx_daily.c.code))]
