"""KOSPI / KOSDAQ program trading for today from KIS (#677): the program trading summary by time
(``FHPPG04600101``, ``comp-program-trade-today``) - arbitrage, non-arbitrage and their total, net buy value.

Read inside the futures snapshot (desk.futures_history), once a minute per market, on the futures' KIS client
(one token for all: no client of its own). The REST answer, not the ``H0UPPGM0`` stream: the snapshot runs in
the collector, the stream in the api process (#688), and the stream's subscription cap is shared.

- The answer's ``output1`` (``output`` taken too) is the last half hour by ``bsop_hour``; each row carries the
  running totals at that time (``arbt_`` / ``nabt_`` / ``whol_smtn_ntby_tr_pbmn``). The newest row that is
  today's is kept: from 09:00 up to now - before 15:30 a later time is yesterday's close (KIS answers it till
  the next session), after 15:30 KIS repeats the close up to 17:00 (all the same figures).
- KRX (``J``) only: KIS' own example's market; NXT's program trades are not in it.
- The unit is million won as in KIS' other value columns, but neither KIS' example nor the other clients
  say so: every answer is checked (``check``) - total = arbitrage + non-arbitrage, and no figure past
  ``CEILING_100M`` (a won answer reads thousands of 조; a thousand-won one only once it is big, so small
  early figures could pass) - and each market's first answer is logged once (INFO: the answer's keys, the
  row's keys, its time and three figures - market data, no secret) to be matched against HTS / KRX after
  a deploy.
- KRX holidays on a weekday: nothing is asked (KIS would answer the last session's figures).
- Warnings are logged when a market's trouble changes (a KIS refusal or a bad unit is not 800 lines a day).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from . import market_calendar

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
PATH = "/uapi/domestic-stock/v1/quotations/comp-program-trade-today"
TR = "FHPPG04600101"
EXCHANGE = "J"                                  # KRX
MARKETS = {"kospi": "K", "kosdaq": "Q"}
KINDS = {"arbitrage": "arbt", "non_arbitrage": "nabt", "total": "whol"}
COLUMNS = [f"{m}_program_{k}_100m" for m in MARKETS for k in KINDS]
MN = 1_000_000                                  # KIS values in million won (checked, see above)
CEILING_100M = 50_000.0                         # 5조 net in a day: past it the unit is not million won
SUM_SLACK_100M = 1.0                            # total vs arbitrage + non-arbitrage, rounding of the parts
OPEN, KIS_CLOSE = time(9, 0), time(15, 30)
AHEAD_SEC = 60                                  # a row a little ahead of our clock is still now's
TIMEOUT_SEC = 3.0                               # a slow KIS must not hold the snapshot

_shown: set[str] = set()                        # markets whose answer's shape was logged
_trouble: dict[str, str | None] = {}            # a market's last warning, logged again only when it changes


def _warn(market: str, why: str | None) -> None:
    if _trouble.get(market) != why:
        if why:
            log.warning("program trade %s %s", market, why)
        elif _trouble.get(market):
            log.info("program trade %s back", market)
        _trouble[market] = why


def hours(now: datetime, is_open=market_calendar.is_open) -> bool:
    """KRX trading days from the 09:00 bell: before it (or on a holiday) the answer is the last session's."""
    local = now.astimezone(KST)
    return local.weekday() < 5 and local.time() >= OPEN and is_open("kr", local.date())


