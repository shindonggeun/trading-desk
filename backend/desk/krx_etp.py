"""Leveraged / inverse ETFs' net assets and NAV gap, and the KTB 3 / 10-year yields, from KRX (#605).

Read from ``krx_daily`` (#601 stores KRX OPEN API end-of-day rows; nothing here calls KRX):

- ``etf_bydd_trd`` - per ETF in ``ETFS``: net assets (INVSTASST_NETASST_TOTAMT) and their change on the
  previous stored day, the listed units (LIST_SHRS) and their change - units are created or redeemed
  only when money comes in or goes out, so ``streak`` (days in a row the units grew, or shrank) and
  ``flow`` (the change in units x NAV) are the "retail positioning" proxy; net assets also move with
  the price - and the NAV gap (close / NAV - 1).
- ``kts_bydd_trd`` - the 3- and 10-year KTB closing yields (CLSPRC_YD) and their change in bp: the row
  whose maturity (BND_EXP_TP_NM) is that many years, the benchmark issue (GOVBND_ISU_TP_NM "지표") first,
  else the most traded; the change is that same issue (ISU_CD) on the day before - when the benchmark
  rolls to a new issue there is no change, never one issue's yield against another's. These field values are KRX's documented ones (docs/krx.md), checked against real
  rows only after the first backfill - a value that never matches leaves the yields out, not wrong.

A day is a stored business day with rows (``krx_days.rows > 0``): a closed day (0 rows) is skipped, so
"the day before" is the previous trading day - and only that: when the previous KR trading day is not
stored (a failed collection not yet caught up), there is no change and the streak ends there, never
two days' move said as one. KRX publishes day D at about 08:00 KST on the next
business day, so the 07:00 morning brief reads the day before that - every line says its date. A latest
day older than ``STALE_DAYS`` is no "latest" (nothing is said). ETF rows are matched by the short code
or its ISIN (KR7 + code + 00 + check digit) - KRX's ISU_CD form is confirmed with the first real rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

from sqlalchemy import and_, select
from sqlalchemy.engine import Engine

from . import market_calendar
from .db import krx_daily, krx_days
from .krx import num

ETF_API = "etf_bydd_trd"
KTB_API = "kts_bydd_trd"
LOOKBACK = 10                # stored days read back for the streak
STALE_DAYS = 6               # a latest KRX day older than this (calendar days) is not shown
KTB_YEARS = (3, 10)


@dataclass(frozen=True)
class Etf:
    code: str                # the 6-digit short code
    name: str
    side: str                # "up" (leveraged long) | "down" (inverse)
    brief: bool = False      # also in the morning brief (the tips show them all)


ETFS = (
    Etf("122630", "KODEX 레버리지", "up", brief=True),
    Etf("252670", "KODEX 200선물인버스2X", "down", brief=True),
    Etf("114800", "KODEX 인버스", "down"),
    Etf("233740", "KODEX 코스닥150레버리지", "up"),
    Etf("251340", "KODEX 코스닥150선물인버스", "down"),
)
SIDE_KO = {"up": "상승 베팅", "down": "하락 베팅"}


def isin(code: str) -> str:
    """A Korean share's ISIN: KR7 + the 6-digit code + 00 + the ISO 6166 check digit."""
    body = f"KR7{code}00"
    digits = "".join(str(int(c, 36)) for c in body)        # letters as 10..35
    total = 0
    for i, ch in enumerate(reversed(digits)):              # Luhn, doubling from the rightmost digit
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d // 10 + d % 10
    return body + str((10 - total % 10) % 10)


def _days(conn, api: str, today: date, n: int) -> list[date]:
    """The newest of the last n stored days with rows, up to today, that follow each other trading day by
    trading day: a calendar day KRX answered empty for (stored with 0 rows) is a closed day, a trading day
    never stored ends the run."""
    days = list(conn.execute(
        select(krx_days.c.bas_dd).where(and_(krx_days.c.api == api, krx_days.c.rows > 0, krx_days.c.bas_dd <= today))
        .order_by(krx_days.c.bas_dd.desc()).limit(n)).scalars())
    if len(days) < 2:
        return days
    closed = set(conn.execute(select(krx_days.c.bas_dd).where(and_(
        krx_days.c.api == api, krx_days.c.rows == 0, krx_days.c.bas_dd >= days[-1], krx_days.c.bas_dd <= days[0]))).scalars())
    out = days[:1]
    for d in days[1:]:
        if d != _prev_open(out[-1], closed):
            break
        out.append(d)
    return out


