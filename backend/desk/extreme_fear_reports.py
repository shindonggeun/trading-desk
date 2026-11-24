"""Offline research snapshot generation and bounded reads for #919."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from . import extreme_fear, indicators, market_calendar
from .backtest_reports import DIR

log = logging.getLogger(__name__)
VERSION = 1
MAX_REPORT_BYTES = 512 * 1024
INPUT_SOURCES = ("calendar", "prices", "cnn", "vix")
PRICE_ADJUSTMENT = "causal_forward_total_return"
SOURCES = {
    "rsi": ("SOXL/KORU adjusted NYSE daily closes", "<=", 30),
    "cnn": ("CNN Fear & Greed", "<=", 10),
    "vix": ("Cboe VIX daily close", ">=", 30),
}


@lru_cache(maxsize=1)
def _rules_hash() -> str:
    digest = hashlib.sha256()
    for module in (extreme_fear, indicators, market_calendar):
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()


def _timestamp(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return at.astimezone(UTC)


def _read(path: Path) -> dict | None:
    try:
        with path.open("rb") as file:
            raw = file.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("report exceeds size cap")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        log.warning("extreme fear report unreadable: %s", path.name)
        return None


def _latest_day(as_of: datetime) -> date:
    day = market_calendar.local_today("us", as_of)
    if market_calendar.is_open("us", day) and market_calendar.session_close("us", day) <= as_of:
        return day
    return market_calendar.previous_open("us", day)


def _empty_conditions(day: date, reason: str) -> dict:
    return {
        "date": day.isoformat(),
        "matched_count": 0,
        "status": "unverified",
        "conditions": {
            key: {"value": None, "operator": op, "threshold": threshold, "source": source,
                  "status": "unverified", "available_at": None, "reason": reason}
            for key, (source, op, threshold) in SOURCES.items()
        },
    }


def _observations(items: tuple[dict, ...]) -> dict:
    out = {}
    for item in items:
        key, value = item.get("key"), item.get("value")
        if key not in ("cnn", "vix") or type(value) not in (int, float) or not math.isfinite(value):
            continue
        out[key] = {
            "value": value, "source": item.get("source"), "as_of": item.get("as_of"),
            "status": "unverified", "reason": "observation_without_confirmed_session_close",
        }
    return out


def get(symbol: str, *, as_of: datetime, root: Path = DIR, observations: tuple[dict, ...] = ()) -> dict:
    """Read a curated offline report. An absent or stale current observation stays explicitly unknown."""
    if symbol not in extreme_fear.SYMBOLS:
        raise ValueError("unsupported ETF")
    latest = _latest_day(as_of)
    report = _read(root / f"extreme_fear_{symbol.lower()}.json")
    if not _usable(report, symbol, as_of):
        return {
            "symbol": symbol, "as_of": as_of.isoformat(), "status": "unverified",
            "reason": "approved_point_in_time_report_unavailable",
            "current": _empty_conditions(latest, "point_in_time_input_unavailable"),
            "signal_history": [], "strategies": {}, "dataset": None,
            "observations": _observations(observations),
        }
    current = report["current"]
    if current.get("date") != latest.isoformat():
        current = _empty_conditions(latest, "report_has_no_current_session")
    return {**report, "current": current, "observations": _observations(observations)}


def _usable(report: dict | None, symbol: str, as_of: datetime) -> bool:
    if (
        report is None or report.get("schema_version") != VERSION or report.get("symbol") != symbol
        or not isinstance(report.get("current"), dict)
        or not isinstance(report.get("signal_history"), list)
        or not isinstance(report.get("strategies"), dict)
    ):
        return False
    dataset = report.get("dataset")
    if (
        not isinstance(dataset, dict) or dataset.get("kind") != "approved"
        or dataset.get("price_adjustment") != PRICE_ADJUSTMENT or report.get("rules_sha256") != _rules_hash()
    ):
        return False
    try:
        return _timestamp(report["as_of"]) <= as_of
    except (KeyError, TypeError, ValueError):
        return False


def _validate_calendar(sessions: list[extreme_fear.Session], as_of: datetime) -> None:
    if not sessions or sessions[-1].day < _latest_day(as_of):
        raise ValueError("calendar must include the latest completed NYSE session at as_of")
    first, last = sessions[0].day, sessions[-1].day
    days = (first + timedelta(days=offset) for offset in range((last - first).days + 1))
    expected = [day for day in days if market_calendar.is_open("us", day)]
    if [session.day for session in sessions] != expected:
        raise ValueError("calendar contains a missing, extra or unordered NYSE session")


def build(raw: bytes, symbol: str) -> dict:
    """Run against an explicitly supplied dataset; inputs are never fetched by this command."""
    data = json.loads(raw)
    if data.get("schema_version") != VERSION or data.get("kind") not in ("approved", "synthetic"):
        raise ValueError("unknown dataset version or kind")
    if data.get("price_adjustment") != PRICE_ADJUSTMENT:
        raise ValueError("prices need a consistent causal forward total-return adjustment")
    sources = data["sources"]
    fields = ("name", "version", "evidence", "usage")
    if set(sources) != set(INPUT_SOURCES) or any(
        not isinstance(sources[key].get(field), str) or not sources[key][field].strip()
        for key in INPUT_SOURCES for field in fields
    ):
        raise ValueError("each source needs name, version, evidence and usage")
    as_of = _timestamp(data["as_of"])
    sessions = [
        extreme_fear.Session(date.fromisoformat(row["date"]), _timestamp(row["opens_at"]),
                             _timestamp(row["closes_at"]))
        for row in data["sessions"]
    ]
    _validate_calendar(sessions, as_of)
    bars = {
        ticker: [
            extreme_fear.Bar(date.fromisoformat(row["date"]), row["open"], row["high"], row["low"],
                             row["close"], _timestamp(row["available_at"]))
            for row in rows
        ]
        for ticker, rows in data["prices"].items()
    }
    cnn, vix = (
        [extreme_fear.Fixing(date.fromisoformat(row["date"]), row["value"], _timestamp(row["available_at"]))
         for row in data[key]]
        for key in ("cnn", "vix")
    )
    report = extreme_fear.evaluate(
        symbol, sessions, bars, cnn, vix, as_of=as_of, cost_bps=data.get("cost_bps", 10.0),
    )
    return {**report, "schema_version": VERSION, "rules_sha256": _rules_hash(),
            "dataset": {"kind": data["kind"], "sha256": hashlib.sha256(raw).hexdigest(),
                        "price_adjustment": PRICE_ADJUSTMENT,
                        "start": sessions[0].day.isoformat(), "end": sessions[-1].day.isoformat(),
                        "sources": {key: {field: sources[key][field] for field in ("name", "version")}
                                    for key in INPUT_SOURCES}}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--symbol", choices=extreme_fear.SYMBOLS, required=True)
    args = parser.parse_args()
    try:
        report = build(args.input.read_bytes(), args.symbol)
        serialized = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(serialized) > MAX_REPORT_BYTES:
            raise ValueError("generated report exceeds size cap")
        args.output.write_bytes(serialized)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