def _num(v) -> float | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hms(v) -> time | None:
    s = str(v or "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    try:
        return time(int(s[:2]), int(s[2:4]), int(s[4:]))
    except ValueError:
        return None


def _rows(answer: dict | None) -> list[dict]:
    out = (answer or {}).get("output1")
    if out is None:
        out = (answer or {}).get("output")
    rows = out if isinstance(out, list) else [out] if isinstance(out, dict) else []
    return [r for r in rows if isinstance(r, dict)]


def latest(answer: dict | None, now: datetime) -> dict | None:
    """Today's newest row of the answer: ``{"at": "HH:MM:SS", arbitrage, non_arbitrage, total}`` in 100
    million KRW, or None (no today's row, an empty one)."""
    local = now.astimezone(KST)
    upto = (datetime.combine(local.date(), local.time(), KST) + timedelta(seconds=AHEAD_SEC)).time()
    best: tuple[time, dict] | None = None
    for r in _rows(answer):
        at = _hms(r.get("bsop_hour"))
        if at is None or at < OPEN:
            continue
        if local.time() < KIS_CLOSE and at > upto:     # yesterday's close, still answered before ours
            continue
        if best is None or at > best[0]:
            best = (at, r)
    if best is None:
        return None
    at, r = best
    vals = {k: _num(r.get(f"{p}_smtn_ntby_tr_pbmn")) for k, p in KINDS.items()}
    if all(v is None for v in vals.values()):
        return None
    return {"at": at.isoformat(), **{k: None if v is None else round(v * MN / 1e8, 2) for k, v in vals.items()}}


def check(got: dict) -> str | None:
    """Why a row is not believable (a unit or shape other than assumed), or None."""
    if any(v is not None and abs(v) > CEILING_100M for v in (got[k] for k in KINDS)):
        return f"a figure past {CEILING_100M:,.0f} (100 million KRW): the unit is not million won"
    parts = (got["arbitrage"], got["non_arbitrage"], got["total"])
    if None not in parts and abs(parts[0] + parts[1] - parts[2]) > SUM_SLACK_100M:
        return "the total is not arbitrage + non-arbitrage"
    return None


def read(answer: dict | None, market: str, now: datetime) -> dict | None:
    """One market's answer -> its checked newest row, or None (logged)."""
    rows = _rows(answer)
    if market not in _shown:
        _shown.add(market)
        first = rows[0] if rows else {}
        log.info("program trade %s: answer keys %s, %d row(s), row keys %s, first %s %s",
                 market, sorted(answer or {})[:10], len(rows), sorted(first)[:40], first.get("bsop_hour"),
                 {p: first.get(f"{p}_smtn_ntby_tr_pbmn") for p in KINDS.values()})
    if not rows:
        _warn(market, "answered no rows")
        return None
    got = latest(answer, now)
    if got is None:
        _warn(market, None)              # nothing of today's yet is normal just after the bell
        return None
    why = check(got)
    _warn(market, f"dropped: {why}" if why else None)
    return None if why else got


async def fetch(rest, now: datetime) -> dict:
    """Both markets' checked rows (``{"kospi": row | None, "kosdaq": ...}``); a failure is that market's None."""
    async def one(cls: str):
        # shielded: past TIMEOUT_SEC the snapshot goes on, but a token refresh inside the call is not cut off
        return await asyncio.wait_for(asyncio.shield(rest.get(PATH, TR, {
            "FID_COND_MRKT_DIV_CODE": EXCHANGE, "FID_MRKT_CLS_CODE": cls, "FID_SCTN_CLS_CODE": "",
            "FID_INPUT_ISCD": "", "FID_COND_MRKT_DIV_CODE1": "", "FID_INPUT_HOUR_1": ""})), TIMEOUT_SEC)

    got = await asyncio.gather(*(one(cls) for cls in MARKETS.values()), return_exceptions=True)
    out: dict[str, dict | None] = {}
    for market, answer in zip(MARKETS, got, strict=True):
        if isinstance(answer, BaseException):
            _warn(market, "failed: " + (type(answer).__name__ if isinstance(answer, TimeoutError)
                                        else str(answer)[:120]))
            out[market] = None
        else:
            out[market] = read(answer, market, now)
    return out


def cols(program: dict | None) -> dict:
    """The snapshot columns for ``fetch``'s answer (None where a market has none)."""
    return {f"{m}_program_{k}_100m": ((program or {}).get(m) or {}).get(k) for m in MARKETS for k in KINDS}
