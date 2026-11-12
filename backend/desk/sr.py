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
from zoneinfo import ZoneInfo

from . import horizons, patterns, recent, trend, volume_profile

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
BROKEN_ATR = 1.0           # a close this far through a swing level broke it: it counts half (#152)
BROKEN_FACTOR = 0.5
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


def _ages(bars: list[dict], timeframe: str, half_life: timedelta) -> list[float]:
    """Each bar's age in half-lives behind the last bar: by date for day and longer bars (as
    before horizons), by the clock for intraday bars."""
    if not bars:
        return []
    if timeframe in ("1d", "1w", "1M", "1y"):
        last = date.fromisoformat(bars[-1]["at"][:10])
        unit = half_life.total_seconds() / 86400
        return [(last - date.fromisoformat(b["at"][:10])).days / unit for b in bars]
    last_at = datetime.fromisoformat(bars[-1]["at"])
    unit = half_life.total_seconds()
    return [(last_at - datetime.fromisoformat(b["at"])).total_seconds() / unit for b in bars]


def _swings(bars: list[dict], k: int, timeframe: str, label: str, weight: float,
            half_life: timedelta = timedelta(days=HALF_LIFE_DAYS), atr: float | None = None) -> list[_Candidate]:
    """Swing highs and lows, decayed by age. With ``atr``, a level a later close went through by
    BROKEN_ATR counts BROKEN_FACTOR: the market already voted on it (its side flips with the price)."""
    out = []
    ages = _ages(bars, timeframe, half_life)
    # the highest / lowest close after each bar, once (today's ATR judges: a break by 1 ATR now)
    hi_after, lo_after = [float("-inf")] * (len(bars) + 1), [float("inf")] * (len(bars) + 1)
    for j in range(len(bars) - 1, -1, -1):
        hi_after[j] = max(hi_after[j + 1], bars[j]["close"])
        lo_after[j] = min(lo_after[j + 1], bars[j]["close"])
    for i in range(k, len(bars) - k):
        window = bars[i - k:i + k + 1]
        decayed = weight * 0.5 ** ages[i]
        if decayed < 1e-6:   # long forgotten: also keeps a zone's total weight above zero
            continue
        if bars[i]["high"] == max(b["high"] for b in window):
            broken = bool(atr and hi_after[i + 1] > bars[i]["high"] + BROKEN_ATR * atr)
            out.append(_Candidate(bars[i]["high"], timeframe, f"{label} 스윙 고점" + (" (돌파됨)" if broken else ""),
                                  decayed * (BROKEN_FACTOR if broken else 1.0)))
        if bars[i]["low"] == min(b["low"] for b in window):
            broken = bool(atr and lo_after[i + 1] < bars[i]["low"] - BROKEN_ATR * atr)
            out.append(_Candidate(bars[i]["low"], timeframe, f"{label} 스윙 저점" + (" (이탈됨)" if broken else ""),
                                  decayed * (BROKEN_FACTOR if broken else 1.0)))
    return out


def day_bars(daily: list[dict], weekly: list[dict], h: horizons.Horizon) -> list[dict] | None:
    """The horizon's daily bars: the primary on a swing, the secondary under intraday bars."""
    if h.primary == "1d":
        return daily
    return weekly if h.secondary == "1d" else None


def forming(daily: list[dict], ticker: str, now: datetime | None = None) -> bool:
    """The last daily bar is today's (on the exchange's calendar): still forming while it trades."""
    if not daily:
        return False
    zone = ZoneInfo("Asia/Seoul" if ticker.upper().endswith((".KS", ".KQ")) else "America/New_York")
    return _local_day(daily[-1]["at"], ticker) == (now or datetime.now(UTC)).astimezone(zone).date()