def _prev_open(day: date, closed: set[date]) -> date:
    d = day - timedelta(days=1)
    while not market_calendar.is_open("kr", d) or d in closed:
        d -= timedelta(days=1)
    return d


def etf_flows(engine: Engine, today: date, etfs: tuple[Etf, ...] = ETFS) -> dict | None:
    """The latest day's numbers per ETF (None when nothing recent is stored)."""
    by_code = {e.code: e for e in etfs} | {isin(e.code): e for e in etfs}
    with engine.connect() as conn:
        days = _days(conn, ETF_API, today, LOOKBACK + 1)
        if not days or (today - days[0]).days > STALE_DAYS:
            return None
        got: dict[str, dict[date, dict]] = {}
        for day, code, payload in conn.execute(
                select(krx_daily.c.bas_dd, krx_daily.c.code, krx_daily.c.payload)
                .where(and_(krx_daily.c.api == ETF_API, krx_daily.c.bas_dd.in_(days),
                            krx_daily.c.code.in_(list(by_code))))):
            got.setdefault(by_code[code].code, {})[day] = payload
    items = []
    for e in etfs:
        # every stored day in order, up to the first one this ETF has no row for
        series = []
        for d in days:
            if (p := got.get(e.code, {}).get(d)) is None:
                break
            series.append((d, p))
        if series and (item := _etf_item(e, series)):
            items.append(item)
    return {"date": days[0].isoformat(), "items": items} if items else None


def _etf_item(e: Etf, rows: list[tuple[date, dict]]) -> dict | None:
    """rows: the ETF's payload on consecutive stored days, newest first."""
    day, p = rows[0]
    net, units, close, nav = (num(p.get(k)) for k in ("INVSTASST_NETASST_TOTAMT", "LIST_SHRS", "TDD_CLSPRC", "NAV"))
    if net is None:
        return None
    item = {"code": e.code, "name": e.name, "side": e.side, "brief": e.brief, "date": day.isoformat(),
            "net_assets": net, "net_change_pct": None, "units": units, "units_change": None, "flow": None,
            "streak": 0, "nav_gap_pct": None}
    if close and nav:
        item["nav_gap_pct"] = round((close / nav - 1) * 100, 2)
    if len(rows) >= 2:
        q = rows[1][1]
        prev_net, prev_units = num(q.get("INVSTASST_NETASST_TOTAMT")), num(q.get("LIST_SHRS"))
        if prev_net:
            item["net_change_pct"] = round((net / prev_net - 1) * 100, 2)
        if units is not None and prev_units is not None:
            item["units_change"] = units - prev_units
            if nav:
                item["flow"] = (units - prev_units) * nav
    item["streak"] = _streak(rows)
    return item


def _streak(rows: list[tuple[date, dict]]) -> int:
    """Days in a row the listed units grew (> 0) or shrank (< 0), up to the latest day; 0 when flat."""
    units = [num(p.get("LIST_SHRS")) for _, p in rows]
    sign, n = 0, 0
    for a, b in pairwise(units):
        if a is None or b is None or a == b:
            break
        s = 1 if a > b else -1
        if sign and s != sign:
            break
        sign, n = s, n + 1
    return sign * n


def _years(text: object) -> int | None:
    m = re.match(r"\s*(\d+)", str(text or ""))
    return int(m.group(1)) if m else None


def _ktb_pick(rows: list[dict], years: int) -> dict | None:
    cands = [r for r in rows if _years(r.get("BND_EXP_TP_NM")) == years and num(r.get("CLSPRC_YD")) is not None]
    if not cands:
        return None
    return max(cands, key=lambda r: ("지표" in str(r.get("GOVBND_ISU_TP_NM") or ""), num(r.get("ACC_TRDVAL")) or 0))


