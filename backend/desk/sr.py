"""Support and resistance from daily and weekly bars.

Arithmetic on price history, not a forecast: swing highs/lows (a bar whose high or low is
the extreme of its neighbours), the 52-week range, last week's high/low and pivot, and the
20/50/200-day moving averages. Candidates within about half an ATR of each other are one
zone; a zone's strength is how many reasons put a level there (weekly swings count double).
The strongest zones on each side of the reference price are kept.

The same levels go to the chart (GET /api/levels) and, as a reference, to the agents that
set entry and stop (desk.profile), and are saved with the plan.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

DAILY_BARS = 200
WEEKLY_BARS = 156          # three years
ATR_DAYS = 14
SWING_DAILY = 3            # bars on each side that must be lower/higher
SWING_WEEKLY = 2
PER_SIDE = 4
# Zones further than the nearer of these from the price are not actionable for a days-to-weeks trade.
MAX_DISTANCE_ATR = 6.0
MAX_DISTANCE_PCT = 0.35
HALF_LIFE_DAYS = 365.0     # a swing a year old counts half: an old, busy range must not outrank today
CACHE_TTL_SEC = 300.0
_CACHE: dict[tuple[str, str | None], tuple[float, dict]] = {}


@dataclass
class _Candidate:
    price: float
    timeframe: str          # 1d | 1w
    basis: str
    weight: float


def _atr(bars: list[dict]) -> float | None:
    if len(bars) < ATR_DAYS + 1:
        return None
    ranges = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
              for p, b in pairwise(bars)]
    atr = sum(ranges[:ATR_DAYS]) / ATR_DAYS
    for tr in ranges[ATR_DAYS:]:
        atr = (atr * (ATR_DAYS - 1) + tr) / ATR_DAYS
    return atr


def _swings(bars: list[dict], k: int, timeframe: str, label: str, weight: float) -> list[_Candidate]:
    out = []
    last = date.fromisoformat(bars[-1]["at"][:10]) if bars else None
    for i in range(k, len(bars) - k):
        window = bars[i - k:i + k + 1]
        age = (last - date.fromisoformat(bars[i]["at"][:10])).days
        decayed = weight * 0.5 ** (age / HALF_LIFE_DAYS)
        if bars[i]["high"] == max(b["high"] for b in window):
            out.append(_Candidate(bars[i]["high"], timeframe, f"{label} 스윙 고점", decayed))
        if bars[i]["low"] == min(b["low"] for b in window):
            out.append(_Candidate(bars[i]["low"], timeframe, f"{label} 스윙 저점", decayed))
    return out


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def compute(daily: list[dict], weekly: list[dict], price: float | None = None,
            weekly_complete: bool = False) -> dict:
    """``daily``/``weekly`` are bars oldest first ({"at","open","high","low","close","volume"}).
    ``weekly_complete``: the last weekly bar has ended (otherwise it may still be trading)."""
    if len(daily) < ATR_DAYS + 1:
        raise ValueError("not enough daily bars for support/resistance")
    atr = _atr(daily) or 0.0
    reference = price if price else daily[-1]["close"]
    candidates = _swings(daily, SWING_DAILY, "1d", "일봉", 1.0)
    candidates += _swings(weekly, SWING_WEEKLY, "1w", "주봉", 2.0)
    closes = [b["close"] for b in daily]
    for days in (20, 50, 200):
        if len(closes) >= days:
            candidates.append(_Candidate(sum(closes[-days:]) / days, "1d", f"{days}일선", 1.0))
    if weekly:
        year = weekly[-52:]
        candidates.append(_Candidate(max(b["high"] for b in year), "1w", "52주 고가", 2.0))
        candidates.append(_Candidate(min(b["low"] for b in year), "1w", "52주 저가", 2.0))
    finished = weekly if weekly_complete else weekly[:-1]
    if finished:
        last = finished[-1]
        pivot = (last["high"] + last["low"] + last["close"]) / 3
        candidates += [_Candidate(last["high"], "1w", "지난주 고가", 1.0),
                       _Candidate(last["low"], "1w", "지난주 저가", 1.0),
                       _Candidate(pivot, "1w", "주간 피벗", 1.0)]

    tolerance = max(0.5 * atr, 0.005 * reference)
    zones: list[list[_Candidate]] = []
    for cand in sorted(candidates, key=lambda c: c.price):
        if zones:
            zone = zones[-1]
            centre = sum(c.price * c.weight for c in zone) / sum(c.weight for c in zone)
            if abs(cand.price - centre) <= tolerance:
                zone.append(cand)
                continue
        zones.append([cand])

    max_distance = MAX_DISTANCE_PCT * reference
    if atr:
        max_distance = min(max_distance, MAX_DISTANCE_ATR * atr)
    unit = 2 * atr if atr else 0.01 * reference
    levels = []
    for zone in zones:
        weight = sum(c.weight for c in zone)
        level = sum(c.price * c.weight for c in zone) / weight
        if abs(level - reference) > max_distance:
            continue
        counts: dict[str, int] = {}
        for c in zone:
            counts[c.basis] = counts.get(c.basis, 0) + 1
        basis = " · ".join(f"{name} ×{n}" if n > 1 else name
                           for name, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        levels.append({"price": _round(level), "kind": "support" if level < reference else "resistance",
                       "timeframe": "1w" if any(c.timeframe == "1w" for c in zone) else "1d",
                       "strength": round(weight, 1), "basis": basis})

    picked = []
    for kind in ("resistance", "support"):
        side = [lv for lv in levels if lv["kind"] == kind]
        # strong and near first: a zone two ATR away needs twice the reasons of one at the price
        side.sort(key=lambda lv: -lv["strength"] / (1 + abs(lv["price"] - reference) / unit))
        picked += side[:PER_SIDE]
    picked.sort(key=lambda lv: -lv["price"])
    return {"reference_price": _round(reference), "reference": "live" if price else "close",
            "last_close": _round(daily[-1]["close"]),
            "atr": _round(atr), "as_of": daily[-1]["at"], "levels": picked}


def prompt_note(sr: dict | None) -> str | None:
    """The levels as a short reference block for the agents' prompts."""
    if not sr or not sr.get("levels"):
        return None
    lines = [f"- {'R' if lv['kind'] == 'resistance' else 'S'} {lv['price']} "
             f"({lv['timeframe']}, {lv['basis']}; strength {lv['strength']})" for lv in sr["levels"]]
    return ("Desk-computed support (S) and resistance (R) from daily and weekly bars "
            f"(reference price {sr['reference_price']}, ATR14 {sr['atr']}):\n" + "\n".join(lines) + "\n"
            "Use them when you set levels: an entry near a support (long) or resistance (short), a stop "
            "beyond the next zone, a target before the opposite zone. Say which zone a level relies on. "
            "They are arithmetic from price history, not a forecast.")