def _recent(days: list[dict] | None, h: horizons.Horizon, partial: bool = False) -> list[_Candidate]:
    """The last completed sessions' highs and lows as levels of their own (#152): a swing needs k
    bars on both sides, so the last k sessions - yesterday's low after a crash - are never swings.
    Swing only (the horizon the basket backtest measured; the intraday horizons already carry the
    prior day's high / low and the daily range). A forming session (``partial``) is not a level:
    its high and low still move with the price. One candidate per price: the 5-day low that is
    also yesterday's low counts once, at the larger weight, with both names."""
    if h.primary != "1d" or not days:
        return []
    done = days[:-1] if partial else days
    if len(done) < 2:
        return []
    named = [(done[-1]["high"], "최근 일봉 고가", 1.5), (done[-1]["low"], "최근 일봉 저가", 1.5),
             (done[-2]["high"], "그 전 일봉 고가", 1.0), (done[-2]["low"], "그 전 일봉 저가", 1.0)]
    for n in (5, 20):
        if len(done) >= n:
            named += [(max(b["high"] for b in done[-n:]), f"{n}일 고가", 1.0),
                      (min(b["low"] for b in done[-n:]), f"{n}일 저가", 1.0)]
    by_price: dict[float, tuple[float, list[str]]] = {}
    for price, name, weight in named:
        w, names = by_price.get(price, (0.0, []))
        by_price[price] = (max(w, weight), [*names, name])
    return [_Candidate(p, "1d", " = ".join(names), w) for p, (w, names) in by_price.items()]


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def compute(daily: list[dict], weekly: list[dict], price: float | None = None,
            weekly_complete: bool = False, *, horizon: str | None = None, partial: bool = False) -> dict:
    """``daily``/``weekly`` are bars oldest first ({"at","open","high","low","close","volume"}):
    the horizon's primary and secondary bars (desk.horizons; swing = daily and weekly).
    ``weekly_complete``: the last secondary bar has ended (otherwise it may still be trading).
    ``partial``: the last daily bar is today's session, still trading (desk.sr.forming)."""
    h = horizons.get(horizon)
    if len(daily) < ATR_DAYS + 1:
        raise ValueError(f"not enough {h.primary} bars for support/resistance")
    atr = _atr(daily) or 0.0
    reference = price if price else daily[-1]["close"]
    candidates = _swings(daily, h.primary_k, h.primary, h.primary_label, 1.0, h.half_life, atr)
    candidates += _swings(weekly, h.secondary_k, h.secondary, h.secondary_label, 2.0, h.secondary_half_life,
                          _atr(weekly))
    sessions = day_bars(daily, weekly, h)
    candidates += _recent(sessions, h, partial and h.primary == "1d")
    closes = [b["close"] for b in daily]
    for days in (20, 50, 200):
        if len(closes) >= days:
            candidates.append(_Candidate(sum(closes[-days:]) / days, h.primary, h.ma_label(days), 1.0))
    if weekly:
        year = weekly[-h.range_bars:]
        candidates.append(_Candidate(max(b["high"] for b in year), h.secondary, f"{h.range_label} 고가", 2.0))
        candidates.append(_Candidate(min(b["low"] for b in year), h.secondary, f"{h.range_label} 저가", 2.0))
    finished = weekly if weekly_complete else weekly[:-1]
    if finished:
        last = finished[-1]
        pivot = (last["high"] + last["low"] + last["close"]) / 3
        candidates += [_Candidate(last["high"], h.secondary, f"{h.last_label} 고가", 1.0),
                       _Candidate(last["low"], h.secondary, f"{h.last_label} 저가", 1.0),
                       _Candidate(pivot, h.secondary, h.pivot_label, 1.0)]

    tolerance = max(0.5 * atr, h.tolerance_pct * reference)
    zones: list[list[_Candidate]] = []
    for cand in sorted(candidates, key=lambda c: c.price):
        if zones:
            zone = zones[-1]
            centre = sum(c.price * c.weight for c in zone) / sum(c.weight for c in zone)
            if abs(cand.price - centre) <= tolerance:
                zone.append(cand)
                continue
        zones.append([cand])

    max_distance = h.max_distance_pct * reference
    if atr:
        max_distance = min(max_distance, h.max_distance_atr * atr)
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
                       "timeframe": h.secondary if any(c.timeframe == h.secondary for c in zone) else h.primary,
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
            "atr": _round(atr), "as_of": daily[-1]["at"], "levels": picked,
            "recent": recent.compare(sessions or [], partial=partial and h.primary == "1d")}


