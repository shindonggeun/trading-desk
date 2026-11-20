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

from . import horizons, market_calendar, patterns, recent, sr_major, sr_zones, trend, volume_profile
from .shared_cache import TieredCache
from .ttl_cache import TTLCache

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
# #321: rank by the zone's record - held visits lift it, a role change more. Off: on the swing basket it
# moved held-out R by -0.001 (0.022 -> 0.021), so the record is shown, not ranked by.
REACTION_RANK = False
# #328: major levels from years of weekly bars (desk.sr_major) join the swing levels as tier "major".
# Off: in the event study (desk.sr_major_bt, 20 tickers, held-out from 2022-07) the price bounced at
# major supports 57.9% of first touches, at same-distance random zones 60.4% and at the current
# supports 56.2% - no edge over chance, so they are not drawn or ranked until one is shown.
MAJOR_TIER = False
MAJOR_PER_SIDE = 2          # majors kept on each side whatever the distance ranking says
LONG_WEEKS = 520            # ten years of weekly bars for them (one extra request per ticker)
FLIP_FACTOR = 1.5
CACHE_MAX = 256
# (ticker, trade date, horizon) → the close-based result, each kept its horizon's sr_ttl_sec
# #689 memory first, then the shared tier: the api, the analyst's runs and the line jobs frame the same levels
_CACHE = TieredCache("sr", TTLCache(CACHE_MAX, CACHE_TTL_SEC))


@dataclass
class _Candidate:
    price: float
    timeframe: str          # 1d | 1w
    basis: str
    weight: float
    body: float | None = None   # a swing bar's body edge: the zone spans wick and body (#321)
    recent: bool = False        # the last sessions' own highs / lows: a sweep is judged against the zone without them


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
                                  decayed * (BROKEN_FACTOR if broken else 1.0),
                                  max(bars[i]["open"], bars[i]["close"])))
        if bars[i]["low"] == min(b["low"] for b in window):
            broken = bool(atr and lo_after[i + 1] < bars[i]["low"] - BROKEN_ATR * atr)
            out.append(_Candidate(bars[i]["low"], timeframe, f"{label} 스윙 저점" + (" (이탈됨)" if broken else ""),
                                  decayed * (BROKEN_FACTOR if broken else 1.0),
                                  min(bars[i]["open"], bars[i]["close"])))
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
    return [_Candidate(p, "1d", " = ".join(names), w, recent=True) for p, (w, names) in by_price.items()]


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def _merge_majors(levels: list[dict], majors: list[dict], reference: float, max_distance: float) -> None:
    """#328: a major zone that overlaps a level of the same side makes it major (its zone the union,
    its basis the record); one that overlaps none is a level of its own."""
    for m in majors:
        lo, hi = m["zone"]
        if abs(m["price"] - reference) > max_distance:
            continue
        kind = "support" if m["price"] < reference else "resistance"
        overlap = [lv for lv in levels if lv["kind"] == kind and lv.get("tier") != "major"
                   and lv["zone"][0] <= hi and lv["zone"][1] >= lo]
        hit = min(overlap, key=lambda lv: abs(lv["price"] - m["price"]), default=None)
        if hit:
            hit["tier"] = "major"
            hit["zone"] = [min(hit["zone"][0], lo), max(hit["zone"][1], hi)]
            hit["_core"] = [min(hit["_core"][0], lo), max(hit["_core"][1], hi)]
            hit["strength"] = round(max(hit["strength"], m["score"]), 1)
            hit["basis"] = f"주요: {m['basis']} · {hit['basis']}"
        else:
            levels.append({"price": _round(m["price"]), "kind": kind, "timeframe": "1w", "strength": m["score"],
                           "basis": f"주요: {m['basis']}", "zone": [lo, hi], "_core": [lo, hi], "tier": "major"})


