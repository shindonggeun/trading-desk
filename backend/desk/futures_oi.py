"""KOSPI200 futures open interest, basis and the options' put/call open interest per day (#603), from
the KRX end-of-day rows desk.krx keeps (``fut_bydd_trd`` · ``opt_bydd_trd`` in krx_daily).

KIS (desk.futures_flow, desk.futures_history) gives today's values; KRX gives the past. One row a day
goes to ``futures_oi_daily`` - the option table holds thousands of rows a day, too many to read again
for a year's line on every request. The rows are derived again (idempotent) after each KRX pass and by
``python -m desk.cli krx derive``; KRX is never asked from here.

- Front month: the quarterly contract whose last trading day (the second Thursday, back to an open day)
  is today or later - the same contract desk.futures_flow.front_month shows, expiry day included.
  Contracts are told apart by the YYYYMM in their name, not by code (KRX and KIS codes differ).
- ``oi_change`` compares the front contract with its own value the previous business day, so the day
  after a roll is not "the new contract minus the old one". ``oi_total`` is every quarterly contract;
  its change counts the contracts listed on both days, so the expired month's last open interest
  dropping off the list the day after expiry is not read as a fall. ``rolling`` marks the last ROLL_DAYS business days before an expiry and
  the expiry itself, where the front month's open interest moves to the next one.
- A business day KRX has no rows for (not collected yet) leaves the next day's changes empty: two
  days' difference is not written as one.
- Options: the monthly KOSPI200 options only (no weeklies, no minis). ``pc_oi_ratio`` = put / call
  open interest over every listed month; ``atm_iv`` = the mean implied volatility of the call and put
  at the strike nearest the index, from the nearest month with at least ROLL_DAYS business days left.
- ``features_before(day, at)`` hands the direction call (#572) only rows dated before ``day`` and
  published by ``at`` - KRX puts D's rows up at 08:30 KST the next business day (desk.krx.ready_at), so
  a 07:30 call sees D-2's row, not D-1's. Training and the live call read the same thing.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine

from . import market_calendar
from .db import futures_oi_daily, krx_daily, now
from .krx import num, open_days, ready_at

log = logging.getLogger(__name__)

FUT_API, OPT_API = "fut_bydd_trd", "opt_bydd_trd"
FUT_PRODUCT = "코스피200선물"      # PROD_NM without spaces; the mini, weekly and stock futures differ
OPT_PRODUCT = "코스피200옵션"      # the monthly options; weeklies are "코스피200 위클리 옵션(...)"
NIGHT = "야간"                     # MKT_NM of the night session - the regular session's rows are read
QUARTERS = (3, 6, 9, 12)
ROLL_DAYS = 5
MAX_DAYS = 1000
CHUNK_DAYS = 20
FEATURE_WINDOW = 20
OI_WARN_RATIO, OI_WARN_MIN = 0.01, 300.0     # KRX vs the KIS closing snapshot (desk.futures_history)
BASIS_WARN = 0.5
MONTH_RE = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)")
STRIKE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*$")


def _bare(text: object) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def expiry_of(year: int, month: int) -> date:
    """The last trading day of a KOSPI200 month: its second Thursday, moved back to an open day."""
    first = date(year, month, 1)
    second_thursday = first + timedelta(days=(3 - first.weekday()) % 7 + 7)
    d = second_thursday
    while not market_calendar.is_open("kr", d):
        d -= timedelta(days=1)
    return d


def _month(name: object) -> tuple[int, int] | None:
    """The contract month in a KRX name; None for none or two (a spread, "... SP 202612-202703")."""
    found = MONTH_RE.findall(str(name or ""))
    return (int(found[0][0]), int(found[0][1])) if len(found) == 1 else None


def futures(rows: Iterable[dict]) -> list[dict]:
    """The regular session's quarterly KOSPI200 futures: [{month, expiry, code, close, settle, spot, oi}]."""
    out: dict[tuple[int, int], dict] = {}
    for r in rows:
        if _bare(r.get("PROD_NM")) != FUT_PRODUCT or NIGHT in str(r.get("MKT_NM") or ""):
            continue
        ym = _month(r.get("ISU_NM"))
        if ym is None or ym[1] not in QUARTERS:
            continue
        f = {"month": ym, "expiry": expiry_of(*ym), "code": str(r.get("ISU_CD") or ""),
             "close": num(r.get("TDD_CLSPRC")), "settle": num(r.get("SETL_PRC")),
             "spot": num(r.get("SPOT_PRC")), "oi": num(r.get("ACC_OPNINT_QTY"))}
        if ym in out:
            # one contract a month is expected; keep the larger open interest, whatever the row order
            log.warning("krx futures %s: two rows for %04d%02d (%s, %s)", r.get("BAS_DD"), *ym,
                        out[ym]["code"], f["code"])
            if (f["oi"] or 0) <= (out[ym]["oi"] or 0):
                continue
        out[ym] = f
    return sorted(out.values(), key=lambda f: f["month"])