def ktb_yields(engine: Engine, today: date) -> dict | None:
    """The 3- and 10-year closing yields of the latest stored day and their change in bp."""
    with engine.connect() as conn:
        days = _days(conn, KTB_API, today, 2)
        if not days or (today - days[0]).days > STALE_DAYS:
            return None
        rows = {d: [r for (r,) in conn.execute(select(krx_daily.c.payload).where(
            and_(krx_daily.c.api == KTB_API, krx_daily.c.bas_dd == d)))] for d in days}
    out = {}
    for y in KTB_YEARS:
        now = _ktb_pick(rows[days[0]], y)
        if now is None:
            continue
        # the same issue the day before (a benchmark roll is no yield move)
        prev = next((r for r in rows[days[1]] if r.get("ISU_CD") == now.get("ISU_CD")
                     and num(r.get("CLSPRC_YD")) is not None), None) if len(days) > 1 else None
        yd = num(now["CLSPRC_YD"])
        out[y] = {"yield": yd, "change_bp": round((yd - num(prev["CLSPRC_YD"])) * 100, 1) if prev else None,
                  "name": now.get("ISU_NM")}
    return {"date": days[0].isoformat(), "years": out} if out else None


def summary(engine: Engine, today: date) -> dict:
    """Both, for the morning brief and the tips (each None when nothing recent is stored)."""
    return {"etf": etf_flows(engine, today), "ktb": ktb_yields(engine, today)}


# ---------------------------------------------------------------- words

def eok(won: float, signed: bool = False) -> str:
    return f"{round(won / 1e8):+,}억" if signed else f"{round(won / 1e8):,}억"


def pct(x: float) -> str:
    return f"{x:+.2f}%"


def etf_line(item: dict) -> str:
    """One ETF in a phrase: net assets (change), units streak with the money in or out, NAV gap."""
    text = f"{item['name']} 순자산 {eok(item['net_assets'])}"
    if item["net_change_pct"] is not None:
        text += f"({pct(item['net_change_pct'])})"
    s = item["streak"]
    if s:
        text += f", 좌수 {f'{abs(s)}일 연속 ' if abs(s) > 1 else ''}{'증가' if s > 0 else '감소'}"
        if item["flow"]:
            text += f"(전일 대비 {eok(item['flow'], signed=True)})"
    if item["nav_gap_pct"] is not None:
        text += f", NAV 괴리 {pct(item['nav_gap_pct'])}"
    return text


def etf_tip(item: dict) -> str:
    """The tips' sentence: what the units say about the bets on that side."""
    s = item["streak"]
    side = SIDE_KO[item["side"]]
    if s >= 2:
        return f"{etf_line(item)} — {side} 자금이 {s}일째 들어오고 있어요"
    if s <= -2:
        return f"{etf_line(item)} — {side} 자금이 {-s}일째 빠지고 있어요"
    return etf_line(item)


def ktb_line(ktb: dict) -> str:
    parts = []
    for y, v in sorted(ktb["years"].items()):
        text = f"국고 {y}년 {v['yield']:.3f}%"
        if v["change_bp"] is not None:
            text += f"({v['change_bp']:+.1f}bp)"
        parts.append(text)
    return " · ".join(parts)


def tips_view(engine: Engine, today: date) -> dict:
    """The tips' KRX lines: each ETF's sentence and the yields (empty / None when nothing is stored)."""
    got = summary(engine, today)
    etf, ktb = got["etf"], got["ktb"]
    return {"etf_flow": [{"code": i["code"], "name": i["name"], "side": i["side"], "date": i["date"],
                          "streak": i["streak"], "text": etf_tip(i)} for i in (etf or {}).get("items", [])],
            "ktb": {"date": ktb["date"], "text": ktb_line(ktb),
                    "years": {str(y): v for y, v in ktb["years"].items()}} if ktb else None}