def live_note(live: dict | None, atr: float | None = None) -> str | None:
    """The current price for the deciding agents, so levels start from where the market is now
    rather than from a close days old or a zone the price left long ago."""
    if not live:
        return None
    atr_part = f", ATR14 {atr}" if atr else ""
    return (f"Current market price (live quote from {live['source']} at {live['fetched_at']} UTC): "
            f"{live['price']}{atr_part}. This is where the trade starts. An Entry Price within about "
            "half an ATR of it is an entry now; one further away is a conditional order (a limit on a "
            "pullback or rally, or a stop on a breakout/breakdown) — say which, what confirms it, and "
            "what the owner does if the price never gets there. Do not anchor the entry on an old "
            "zone the price has already moved well away from; prefer a level the price can reach "
            "within the holding period, and keep stop and target consistent with the current price.")


def _local_day(at: str, ticker: str) -> date:
    """The market's own date of a bar. Korean bars open at local midnight, which is 15:00 UTC the
    day before; US bars (midnight New York = 04:00/05:00 UTC) already carry their own date."""
    stamp = datetime.fromisoformat(at)
    if ticker.upper().endswith((".KS", ".KQ")):
        stamp += timedelta(hours=9)
    return stamp.date()


def _until(daily: list[dict], weekly: list[dict], ticker: str,
           trade_date: str) -> tuple[list[dict], list[dict]]:
    """Bars an analysis on ``trade_date`` could have seen: days up to it, and only the weeks that
    had ended before it (a weekly bar carries the whole week's high and low)."""
    cut = date.fromisoformat(trade_date)
    days = [b for b in daily if _local_day(b["at"], ticker) <= cut]
    weeks = [b for b in weekly if _local_day(b["at"], ticker) + timedelta(days=7) <= cut]
    return days, weeks


async def support_resistance(prices, ticker: str, trade_date: str | None = None,
                             price: float | None = None, clock=time.monotonic) -> dict:
    """Levels for ``ticker`` from desk.prices bars, up to ``trade_date`` when given; cached a few
    minutes. ``price`` (a live quote) decides which side of it a zone is on."""
    key = (ticker, trade_date)
    cached = _CACHE.get(key)
    if price is None and cached and clock() - cached[0] < CACHE_TTL_SEC:
        return cached[1]
    daily, weekly = await asyncio.gather(prices.candles(ticker, "1d", DAILY_BARS),
                                         prices.candles(ticker, "1w", WEEKLY_BARS))
    days, weeks = daily["bars"], weekly["bars"]
    if trade_date:
        days, weeks = _until(days, weeks, ticker, trade_date)
    result = compute(days, weeks, price, weekly_complete=bool(trade_date))
    result.update(ticker=ticker, sources={"1d": daily["source"], "1w": weekly["source"]},
                  computed_at=datetime.now(UTC).isoformat(timespec="seconds"))
    if price is None:   # a result framed around a live price is not the cached, close-based one
        if len(_CACHE) > 256:
            _CACHE.clear()
        _CACHE[key] = (clock(), result)
    return result
