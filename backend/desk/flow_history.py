"""#389: KOSPI / KOSDAQ daily net buying by investor class, back to 2019, kept as a file.

The live market-flow panel (desk.market_flow) reads only Toss' first 100 daily records. This pages
further back with ``nextUntil`` so the direction score's flow part (desk.direction ``_flow_part``,
weight 0) can be measured on years of plans. Toss answers only from its registered IP, so the
collection runs on the server::

    python -m desk.cli flow-history --market all --from 2019-01-01 --out-dir data/flow

One file per market, ``<dir>/<market>.json``: {"version", "market", "interval": "1d", "updated_at",
"rows": {"YYYY-MM-DD": {investor: net KRW}}} - net = buy - sell per INVESTORS class. The file is
written after every page, so a stopped run keeps what it got; the next run first fills the newest
gap (down to a day it already has) and then carries on below the oldest day it has. Today's row
(KST) is left out - it is provisional until the evening confirmation. Toss' own 429 Retry-After is
honoured by the broker; this adds a pause between pages on top.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .broker.base import Broker

VERSION = 1
MARKETS = ("KOSPI", "KOSDAQ")
PAGE = 100                 # Toss' most records per call
PAUSE_SEC = 1.0            # between pages, on top of the broker's 429 handling
MAX_PAGES = 400            # a guard: 2019 to now is about 20 pages a market
WINDOW = 20                # sessions the direction's flow part sums


def path_for(root: Path | str, market: str) -> Path:
    return Path(root) / f"{market.lower()}.json"


def market_of(ticker: str) -> str | None:
    """The market whose flow goes with a ticker: KOSPI for .KS, KOSDAQ for .KQ, None otherwise."""
    t = ticker.upper()
    return "KOSPI" if t.endswith(".KS") else "KOSDAQ" if t.endswith(".KQ") else None


def load(path: Path) -> dict | None:
    """The file as written, or None when it is missing or not a flow file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rows"), dict) else None


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    out = {**data, "rows": dict(sorted(data["rows"].items()))}     # sorted on disk; ``data`` keeps its dict
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8", newline="\n")
    os.replace(tmp, path)            # a stop mid-write leaves the last whole file


async def collect(broker: Broker, market: str, start: date, path: Path, *, today: date,
                  pause: float = PAUSE_SEC, sleep: Callable[[float], Awaitable] = asyncio.sleep,
                  say: Callable[[str], None] = print) -> dict:
    """Page ``market``'s daily records from the newest back to ``start`` into ``path``; returns the
    file's data. Days before ``start`` and from ``today`` on are not kept."""
    if market not in MARKETS:
        raise ValueError(f"market must be one of {MARKETS}")
    data = load(path) or {"version": VERSION, "market": market, "interval": "1d", "rows": {}}
    rows: dict[str, dict] = data["rows"]
    first, last = start.isoformat(), today.isoformat()

    async def walk(until: date | None, stop_at_known: bool) -> None:
        for page in range(MAX_PAGES):
            if page:
                await sleep(pause)
            records, next_until = await broker.investor_flow(market, "1d", PAGE, until=until)
            known = added = 0
            for rec in records:
                if rec.date in rows:
                    known += 1
                elif first <= rec.date < last:
                    rows[rec.date] = {k: int(buy - sell) for k, (buy, sell) in rec.investors.items()}
                    added += 1
            _save(path, data)
            oldest = min((r.date for r in records), default=None)
            say(f"{market} {oldest or '-'} ~ : +{added} (모두 {len(rows)}일)")
            nxt = date.fromisoformat(next_until) if next_until else None
            if not records or nxt is None or oldest < first or (stop_at_known and known) \
                    or (not stop_at_known and not added) or nxt == until:
                return
            until = nxt

    had = bool(rows)
    await walk(None, stop_at_known=had)               # the newest days, down to one already kept
    oldest = min(rows) if rows else None
    if had and oldest and oldest > first:              # then on below the oldest kept day
        await walk(date.fromisoformat(oldest) - timedelta(days=1), stop_at_known=False)
    return data


def window(data: dict | None, day: str, investor: str = "foreigner", n: int = WINDOW) -> list[int]:
    """The ``investor`` net of the last ``n`` kept sessions strictly before ``day`` (oldest first):
    what was known on the morning of ``day``. Empty without a file."""
    if not data:
        return []
    days = [d for d in data["rows"] if d < day]
    return [data["rows"][d][investor] for d in sorted(days)[-n:] if investor in data["rows"][d]]