def compute(daily: list[dict], weekly: list[dict], price: float | None = None,
            weekly_complete: bool = False, *, horizon: str | None = None, partial: bool = False,
            long_weekly: list[dict] | None = None) -> dict:
    """``daily``/``weekly`` are bars oldest first ({"at","open","high","low","close","volume"}):
    the horizon's primary and secondary bars (desk.horizons; swing = daily and weekly).
    ``weekly_complete``: the last secondary bar has ended (otherwise it may still be trading).
    ``partial``: the last daily bar is today's session, still trading (desk.sr.forming).
    ``long_weekly``: up to ten years of finished weekly bars for the major levels (#328, swing only)."""
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
                       "strength": round(weight, 1), "basis": basis,
                       "zone": list(sr_zones.zone_of([(c.price, c.body) for c in zone], atr, level)),
                       "_core": list(sr_zones.zone_of([(c.price, c.body) for c in zone if not c.recent]
                                                      or [(c.price, c.body) for c in zone], atr, level))})
    majors = sr_major.find(long_weekly, reference) if MAJOR_TIER and long_weekly and h.primary == "1d" else []
    if majors:
        _merge_majors(levels, majors, reference, max_distance)
        for lv in levels:
            lv.setdefault("tier", "minor")
    # a forming session is no evidence yet: its visit and its "close back" are still moving
    judged = daily[:-1] if partial and h.primary == "1d" else daily
    events = sr_zones.enrich(levels, judged, atr, weekly, _atr(weekly) if weekly else None)
    for lv in levels:
        if lv["flipped"] == ("resistance_to_support" if lv["kind"] == "support" else "support_to_resistance"):
            lv["basis"] += " · " + ("저항→지지 전환" if lv["kind"] == "support" else "지지→저항 전환")
    rank = {id(lv): lv["strength"] for lv in levels}
    if REACTION_RANK:
        for lv in levels:
            if lv["touches"] >= 2:
                rank[id(lv)] *= 0.5 + lv["hold_rate"]
            if "전환" in lv["basis"]:
                rank[id(lv)] *= FLIP_FACTOR

    picked = []
    for kind in ("resistance", "support"):
        side = [lv for lv in levels if lv["kind"] == kind]
        # strong and near first: a zone two ATR away needs twice the reasons of one at the price
        side.sort(key=lambda lv: -rank[id(lv)] / (1 + abs(lv["price"] - reference) / unit))
        kept = side[:PER_SIDE]
        # #328: the nearest majors stay whatever the ranking - a close weak line must not push out the big zone
        near = sorted((lv for lv in side if lv.get("tier") == "major" and lv not in kept),
                      key=lambda lv: abs(lv["price"] - reference))
        need = MAJOR_PER_SIDE - sum(lv.get("tier") == "major" for lv in kept)
        picked += kept + near[:max(0, need)]
    picked.sort(key=lambda lv: -lv["price"])
    sr_zones.add_buy_zones(picked, atr)
    for lv in levels:
        lv.pop("_core", None)
    return {"reference_price": _round(reference), "reference": "live" if price else "close",
            "last_close": _round(daily[-1]["close"]),
            "atr": _round(atr), "as_of": daily[-1]["at"], "levels": picked,
            "events": [e for e in events if any(e["level"] == lv["price"] for lv in picked)],
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
             f"({lv['timeframe']}, {lv['basis']}; strength {lv['strength']}{_zone_text(lv)})" for lv in sr["levels"]]
    majors = [lv for lv in sr["levels"] if lv.get("tier") == "major" and lv.get("zone")]
    if majors:   # #328: the big zones first - entries and stops are set around them
        lines = [f"- MAJOR {'support' if lv['kind'] == 'support' else 'resistance'} zone {lv['zone'][0]}-{lv['zone'][1]} "
                 f"({lv['basis']}): a zone the price turned at in several separate years; set a long's entry "
                 "in it and its stop below it, never inside it" if lv["kind"] == "support" else
                 f"- MAJOR resistance zone {lv['zone'][0]}-{lv['zone'][1]} ({lv['basis']}): take profit before it"
                 for lv in majors] + lines
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
            + (" A zone is a range, not a line: a long's stop goes below the zone's low (its wicks included), "
               "never inside it, or one wick through the zone stops the trade out before it turns."
               if any(lv.get("zone") for lv in sr["levels"]) else "")
            + _sweep_text(sr.get("events"))
            + ("\n\n" + trend_note if trend_note else ""))


def _zone_text(lv: dict) -> str:
    """#321: a level's zone, record and buy zone for the prompt line (older saved levels have none)."""
    if not lv.get("zone"):
        return ""
    out = f"; zone {lv['zone'][0]}-{lv['zone'][1]}"
    if lv.get("touches"):
        out += f", held {lv['held']} of {lv['touches']} past visits"
    bz = lv.get("buy_zone")
    if bz:
        out += f"; buy zone stop {bz['stop']}" + ("" if bz["ok"] else " (thin record or reward below risk)")
    return out


def _sweep_text(events: list[dict] | None) -> str:
    sweeps = [e for e in events or [] if e.get("kind") == "sweep_reclaim"]
    if not sweeps:
        return ""
    e = sweeps[-1]
    if e["side"] == "support":
        moved = f"took out the prior lows {'through' if e.get('through') else 'into'} the support zone at {e['level']}"
        back = "closed back above it"
    else:
        moved = f"took out the prior highs {'through' if e.get('through') else 'into'} the resistance zone at {e['level']}"
        back = "closed back below it"
    return f" On {e['at'][:10]} the price {moved} and {back} (a sweep and reclaim: the stops there were run)."


def _bars_behind(last: str, now: str, bar_days: int) -> float:
    """How many secondary bars the primary bars' last one is past the last secondary bar: a
    finished weekly bar a week and a half before today is 1.5 bars behind; a prior-session
    daily bar under intraday bars is one session behind."""
    days = (date.fromisoformat(now[:10]) - date.fromisoformat(last[:10])).days
    if days <= 0:
        return 0.0
    return days / bar_days if bar_days >= 7 else 1.0