def options(rows: Iterable[dict]) -> list[dict]:
    """The monthly KOSPI200 options: [{right, month, expiry, strike, oi, iv}]."""
    out = []
    for r in rows:
        if _bare(r.get("PROD_NM")) != OPT_PRODUCT:
            continue
        right = str(r.get("RGHT_TP_NM") or "").strip().upper()
        ym, strike = _month(r.get("ISU_NM")), STRIKE_RE.search(str(r.get("ISU_NM") or ""))
        if right not in ("CALL", "PUT") or ym is None or strike is None:
            continue
        out.append({"right": right, "month": ym, "expiry": expiry_of(*ym), "strike": float(strike.group(1)),
                    "oi": num(r.get("ACC_OPNINT_QTY")), "iv": num(r.get("IMP_VOLT"))})
    return out


def front(futs: list[dict], day: date) -> dict | None:
    live = [f for f in futs if f["expiry"] >= day]
    return min(live, key=lambda f: f["expiry"]) if live else None


def days_left(day: date, expiry: date) -> int:
    """Business days after ``day`` up to and including ``expiry`` (0 on the expiry day)."""
    return max(0, len(open_days(day, expiry)) - 1) if expiry >= day else -1


def _sum(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def put_call(opts: list[dict]) -> float | None:
    calls = _sum(o["oi"] for o in opts if o["right"] == "CALL")
    puts = _sum(o["oi"] for o in opts if o["right"] == "PUT")
    return round(puts / calls, 4) if calls and puts is not None else None


def atm_iv(opts: list[dict], spot: float | None, day: date) -> tuple[float | None, date | None]:
    """The mean IV of the call and the put nearest the index, in the nearest month that is not about to
    expire (ROLL_DAYS business days left or more)."""
    if spot is None:
        return None, None
    for expiry in sorted({o["expiry"] for o in opts if o["expiry"] >= day}):
        if days_left(day, expiry) < ROLL_DAYS:
            continue
        pairs: dict[float, dict[str, float]] = {}
        for o in opts:
            if o["expiry"] == expiry and o["iv"] is not None and o["iv"] > 0:
                pairs.setdefault(o["strike"], {})[o["right"]] = o["iv"]
        both = [k for k, v in pairs.items() if len(v) == 2]
        if not both:
            continue
        k = min(both, key=lambda s: (abs(s - spot), s))
        return round((pairs[k]["CALL"] + pairs[k]["PUT"]) / 2, 2), expiry
    return None, None


def derive(day: date, futs: list[dict], opts: list[dict], prev: list[dict] | None) -> dict | None:
    """The day's row from its parsed KRX rows (futures(), options()); prev the previous business day's
    futures (None: not collected). None when the day has no KOSPI200 futures (closed, or not collected)."""
    f = front(futs, day)
    if f is None:
        return None
    prev_by = {p["month"]: p for p in prev or []}
    prev_same = prev_by.get(f["month"])
    oi_total = _sum(x["oi"] for x in futs)
    both = [(x["oi"], prev_by[x["month"]]["oi"]) for x in futs if x["month"] in prev_by]
    both = [(a, b) for a, b in both if a is not None and b is not None]
    oi_total_change = sum(a - b for a, b in both) if both else None
    iv, iv_expiry = atm_iv(opts, f["spot"], day)
    return {
        "trade_date": day, "code": f["code"][:32], "expiry": f["expiry"],
        "close": f["close"], "settle": f["settle"], "spot": f["spot"],
        # futures - KOSPI200 at the close, as desk.futures_flow's market basis (not the settlement price)
        "basis": round(f["close"] - f["spot"], 2) if f["close"] is not None and f["spot"] is not None else None,
        "oi": f["oi"],
        "oi_change": f["oi"] - prev_same["oi"] if prev_same and f["oi"] is not None and prev_same["oi"] is not None
        else None,
        "oi_total": oi_total,
        "oi_total_change": oi_total_change,
        "rolling": days_left(day, f["expiry"]) <= ROLL_DAYS,
        "pc_oi_ratio": put_call(opts), "atm_iv": iv, "atm_iv_expiry": iv_expiry,
    }


def _prev_open(day: date) -> date:
    d = day - timedelta(days=1)
    while not market_calendar.is_open("kr", d):
        d -= timedelta(days=1)
    return d


def _point(r) -> dict:
    out = {k: r[k] for k in ("code", "close", "settle", "spot", "basis", "oi", "oi_change", "oi_total",
                             "oi_total_change", "pc_oi_ratio", "atm_iv")}
    return {"date": r["trade_date"].isoformat(), "expiry": r["expiry"].isoformat(), **out,
            "rolling": bool(r["rolling"]),
            "atm_iv_expiry": r["atm_iv_expiry"].isoformat() if r["atm_iv_expiry"] else None}


def kis_month(code: str, day: date) -> tuple[int, int] | None:
    """KIS' front-month code (A01612: the year's last digit, the month) as (year, month) near ``day``."""
    m = re.fullmatch(r"A01(\d)(\d{2})", code or "")
    if not m:
        return None
    year = day.year - day.year % 10 + int(m.group(1))
    if year < day.year - 1:
        year += 10
    return year, int(m.group(2))


class FuturesOi:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = now) -> None:
        self.engine = engine
        self.clock = clock

    def _krx_rows(self, api: str, first: date, last: date, parse: Callable[[list[dict]], list[dict]]
                  ) -> dict[date, list[dict]]:
        """[first, last]'s rows of api, parsed day by day - only the KOSPI200 rows stay in memory (the
        services also carry minis, sector, bond and currency futures, weeklies)."""
        out: dict[date, list[dict]] = {}
        raw: list[dict] = []
        day = None
        with self.engine.connect() as conn:
            result = conn.execution_options(stream_results=True, yield_per=2000).execute(
                select(krx_daily.c.bas_dd, krx_daily.c.payload)
                .where(krx_daily.c.api == api, krx_daily.c.bas_dd >= first, krx_daily.c.bas_dd <= last)
                .order_by(krx_daily.c.bas_dd, krx_daily.c.code))
            for d, payload in result:
                if d != day and raw:
                    out[day] = parse(raw)
                    raw = []
                day = d
                raw.append(payload)
        if raw:
            out[day] = parse(raw)
        # rows that are there but none of them read: KRX's names differ from what this module expects
        unread = sorted(d for d, rows in out.items() if not rows)
        if unread:
            log.warning("krx %s: no KOSPI200 rows read on %d day(s) with rows (%s ~ %s) - check PROD_NM / ISU_NM "
                        "(docs/krx.md)", api, len(unread), unread[0], unread[-1])
        return out

    def derive(self, first: date, last: date) -> int:
        """Write futures_oi_daily for the business days in [first, last] that have KRX futures rows
        (replacing what was there); returns the rows written. Reads krx_daily only."""
        days = open_days(first, last)
        if not days:
            return 0
        written = 0
        for chunk in range(0, len(days), CHUNK_DAYS):    # a few weeks at a time: a backfill spans years
            part = days[chunk:chunk + CHUNK_DAYS]
            futs = self._krx_rows(FUT_API, _prev_open(part[0]), part[-1], futures)
            opts = self._krx_rows(OPT_API, part[0], part[-1], options)
            rows = [r for d in part
                    if d in futs and (r := derive(d, futs[d], opts.get(d, []), futs.get(_prev_open(d))))]
            at = self.clock()
            with self.engine.begin() as conn:
                conn.execute(delete(futures_oi_daily).where(futures_oi_daily.c.trade_date.in_(part)))
                if rows:
                    conn.execute(insert(futures_oi_daily), [{**r, "computed_at": at} for r in rows])
            written += len(rows)
        return written

    def history(self, days: int = 250) -> list[dict]:
        """The latest ``days`` rows, newest first."""
        days = max(1, min(days, MAX_DAYS))
        with self.engine.connect() as conn:
            rows = conn.execute(select(futures_oi_daily).order_by(futures_oi_daily.c.trade_date.desc())
                                .limit(days)).mappings().all()
        return [_point(r) for r in rows]

    def features_before(self, day: date, at: datetime | None = None) -> dict | None:
        """For the direction call on ``day`` made at ``at``: the latest row dated before ``day`` that KRX had
        published by ``at`` (ready_at), with each value's z-score against the FEATURE_WINDOW rows before
        that one. Without ``at``, every row before ``day``. None without such a row."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(futures_oi_daily).where(futures_oi_daily.c.trade_date < day)
                                .order_by(futures_oi_daily.c.trade_date.desc())
                                .limit(FEATURE_WINDOW + 4)).mappings().all()
        if at is not None:
            rows = [r for r in rows if ready_at(r["trade_date"]) <= at]
        rows = rows[:FEATURE_WINDOW + 1]
        if not rows:
            return None
        last, past = rows[0], rows[1:]
        out = {"date": last["trade_date"].isoformat()}
        for key in ("oi_total_change", "basis", "pc_oi_ratio", "atm_iv"):
            out[key] = last[key]
            vals = [r[key] for r in past if r[key] is not None]
            z = None
            if last[key] is not None and len(vals) >= 5:
                mean = sum(vals) / len(vals)
                sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))
                z = round((last[key] - mean) / sd, 2) if sd > 0 else None
            out[f"{key}_z"] = z
        return out

    def check(self, kis_days: list[dict]) -> list[str]:
        """Compare the KIS closing snapshots (desk.futures_history.daily) with KRX's day of the same
        contract; a gap past the thresholds is logged as a warning. Returns the dates that differed."""
        by_day = {p["date"]: p for p in self.history(len(kis_days) + 5)}
        off = []
        for k in kis_days:
            mine = by_day.get(k.get("date"))
            if mine is None:
                continue
            day = date.fromisoformat(k["date"])
            expiry = date.fromisoformat(mine["expiry"])
            if kis_month(str(k.get("code") or ""), day) != (expiry.year, expiry.month):
                continue                          # a different contract (KIS rolled at another moment)
            gaps = []
            if k.get("open_interest") is not None and mine["oi"] is not None:
                gap = abs(k["open_interest"] - mine["oi"])
                if gap > max(OI_WARN_RATIO * mine["oi"], OI_WARN_MIN):
                    gaps.append(f"open interest KIS {k['open_interest']:.0f} vs KRX {mine['oi']:.0f}")
            if (k.get("market_basis") is not None and mine["basis"] is not None
                    and abs(k["market_basis"] - mine["basis"]) > BASIS_WARN):
                gaps.append(f"basis KIS {k['market_basis']:.2f} vs KRX {mine['basis']:.2f}")
            if gaps:
                log.warning("futures %s: %s", k["date"], "; ".join(gaps))
                off.append(k["date"])
        return off
