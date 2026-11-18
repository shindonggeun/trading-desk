"""KRX's own KOSPI daily bars for today's direction calls (#604): the Korean answer (previous close or
open -> close) is KRX's official open and close, not Yahoo's copy.

desk.krx keeps KRX's end-of-day rows in krx_daily (api kospi_dd_trd, code "코스피", from the next business
day 08:30 KST). ``merge`` puts them in place of Yahoo's ^KS11 bars on the days KRX has (the latest
session, not published yet, stays Yahoo's) and lists every day the two disagree by more than
DIFF_PCT, or that only one of them has. The training (desk.day_call_train, ``--krx-file`` or
``--krx-db``) and the live scoring (desk.day_calls) use the merged bars.

A training run on the PC has no krx_daily: ``python -m desk.cli krx export`` on the server writes the
rows to a file it can read.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path

from sqlalchemy import and_, select

from . import day_call as dc
from . import krx
from .broker.base import Bar
from .db import krx_daily

API = "kospi_dd_trd"
NAME = "코스피"                 # IDX_NM of the KOSPI composite row
SYMBOL = "^KS11"                # the Yahoo series KRX's bars stand in for
DIFF_PCT = 0.1                  # open or close apart by more than this (%) is a disagreement worth a line
FIELDS = {"open": "OPNPRC_IDX", "high": "HGPRC_IDX", "low": "LWPRC_IDX", "close": "CLSPRC_IDX"}


def bar_of(day: date, row: dict) -> Bar | None:
    """KRX's row as a Yahoo-shaped daily bar (dated at local midnight, as Yahoo dates ^KS11); None when a
    price is missing ("-" on a day with no trade)."""
    prices = {k: krx.num(row.get(f)) for k, f in FIELDS.items()}
    if any(not v for v in prices.values()):
        return None
    at = datetime.combine(day, time(0), dc.KST).astimezone(UTC).isoformat(timespec="seconds")
    return Bar(at=at, volume=krx.num(row.get("ACC_TRDVOL")) or 0.0, **prices)


def bars(rows: dict[date, dict]) -> list[Bar]:
    """{day: KRX row} as bars, oldest first, days without prices left out."""
    out = [bar_of(d, rows[d]) for d in sorted(rows)]
    return [b for b in out if b is not None]


def load_db(engine, first: date | None = None, last: date | None = None) -> dict[date, dict]:
    """The KOSPI rows krx_daily holds (desk.krx), {day: payload}."""
    q = select(krx_daily.c.bas_dd, krx_daily.c.payload).where(
        and_(krx_daily.c.api == API, krx_daily.c.code == NAME))
    if first:
        q = q.where(krx_daily.c.bas_dd >= first)
    if last:
        q = q.where(krx_daily.c.bas_dd <= last)
    with engine.connect() as conn:
        return {d: p for d, p in conn.execute(q)}


def export(engine, path: str | Path) -> int:
    """Write the KOSPI rows to ``path`` for a training run elsewhere; returns how many days."""
    rows = load_db(engine)
    body = {"api": API, "code": NAME, "rows": {d.isoformat(): rows[d] for d in sorted(rows)}}
    Path(path).write_text(json.dumps(body, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return len(rows)


def load_file(path: str | Path) -> dict[date, dict]:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    if body.get("api") != API or body.get("code") != NAME:
        raise ValueError(f"{path}: not a {API} / {NAME} export")
    return {date.fromisoformat(d): row for d, row in body["rows"].items()}


def _pct(a: float, b: float) -> float:
    return (b / a - 1) * 100


def merge(yahoo: Sequence[Bar], rows: dict[date, dict]) -> tuple[list[Bar], dict]:
    """Yahoo's ^KS11 bars with KRX's in their place on every day KRX has, and the check: how many days
    came from each, and each day the open or close differ by more than DIFF_PCT or only one side has
    (inside the span both cover)."""
    theirs = {dc.session_day(SYMBOL, b): b for b in yahoo if b.close}
    ours = {dc.session_day(SYMBOL, b): b for b in bars(rows)}
    merged = {**theirs, **ours}
    diffs, only_yahoo, only_krx = [], [], []
    if ours and theirs:
        lo, hi = max(min(ours), min(theirs)), min(max(ours), max(theirs))
        for day in sorted(set(ours) | set(theirs)):
            if not lo <= day <= hi:
                continue
            k, y = ours.get(day), theirs.get(day)
            if k is None:
                only_yahoo.append(day.isoformat())
            elif y is None:
                only_krx.append(day.isoformat())
            else:
                gaps = {f: round(_pct(getattr(y, f), getattr(k, f)), 3) for f in ("open", "close")}  # KRX vs Yahoo
                if any(abs(v) > DIFF_PCT for v in gaps.values()):
                    diffs.append({"day": day.isoformat(), "krx": {"open": k.open, "close": k.close},
                                  "yahoo": {"open": y.open, "close": y.close}, "pct": gaps})
    check = {"source": "krx" if ours else "yahoo", "krx_days": len(ours), "yahoo_only_days": len(set(theirs) - set(ours)),
             "from": min(ours).isoformat() if ours else None, "to": max(ours).isoformat() if ours else None,
             "diff_pct": DIFF_PCT, "diffs": diffs, "only_yahoo": only_yahoo, "only_krx": only_krx}
    return [merged[d] for d in sorted(merged)], check


def apply(series: dict[str, list], rows: dict[date, dict]) -> dict:
    """Put KRX's bars into ``series`` (in place: a new ^KS11 list) and return the check."""
    series[SYMBOL], check = merge(series.get(SYMBOL) or [], rows)
    return check