def add_trend(result: dict, days: list[dict], weeks: list[dict], h: horizons.Horizon,
              price: float | None, *, annotate: bool = True) -> None:
    """Trendlines on both timeframes and the primary bars' retracement (desk.trend) onto a
    ``compute`` result. A failure leaves the horizontal levels as they are and says why.
    ``annotate`` False leaves out the lines' scenarios and sentences (the backtest reads only
    where the lines are)."""
    try:
        atr = result["atr"] or (_atr(days) or 0.0)
        reference = price or days[-1]["close"]
        lines = trend.trendlines(days, h.primary, h.primary_k, reference, atr, scales=trend.SCALES)
        frames = {h.primary: {"bars": days, "k": h.primary_k, "atr": atr}}
        if weeks:
            weekly_atr = _atr(weeks)
            lines += trend.trendlines(weeks, h.secondary, h.secondary_k, reference, atr, weekly_atr,
                                      extend=_bars_behind(weeks[-1]["at"], days[-1]["at"], h.secondary_days),
                                      now_at=days[-1]["at"], scales=trend.SCALES)
            frames[h.secondary] = {"bars": weeks, "k": h.secondary_k, "atr": weekly_atr or atr}
        lines = trend.select(lines)
        if annotate:
            trend.annotate(lines, result["levels"], reference, frames)
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
    # #695 head and shoulders on both timeframes, the Minsky-style stage on the weekly bars (when weekly)
    from . import bubble_stage, hs_pattern

    try:
        hs_found = hs_pattern.find(days, h.primary_k, result["atr"] or (_atr(days) or 0.0), h.primary, price)
        if weeks:
            hs_found += hs_pattern.find(weeks, h.secondary_k, _atr(weeks) or 0.0, h.secondary, price)
        result["hs"] = hs_found
    except Exception as exc:  # noqa: BLE001
        result["hs"] = []
        result["patterns_error"] = result.get("patterns_error") or f"{type(exc).__name__}: {exc}"
    try:
        result["bubble"] = bubble_stage.stage(weeks) if weeks and h.secondary == "1w" else None
    except Exception:  # noqa: BLE001
        result["bubble"] = None


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
    return market_calendar.bar_day(at, ticker.upper().endswith((".KS", ".KQ")), naive_is_utc=True)


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
    cached = await _CACHE.aget(key, now=clock()) if price is None else None
    if cached is not None:
        return cached
    try:
        result = await _frame(prices, ticker, trade_date, price, h)
    except Exception as exc:
        if h.id == horizons.DEFAULT_HORIZON:
            raise
        result = await _frame(prices, ticker, trade_date, price, horizons.get(None))
        result["fallback"] = {"from": h.primary, "reason": f"{type(exc).__name__}: {exc}"}
    result["horizon"] = h.id
    if price is None:   # a result framed around a live price is not the cached, close-based one
        await _CACHE.aput(key, result, h.sr_ttl_sec, now=clock())
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


async def _long_weeks(prices, ticker: str, trade_date: str | None, days: list[dict],
                      complete: bool) -> list[dict] | None:
    """#328: ten years of weekly bars, finished by ``trade_date`` - None when the source has none."""
    try:
        got = (await prices.candles(ticker, "1w", LONG_WEEKS))["bars"]
    except Exception:  # noqa: BLE001 - the major levels are extra; the swing levels stand without them
        return None
    if trade_date:
        _, got = _until(days, got, ticker, trade_date)
    return got if complete else got[:-1]


async def _frame(prices, ticker: str, trade_date: str | None, price: float | None,
                 h: horizons.Horizon) -> dict:
    days, weeks, complete, primary, secondary = await frame_bars(prices, ticker, trade_date, h)
    live_day = trade_date is None or trade_date == date.today().isoformat()
    long_weekly = await _long_weeks(prices, ticker, trade_date, days, complete) \
        if MAJOR_TIER and h.primary == "1d" else None
    result = compute(days, weeks, price, weekly_complete=complete, horizon=h.id,
                     partial=live_day and forming(days, ticker), long_weekly=long_weekly)
    # pairs of swings × bars: off the event loop so it never stalls the streams
    await asyncio.to_thread(add_trend, result, days, weeks, h, price)
    await asyncio.to_thread(add_patterns, result, days, weeks, h, price)
    await asyncio.to_thread(add_volume_profile, result, days, h)
    result.update(ticker=ticker, sources={h.primary: primary["source"], h.secondary: secondary["source"]},
                  intervals={"primary": h.primary, "secondary": h.secondary},
                  computed_at=datetime.now(UTC).isoformat(timespec="seconds"))
    return result
