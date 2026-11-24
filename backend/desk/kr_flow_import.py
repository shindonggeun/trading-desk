"""Validate a complete, authorized KRX daily-stock JSON saved out of band (#916)."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import delete, insert, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from . import market_calendar
from .db import krx_daily, krx_days, make_engine
from .kr_flow_data import MAX_LISTINGS, STOCK_APIS, parsed_stock

MAX_BYTES = 4 * 1024 * 1024


def validate(market: str, day: date, data: object, now: datetime) -> dict[str, dict]:
    if not market_calendar.is_open("kr", day) or now < market_calendar.session_close("kr", day):
        raise ValueError("not a closed KRX session")
    rows = data.get("OutBlock_1") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_LISTINGS:
        raise ValueError("expected a complete KRX OutBlock_1")
    valid = {}
    for position, row in enumerate(rows, start=1):
        record = parsed_stock(row, market, day)
        if record is None or record.value_krw is None or record.ticker in valid:
            raise ValueError(f"invalid date, market, turnover or duplicate stock at row {position}")
        valid[record.ticker] = {"BAS_DD": day.isoformat(), "ISU_CD": record.ticker,
                                "ISU_NM": record.name, "MKT_NM": market.upper(),
                                "ACC_TRDVAL": str(record.value_krw)}
    return valid


def save(engine: Engine, market: str, day: date, data: object, now: datetime) -> int:
    payloads = validate(market, day, data, now)
    api = STOCK_APIS[market]
    marker = {"api": api, "bas_dd": day, "rows": len(payloads), "fetched_at": now}
    with engine.begin() as conn:
        count = conn.execute(update(krx_days).where(
            krx_days.c.api == api, krx_days.c.bas_dd == day,
        ).values(rows=marker["rows"], fetched_at=now)).rowcount
        if not count:
            # A concurrent first import will lose the unique key and roll back.
            conn.execute(insert(krx_days), marker)
        conn.execute(delete(krx_daily).where(krx_daily.c.api == api, krx_daily.c.bas_dd == day))
        conn.execute(insert(krx_daily), [
            {"api": api, "bas_dd": day, "code": code, "payload": payload, "fetched_at": now}
            for code, payload in payloads.items()
        ])
    return len(payloads)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=tuple(STOCK_APIS), required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="store the validated market day in the desk database")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    try:
        with args.input.open("rb") as path:
            raw = path.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("input exceeds the KRX snapshot size limit")
        data = json.loads(raw)
        if args.apply:
            from .settings import Settings

            engine = make_engine(Settings().database_url)
            try:
                count = save(engine, args.market, args.date, data, now)
            finally:
                engine.dispose()
        else:
            count = len(validate(args.market, args.date, data, now))
    except (OSError, ValueError, RecursionError, SQLAlchemyError):
        print("KRX snapshot rejected or storage unavailable")
        return 1
    print(f"{'stored' if args.apply else 'validated'} {args.market} {args.date}: {count} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