def prompt_note(sr: dict | None) -> str | None:
    """The levels as a short reference block for the agents' prompts."""
    trend_note = trend.prompt_note(sr)
    vp_line = volume_profile.prompt_line((sr or {}).get("volume_profile"))
    if vp_line:
        trend_note = "\n\n".join(part for part in (vp_line + " Thick nodes in the trade's direction are where to "
                                                     "take partial profit; thick nodes against it are where a "
                                                     "pullback should hold; a thin stretch past a broken level is "
                                                     "where price can run to the next zone.", trend_note) if part)
    if not sr or not sr.get("levels"):
        return trend_note
    lines = [f"- {'R' if lv['kind'] == 'resistance' else 'S'} {lv['price']} "
             f"({lv['timeframe']}, {lv['basis']}; strength {lv['strength']})" for lv in sr["levels"]]
    intervals = sr.get("intervals") or {"primary": "1d", "secondary": "1w"}
    frame = ("daily and weekly bars" if intervals == {"primary": "1d", "secondary": "1w"}
             else f"{intervals['primary']} and {intervals['secondary']} bars")
    if sr.get("fallback"):
        frame += f" ({sr['fallback']['from']} bars were not available for this date, so these are daily-frame levels)"
    return (f"Desk-computed support (S) and resistance (R) from {frame} "
            f"(reference price {sr['reference_price']}, ATR14 {sr['atr']}):\n" + "\n".join(lines) + "\n"
            "Use them when you set levels: an entry near a support (long) or resistance (short), a stop "
            "beyond the next zone, a target before the opposite zone. Say which zone a level relies on. "
            "They are arithmetic from price history, not a forecast."
            + ("\n\n" + trend_note if trend_note else ""))


def _bars_behind(last: str, now: str, bar_days: int) -> float:
    """How many secondary bars the primary bars' last one is past the last secondary bar: a
    finished weekly bar a week and a half before today is 1.5 bars behind; a prior-session
    daily bar under intraday bars is one session behind."""
    days = (date.fromisoformat(now[:10]) - date.fromisoformat(last[:10])).days
    if days <= 0:
        return 0.0
    return days / bar_days if bar_days >= 7 else 1.0


def add_trend(result: dict, days: list[dict], weeks: list[dict], h: horizons.Horizon,
              price: float | None) -> None:
    """Trendlines on both timeframes and the primary bars' retracement (desk.trend) onto a
    ``compute`` result. A failure leaves the horizontal levels as they are and says why."""
    try:
        atr = result["atr"] or (_atr(days) or 0.0)
        reference = price or days[-1]["close"]
        lines = trend.trendlines(days, h.primary, h.primary_k, reference, atr)
        if weeks:
            lines += trend.trendlines(weeks, h.secondary, h.secondary_k, reference, atr, _atr(weeks),
                                      extend=_bars_behind(weeks[-1]["at"], days[-1]["at"], h.secondary_days),
                                      now_at=days[-1]["at"])
        result["trendlines"] = lines
        result["fibonacci"] = trend.fibonacci(days, result["levels"], max(0.5 * atr, h.tolerance_pct * reference))
    except Exception as exc:  # noqa: BLE001 - the levels stand without the lines
        result["trendlines"], result["fibonacci"] = [], None
        result["trend_error"] = f"{type(exc).__name__}: {exc}"


def add_patterns(result: dict, days: list[dict], weeks: list[dict], h: horizons.Horizon,
                 price: float | None = None) -> None:
    """#297: a converging pattern on each timeframe (its own ATR), with its measured record
    (desk.patterns). Live only - the backtest's snapshots skip it (its plans do not read it)."""
    try:
        found = patterns.find(days, h.primary_k, result["atr"] or (_atr(days) or 0.0), h.primary, price)
        if weeks:
            found += patterns.find(weeks, h.secondary_k, _atr(weeks) or 0.0, h.secondary, price)
        result["patterns"] = found
    except Exception as exc:  # noqa: BLE001 - the levels and lines stand without it
        result["patterns"] = []
        result["patterns_error"] = f"{type(exc).__name__}: {exc}"


