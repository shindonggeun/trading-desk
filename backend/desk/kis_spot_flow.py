"""KOSPI / KOSDAQ spot investor flow for today from KIS (#676): the market investor trend screen (HTS [0403],
``FHPTJ04030000``) that desk.futures_flow already reads for the KOSPI200 futures - only the market codes differ.

The exchange's provisional figures, asked on the futures' KIS client (one token for both: KIS issues one a
minute and notifies on each, so no client of its own). Toss' daily record can sit unchanged for 10-30 minutes
(#673); this is the intraday source desk.market_flow puts first, Toss after it.

- The answer's first row is the running total: per class the buy / sell value (``*_shnu_tr_pbmn`` /
  ``*_seln_tr_pbmn``, million won) -> the same record shape as Toss' (KRW), marked ``source: "kis"``.
- KIS gives no time of its own: ``updated_at`` is None (desk.market_flow watches when the figures change).
- The market codes (KSP / 0001, KSQ / 1001) follow the futures pair (K2I / F001) on the same screen; every
  answer is checked (``check``) so a wrong code, unit or day falls back to Toss instead of showing another
  market's or yesterday's figures. Running totals only grow and Toss' may sit 10-30 minutes behind, so
  against Toss' today record only a floor holds (RATIO_MIN); the ceiling is yesterday's whole day
  (DAY_MAX), and before 09:00 the pre-market cannot reach half of yesterday (PRE_MAX_SHARE - a repeat of
  yesterday's totals would). ``verified``: a check against a Toss figure big enough to say something.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

CODES = {"KOSPI": ("KSP", "0001"), "KOSDAQ": ("KSQ", "1001")}
MN = 1_000_000                 # KIS values are in million won
# Toss' classes (desk.broker.base.INVESTORS / INSTITUTION_KINDS) -> KIS column prefixes
CLASSES = {"individual": "prsn", "foreigner": "frgn", "institution": "orgn", "other_corporation": "etc_corp"}
KINDS = {"financial_investment": "scrt", "insurance": "insu", "trust": "ivtr", "private_equity_fund": "pe_fund",
         "bank": "bank", "other_financial_institution": "mrbn", "pension_fund": "fund"}
REQUIRED = ("individual", "foreigner", "institution")
RATIO_MIN = 0.5                # KIS individual buy at least this share of Toss' today (Toss lags, never leads)
DAY_MAX = 3.0                  # ... and at most this many times yesterday's whole day
PRE_MAX_SHARE = 0.5            # before 09:00: under this share of yesterday's whole day
CHECK_MIN_KRW = 10_000_000_000     # below 100억 of Toss individual buy the floor says nothing (early minutes)
TIMEOUT_SEC = 3.0              # a slow KIS must not hold Toss' answer (the snapshot waits 10 s for both)
HOLD_SEC = 300.0               # a KIS failure shows the last KIS record this long rather than Toss' older one
HOURS = ((8, 0), (16, 0))      # pre-market through the closing snapshot (desk.futures_history, 15:46)


def _side(row: dict, prefix: str) -> dict | None:
    from . import futures_flow  # here: desk.futures_flow imports desk.market_flow, which imports this

    buy, sell = futures_flow._num(row.get(f"{prefix}_shnu_tr_pbmn")), futures_flow._num(row.get(f"{prefix}_seln_tr_pbmn"))
    if buy is None or sell is None:
        return None
    buy, sell = round(buy * MN), round(sell * MN)
    return {"buy": buy, "sell": sell, "net": buy - sell}


def record(output, today: str) -> dict | None:
    """Today's record from the answer's ``output`` (Toss' shape, KRW), or None when it lacks a main class or
    nothing traded yet."""
    rows = output if isinstance(output, list) else [output] if isinstance(output, dict) else []
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None
    row = rows[0]
    investors = {name: s for name, prefix in CLASSES.items() if (s := _side(row, prefix)) is not None}
    if any(name not in investors for name in REQUIRED) or not any(s["buy"] for s in investors.values()):
        return None
    return {"date": today, "updated_at": None, "in_progress": True, "provisional": True, "investors": investors,
            "institution_breakdown": {kind: s for kind, prefix in KINDS.items()
                                      if (s := _side(row, prefix)) is not None},
            "source": "kis"}


def hours(now: datetime) -> bool:
    """Weekdays HOURS[0] ~ HOURS[1] KST: when KIS is asked."""
    local = now.astimezone(KST)
    return local.weekday() < 5 and HOURS[0] <= (local.hour, local.minute) < HOURS[1]


def _buy(record: dict | None) -> int:
    return (((record or {}).get("investors") or {}).get("individual") or {}).get("buy") or 0


def check(kis: dict, toss_today: dict | None, toss_before: dict | None, pre: bool) -> str | None:
    """None when the KIS record may be shown, else why not (Toss' records: today's and the day before)."""
    ours, before = _buy(kis), _buy(toss_before)
    if before:
        if ours > DAY_MAX * before:
            return f"individual buy {ours} over {DAY_MAX:g}x yesterday's {before}"
        if pre and ours >= PRE_MAX_SHARE * before:
            return f"individual buy {ours} before 09:00 near yesterday's {before} (yesterday's totals?)"
    if toss_today and toss_today.get("date") == kis["date"]:
        theirs = _buy(toss_today)
        if theirs >= CHECK_MIN_KRW and ours < RATIO_MIN * theirs:
            return f"individual buy {ours} under Toss' {theirs}"
    return None


def verified(kis: dict, toss_today: dict | None) -> bool:
    """The check met a Toss figure of the same day big enough to say something (``check`` passed)."""
    return bool(toss_today) and toss_today.get("date") == kis["date"] and _buy(toss_today) >= CHECK_MIN_KRW


async def fetch(rest, market: str) -> dict:
    """The raw KIS answer for ``market`` (KOSPI / KOSDAQ)."""
    from . import futures_flow

    code, sector = CODES[market]
    return await rest.get(futures_flow.INVESTOR_PATH, futures_flow.INVESTOR_TR,
                          {"FID_INPUT_ISCD": code, "FID_INPUT_ISCD_2": sector})