def add_volume_profile(result: dict, days: list[dict], h: horizons.Horizon) -> None:
    """The primary bars' volume profile (desk.volume_profile, #106) onto a ``compute`` result;
    None when the bars carry no volume (an index), a failure leaves the levels as they are."""
    try:
        result["volume_profile"] = volume_profile.profile(days, result.get("atr"), h.primary)
    except Exception as exc:  # noqa: BLE001
        result["volume_profile"] = None
        result["volume_profile_error"] = f"{type(exc).__name__}: {exc}"


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
           trade_date: str, *, secondary_days: int = 7) -> tuple[list[dict], list[dict]]:
    """Bars an analysis on ``trade_date`` could have seen: primary bars up to it, and only the
    secondary bars that had ended before it (a weekly bar carries the whole week's high and low;
    with daily secondary bars, the analysis day itself is still trading)."""
    cut = date.fromisoformat(trade_date)
    days = [b for b in daily if _local_day(b["at"], ticker) <= cut]
    weeks = [b for b in weekly if _local_day(b["at"], ticker) + timedelta(days=secondary_days) <= cut]
    return days, weeks


async def support_resistance(prices, ticker: str, trade_date: str | None = None,
                             price: float | None = None, clock=time.monotonic, *,
                             horizon: str | None = None) -> dict:
    """Levels for ``ticker`` from desk.prices bars, up to ``trade_date`` when given; cached a few
    minutes. ``price`` (a live quote) decides which side of it a zone is on. ``horizon``
    (desk.horizons) picks the bars; when its intraday bars are missing (an old date, Yahoo's
    intraday limits) the levels come from the swing frame and say so under ``fallback``."""
    h = horizons.get(horizon)
    key = (ticker, trade_date, h.id)
    cached = _CACHE.get(key)
    if price is None and cached and clock() - cached[0] < h.sr_ttl_sec:
        return cached[1]
    try:
        result = await _frame(prices, ticker, trade_date, price, h)
    except Exception as exc:
        if h.id == horizons.DEFAULT_HORIZON:
            raise
        result = await _frame(prices, ticker, trade_date, price, horizons.get(None))
        result["fallback"] = {"from": h.primary, "reason": f"{type(exc).__name__}: {exc}"}
    result["horizon"] = h.id
    if price is None:   # a result framed around a live price is not the cached, close-based one
        if len(_CACHE) > 256:
            _CACHE.clear()
        _CACHE[key] = (clock(), result)
    return result


async def frame_bars(prices, ticker: str, trade_date: str | None, h: horizons.Horizon
                     ) -> tuple[list[dict], list[dict], bool, dict, dict]:
    """The horizon's primary and secondary bars up to ``trade_date`` - the same requests (and so
    the same desk.prices cache entries) the levels are computed from (#116 reuses them).
    (primary, secondary, secondary_complete, primary_candles, secondary_candles)."""
    primary, secondary = await asyncio.gather(prices.candles(ticker, h.primary, h.primary_bars),
                                              prices.candles(ticker, h.secondary, h.secondary_bars))
    days, weeks = primary["bars"], secondary["bars"]
    if trade_date:
        days, weeks = _until(days, weeks, ticker, trade_date, secondary_days=h.secondary_days)
    complete = bool(trade_date)
    if h.secondary == "1d" and days:
        # "Prior session" = the last daily bar before the session the intraday bars are in. A KST
        # trade date runs ahead of a US session (KST 01:00 = ET noon), so cutting by the trade date
        # would keep today's still-trading daily bar.
        session = _local_day(days[-1]["at"], ticker)
        weeks = [b for b in weeks if _local_day(b["at"], ticker) < session]
        complete = True
    return days, weeks, complete, primary, secondary


async def _frame(prices, ticker: str, trade_date: str | None, price: float | None,
                 h: horizons.Horizon) -> dict:
    days, weeks, complete, primary, secondary = await frame_bars(prices, ticker, trade_date, h)
    live_day = trade_date is None or trade_date == date.today().isoformat()
    result = compute(days, weeks, price, weekly_complete=complete, horizon=h.id,
                     partial=live_day and forming(days, ticker))
    # pairs of swings × bars: off the event loop so it never stalls the streams
    await asyncio.to_thread(add_trend, result, days, weeks, h, price)
    await asyncio.to_thread(add_patterns, result, days, weeks, h, price)
    await asyncio.to_thread(add_volume_profile, result, days, h)
    result.update(ticker=ticker, sources={h.primary: primary["source"], h.secondary: secondary["source"]},
                  intervals={"primary": h.primary, "secondary": h.secondary},
                  computed_at=datetime.now(UTC).isoformat(timespec="seconds"))
    return result
