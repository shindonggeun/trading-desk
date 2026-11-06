"""Two-sided playbooks (#116): the regime from the bars, then a LONG and a SHORT plan at once.

In a crash the owner trades both ways from one analysis: a long "dip catch" (하따 — scale into a
strong support / volume zone, add only after the bounce shows, take the first targets, cut fast
on a time stop) and a short scalp (fade a bounce that fails at resistance, or ride a support
breakdown; flat before the close). In other regimes the same two slots become a pullback buy
and an overextension scalp.

Every number here is the desk's arithmetic on the bars the support/resistance step already
fetched (desk.sr.frame_bars, cached): no extra network call, no LLM. The deciding agents get
the candidates as a note and answer with three English-label lines (``mandate``) that are read
back (``parse``) and accepted only where they stay sane (``merge``). Scoring is in desk.outcome.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from itertools import pairwise

from . import horizons
from .indicators import rsi

VERSION = 1
MIN_DAILY = 60

# zones and levels, in the frame's ATR
MERGE_ATR = 0.5
MIN_GAP_ATR = 0.3
MAX_ZONE_ATR = {"scalp": 3.0, "intraday": 3.0, "short_swing": 4.0, "swing": 6.0}
LONG_STOP_ATR = 0.5
SHORT_STOP_ATR = 0.3
CONFIRM_ATR = 0.25
TRIGGER_ATR = 0.1
FILL_T1_ATR, FILL_T2_ATR = 2.0, 4.0
WICK_RATIO = 2.0
VOL_DRY = 0.8
TIME_STOP_BARS = {"scalp": 20, "intraday": 12, "short_swing": 8, "swing": 3}
SHORT_TIME_STOP_BARS = {"scalp": 15, "intraday": 12, "short_swing": 6, "swing": 2}
SIZE = {"crash": "half", "downtrend": "half", "unknown": "half", "range": "normal", "uptrend": "normal"}
# A crash ladder buys less before the bounce shows: 25 / 25 / 50 (the third only after confirmation).
SHARES = {"crash": (0.25, 0.25, 0.5), "default": (0.34, 0.33, 0.33)}
LIVE_MAX_GAP = 0.25      # a model price further than this from the reference is not taken

# regime thresholds (daily bars, daily ATR)
CRASH_DD = -0.10
CRASH_DD_ATR = -4.0
STREAK = 3
OVERSOLD_RSI, OVERBOUGHT_RSI, OVERBOUGHT_PRIMARY = 30.0, 70.0, 75.0
STRETCH_ATR = 2.0
VOL_SURGE = 2.0
GAP_ATR = -1.0
VIX_FEAR = 30.0


def _round(price: float | None) -> float | None:
    if price is None:
        return None
    return float(round(price)) if price >= 1000 else round(price, 2)


def _atr(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    tr = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
          for p, b in pairwise(bars)]
    atr = sum(tr[:n]) / n
    for x in tr[n:]:
        atr = (atr * (n - 1) + x) / n
    return atr


def _sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


# --- regime ---------------------------------------------------------------------------------

def features(daily: list[dict], primary: list[dict] | None = None, vix: float | None = None,
             today: dict | None = None) -> dict | None:
    """The numbers the regime is read from. ``today`` is the still-forming session (from intraday
    bars) when the daily series stops at the prior session."""
    bars = [*daily, today] if today else list(daily)
    if len(bars) < MIN_DAILY:
        return None
    closes = [b["close"] for b in bars]
    close = closes[-1]
    atr_d = _atr(bars) or 0.0
    high20 = max(b["high"] for b in bars[-20:])
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    sma20_prev = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else None
    streak = 0
    for a, b in zip(reversed(closes[:-1]), reversed(closes[1:]), strict=False):
        if b < a:
            streak += 1
        else:
            break
    complete = bars[:-1] if today else bars           # the last finished session's volume
    vols = [b.get("volume") or 0 for b in complete]
    avg20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else None
    vol_x = round(vols[-1] / avg20, 2) if avg20 else None
    last = complete[-1]
    climax = bool(vol_x and vol_x >= VOL_SURGE and atr_d and last["high"] - last["low"] >= 1.5 * atr_d)
    recent = complete[-5:]
    climax_recent = climax or any(
        avg20 and (b.get("volume") or 0) >= VOL_SURGE * avg20 and atr_d and b["high"] - b["low"] >= 1.5 * atr_d
        for b in recent)
    rsi_d = rsi(closes)[-1]
    rsi_p = None
    if primary and len(primary) > 20:
        rsi_p = rsi([b["close"] for b in primary])[-1]
    f = {
        "close": _round(close), "atr_daily": _round(atr_d),
        "dd20": round(close / high20 - 1, 4) if high20 else None,
        "dd20_atr": round((close - high20) / atr_d, 2) if atr_d else None,
        "down_streak": streak,
        "rsi_d": rsi_d, "rsi_primary": rsi_p,
        "ma20_atr": round((close - sma20) / atr_d, 2) if sma20 and atr_d else None,
        "ma50_atr": round((close - sma50) / atr_d, 2) if sma50 and atr_d else None,
        "ma_up": bool(sma20 and sma50 and sma20 > sma50),
        "slope20": round(sma20 - sma20_prev, 4) if sma20 and sma20_prev else None,
        "vol_x": vol_x,
        "gap_atr": round((bars[-1]["open"] - bars[-2]["close"]) / atr_d, 2) if atr_d else None,
        "chg20": round(close / closes[-21] - 1, 4) if len(closes) > 20 else None,
        "vix": vix, "today_partial": bool(today), "climax_recent": bool(climax_recent),
        "sma20": _round(sma20), "sma50": _round(sma50),
    }
    return f


def detect_regime(f: dict | None) -> dict:
    """crash | downtrend | range | uptrend (| unknown with too few bars), with the evidence."""
    if not f:
        return {"id": "unknown", "features": {}, "evidence": [], "flags": {"oversold": False, "overextended": False,
                                                                          "climax_recent": False}}

    def ev(key, threshold, hit):
        return {"key": key, "value": f.get(key), "threshold": threshold, "hit": bool(hit)}

    dd_hit = (f["dd20"] is not None and f["dd20"] <= CRASH_DD) or (f["dd20_atr"] is not None and f["dd20_atr"] <= CRASH_DD_ATR)
    others = [
        ev("down_streak", STREAK, f["down_streak"] >= STREAK),
        ev("rsi_d", OVERSOLD_RSI, f["rsi_d"] is not None and f["rsi_d"] <= OVERSOLD_RSI),
        ev("ma20_atr", -STRETCH_ATR, f["ma20_atr"] is not None and f["ma20_atr"] <= -STRETCH_ATR),
        ev("vol_x", VOL_SURGE, f["vol_x"] is not None and f["vol_x"] >= VOL_SURGE),
        ev("gap_atr", GAP_ATR, f["gap_atr"] is not None and f["gap_atr"] <= GAP_ATR),
    ]
    if f.get("vix") is not None:
        others.append(ev("vix", VIX_FEAR, f["vix"] >= VIX_FEAR))
    evidence = [ev("dd20", CRASH_DD, f["dd20"] is not None and f["dd20"] <= CRASH_DD),
                ev("dd20_atr", CRASH_DD_ATR, f["dd20_atr"] is not None and f["dd20_atr"] <= CRASH_DD_ATR), *others]
    close, sma20, sma50, chg20 = f["close"], f["sma20"], f["sma50"], f["chg20"] or 0
    if dd_hit and sum(e["hit"] for e in others) >= 2:
        rid = "crash"
    elif (sma20 and sma50 and sma20 < sma50 and close < sma20 and chg20 < 0) or \
            ((f["dd20"] or 0) <= -0.05 and (f["rsi_d"] or 50) < 45):
        rid = "downtrend"
    elif sma20 and sma50 and close > sma20 > sma50 and chg20 > 0:
        rid = "uptrend"
    else:
        rid = "range"
    flags = {
        "oversold": bool((f["rsi_d"] is not None and f["rsi_d"] <= OVERSOLD_RSI)
                         or (f["ma20_atr"] is not None and f["ma20_atr"] <= -STRETCH_ATR)),
        "overextended": bool((f["ma20_atr"] is not None and f["ma20_atr"] >= STRETCH_ATR)
                             or (f["rsi_d"] is not None and f["rsi_d"] >= OVERBOUGHT_RSI)
                             or (f["rsi_primary"] is not None and f["rsi_primary"] >= OVERBOUGHT_PRIMARY)),
        "climax_recent": f["climax_recent"],
    }
    keep = ("dd20", "dd20_atr", "down_streak", "rsi_d", "rsi_primary", "ma20_atr", "ma50_atr", "vol_x", "gap_atr",
            "chg20", "vix", "today_partial")
    return {"id": rid, "features": {k: f.get(k) for k in keep}, "evidence": evidence, "flags": flags}


# --- candidate zones ---------------------------------------------------------------------------

def _vwap(primary: list[dict]) -> float | None:
    """The last session's volume-weighted average price (intraday bars only)."""
    if not primary:
        return None
    last_day = primary[-1]["at"][:10]
    session = [b for b in primary if b["at"][:10] == last_day and (b.get("volume") or 0) > 0]
    vol = sum(b["volume"] for b in session)
    if not vol:
        return None
    return sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in session) / vol


def candidates(sr: dict, primary: list[dict], intraday: bool) -> list[dict]:
    """Every level a zone can be built from, with a weight for the confluence score."""
    out = []
    for lv in sr.get("levels") or []:
        out.append({"price": lv["price"], "source": "sr", "weight": float(lv.get("strength") or 1.0),
                    "basis": lv.get("basis") or ("지지" if lv.get("kind") == "support" else "저항"),
                    "kind": lv.get("kind")})
    vp = sr.get("volume_profile") or {}
    va = vp.get("value_area") or {}
    if va.get("low") is not None:
        out.append({"price": va["low"], "source": "vp", "weight": 1.5, "basis": "가치영역 하단"})
    if va.get("high") is not None:
        out.append({"price": va["high"], "source": "vp", "weight": 1.5, "basis": "가치영역 상단"})
    if vp.get("poc") is not None:
        out.append({"price": vp["poc"], "source": "vp", "weight": 1.0, "basis": "최다 거래 가격(POC)"})
    for node in vp.get("nodes") or []:
        if node.get("kind") == "thick":
            out.append({"price": node["price"], "source": "vp", "weight": 1.0, "basis": "매물대"})
    fib = sr.get("fibonacci")
    if fib:
        # A rise's 0.618/0.786 retracement is a pullback entry; a fall's 0.382/0.5 is where a
        # bounce stalls - a long target and a short zone, not a dip-catch entry.
        wanted = {0.618: 1.0, 0.786: 1.0, 0.5: 0.5} if fib.get("direction") == "up" else {0.382: 1.0, 0.5: 0.5}
        for lv in fib.get("levels") or []:
            if lv.get("ratio") in wanted:
                out.append({"price": lv["price"], "source": "fib", "weight": wanted[lv["ratio"]],
                            "basis": f"피보나치 {lv['ratio']}", "fib": fib.get("direction")})
    for t in sr.get("trendlines") or []:
        if t.get("state") in ("intact", "testing") and t.get("price_now") is not None:
            out.append({"price": t["price_now"], "source": "trend", "weight": 1.0,
                        "basis": "상승 추세선" if t.get("kind") == "support" else "하락 추세선"})
    closes = [b["close"] for b in primary]
    sma20 = _sma(closes, 20)
    if sma20 is not None:
        slope = sma20 - (sum(closes[-25:-5]) / 20 if len(closes) >= 25 else sma20)
        out.append({"price": sma20, "source": "ma", "weight": 1.5 if slope <= 0 else 1.0, "basis": "20봉 이평",
                    "slope": slope})
    if intraday:
        vwap = _vwap(primary)
        if vwap is not None:
            out.append({"price": vwap, "source": "vwap", "weight": 1.5, "basis": "VWAP"})
    return out


def zones(cands: list[dict], ref: float, atr: float, below: bool, max_atr: float) -> list[dict]:
    """Levels on one side of ``ref`` merged within MERGE_ATR, ranked by score / distance."""
    side = sorted((c for c in cands if (c["price"] < ref if below else c["price"] > ref)
                   and abs(c["price"] - ref) <= max_atr * atr),
                  key=lambda c: c["price"], reverse=below)
    groups: list[list[dict]] = []
    for c in side:
        if groups and abs(c["price"] - groups[-1][-1]["price"]) <= MERGE_ATR * atr:
            groups[-1].append(c)
        else:
            groups.append([c])
    out = []
    for g in groups:
        prices = [c["price"] for c in g]
        low, high = min(prices), max(prices)
        if high - low < 0.2 * atr:         # a single level still has a little width
            low, high = low - 0.1 * atr, high + 0.1 * atr
        score = round(sum(c["weight"] for c in g), 2)
        distance = abs((high + low) / 2 - ref) / atr
        out.append({"low": _round(low), "high": _round(high), "mid": _round((low + high) / 2), "score": score,
                    "distance_atr": round(distance, 2), "rank": score / (1 + distance / 2),
                    "members": [{"price": _round(c["price"]), "source": c["source"], "basis": c["basis"],
                                 "weight": c["weight"]} for c in g]})
    return sorted(out, key=lambda z: -z["rank"])


def _fallback_zone(ref: float, atr: float, below: bool) -> dict:
    a, b = (ref - 2 * atr, ref - 1.5 * atr) if below else (ref + 1.5 * atr, ref + 2 * atr)
    return {"low": _round(a), "high": _round(b), "mid": _round((a + b) / 2), "score": 0.0,
            "distance_atr": 1.75, "rank": 0.0, "members": [{"price": _round((a + b) / 2), "source": "atr",
                                                            "basis": "ATR 대체", "weight": 0.0}]}


def _next_level(cands: list[dict], above: float, up: bool, atr: float, prefer=("vwap", "ma", "sr", "vp", "trend")) -> dict | None:
    """The nearest candidate beyond ``above`` (by MIN_GAP_ATR) in the trade's direction."""
    ahead = [c for c in cands if (c["price"] - above if up else above - c["price"]) > MIN_GAP_ATR * atr
             and c["source"] in prefer]
    return min(ahead, key=lambda c: abs(c["price"] - above)) if ahead else None


# --- conditions computed from the last bars ----------------------------------------------------

def _swings(values: list[float], k: int, low: bool) -> list[int]:
    out = []
    for i in range(k, len(values) - k):
        window = values[i - k:i + k + 1]
        if (low and values[i] == min(window)) or (not low and values[i] == max(window)):
            out.append(i)
    return out


def _long_conditions(zone: dict, primary: list[dict], ref: float, atr: float, k: int, climax: bool) -> list[dict]:
    last = primary[-1] if primary else None
    body = abs(last["close"] - last["open"]) if last else 0
    wick = (min(last["open"], last["close"]) - last["low"]) if last else 0
    lows = [b["low"] for b in primary]
    closes = [b["close"] for b in primary]
    r = rsi(closes)
    piv = _swings(lows, max(2, k), True)
    divergence = None
    if len(piv) >= 2 and r[piv[-1]] is not None and r[piv[-2]] is not None:
        divergence = lows[piv[-1]] < lows[piv[-2]] and r[piv[-1]] > r[piv[-2]]
    higher_low = lows[piv[-1]] > lows[piv[-2]] if len(piv) >= 2 else None
    reclaim = None
    if len(primary) >= 2:
        prev = primary[-2]
        reclaim = prev["low"] <= zone["high"] and last["close"] > zone["high"]
    vols = [b.get("volume") or 0 for b in primary]
    dry = None
    if len(vols) >= 23 and sum(vols[-23:-3]):
        dry = sum(vols[-3:]) / 3 < VOL_DRY * (sum(vols[-23:-3]) / 20)
    return [
        {"id": "in_zone", "met": zone["low"] <= ref <= zone["high"] + 0.25 * atr, "how": "computed",
         "value": round((ref - zone["high"]) / atr, 2)},
        {"id": "rejection_wick", "met": bool(last and zone["low"] <= last["low"] <= zone["high"]
                                              and wick >= WICK_RATIO * max(body, 1e-9) and last["close"] >= zone["high"]),
         "how": "computed"},
        {"id": "rsi_divergence", "met": divergence, "how": "computed"},
        {"id": "higher_low", "met": higher_low, "how": "computed"},
        {"id": "reclaim", "met": reclaim, "how": "computed"},
        {"id": "volume_dry", "met": dry, "how": "computed"},
        {"id": "climax_recent", "met": climax, "how": "computed"},
        {"id": "tape_hh_hl", "met": None, "how": "watch"},
    ]


def _short_conditions(zone: dict, primary: list[dict], ref: float, atr: float, k: int, vwap: float | None,
                      sma20: float | None, setup: str) -> list[dict]:
    last = primary[-1] if primary else None
    body = abs(last["close"] - last["open"]) if last else 0
    wick = (last["high"] - max(last["open"], last["close"])) if last else 0
    highs = [b["high"] for b in primary]
    piv = _swings(highs, max(2, k), False)
    lower_high = highs[piv[-1]] < highs[piv[-2]] if len(piv) >= 2 else None
    vols = [b.get("volume") or 0 for b in primary]
    vol_break = None
    if setup == "breakdown" and len(vols) >= 21 and sum(vols[-21:-1]):
        vol_break = vols[-1] >= 1.5 * sum(vols[-21:-1]) / 20
    return [
        {"id": "at_resistance", "met": zone["low"] - 0.25 * atr <= ref <= zone["high"], "how": "computed",
         "value": round((zone["low"] - ref) / atr, 2)},
        {"id": "rejection_bar", "met": bool(last and zone["low"] <= last["high"] <= zone["high"] + 0.25 * atr
                                             and last["close"] < zone["low"] and wick >= WICK_RATIO * max(body, 1e-9)),
         "how": "computed"},
        {"id": "lower_high", "met": lower_high, "how": "computed"},
        {"id": "below_vwap", "met": (ref < vwap) if vwap is not None else None, "how": "computed"},
        {"id": "below_ma20", "met": (ref < sma20) if sma20 is not None else None, "how": "computed"},
        {"id": "volume_on_break", "met": vol_break, "how": "computed"},
        {"id": "no_new_entry_late", "met": None, "how": "watch"},
    ]


def _score(zone_score: float, conditions: list[dict]) -> int:
    known = [c for c in conditions if c["how"] == "computed" and c["met"] is not None]
    met = sum(1 for c in known if c["met"])
    return round(min(zone_score, 6) / 6 * 60 + (met / len(known) * 40 if known else 0))


def _target(role: str, price: float, basis: str, source: str, entry: float, stop: float, up: bool, **extra) -> dict:
    risk = abs(entry - stop) or 1e-9
    rr = round((price - entry) / risk if up else (entry - price) / risk, 2)
    return {"role": role, "price": _round(price), "basis": basis, "source": source, "rr": rr, **extra}


# --- the two sides -----------------------------------------------------------------------------

def _long(regime: dict, cands: list[dict], primary: list[dict], ref: float, atr: float, h: horizons.Horizon,
          fib: dict | None) -> dict:
    rid = regime["id"]
    setup = {"crash": "dip_catch", "downtrend": "dip_catch", "unknown": "range_low", "range": "range_low",
             "uptrend": "pullback"}[rid]
    found = zones(cands, ref, atr, below=True, max_atr=MAX_ZONE_ATR[h.id])
    zone = found[0] if found else _fallback_zone(ref, atr, below=True)
    if setup == "dip_catch" and rid == "downtrend" and zone["score"] < 3:
        return {"setup": "none", "reason": "하락 추세에서 받칠 만한 구역(합류 점수 3 이상)이 없음", "zone": zone,
                "size_hint": SIZE[rid], "flat_by_close": h.flat_by_close}
    shares = SHARES["crash"] if rid == "crash" else SHARES["default"]
    entries = [{"step": 1, "price": zone["high"], "share": shares[0], "kind": "limit"},
               {"step": 2, "price": zone["mid"], "share": shares[1], "kind": "limit"},
               {"step": 3, "price": _round(zone["high"] + CONFIRM_ATR * atr), "share": shares[2], "kind": "stop",
                "after": ["reclaim", "higher_low"]}]
    if setup != "dip_catch":     # a range low / pullback is two limit steps
        entries = [{"step": 1, "price": zone["high"], "share": 0.5, "kind": "limit"},
                   {"step": 2, "price": zone["mid"], "share": 0.5, "kind": "limit"}]
    avg = (entries[0]["price"] * entries[0]["share"] + entries[1]["price"] * entries[1]["share"]) / \
        (entries[0]["share"] + entries[1]["share"])
    stop = _round(zone["low"] - LONG_STOP_ATR * atr)
    t1c = _next_level(cands, avg, True, atr)
    t1 = _target("t1", t1c["price"], t1c["basis"], "desk", avg, stop, True, action="take_half", then_stop=_round(avg)) \
        if t1c else _target("t1", avg + FILL_T1_ATR * atr, "ATR 대체", "atr", avg, stop, True, action="take_half",
                            then_stop=_round(avg))
    t2_price, t2_basis, t2_source = None, None, "desk"
    if fib and fib.get("direction") == "down":
        lv = next((x for x in fib.get("levels") or [] if x.get("ratio") == 0.382), None)
        if lv and lv["price"] - t1["price"] > MIN_GAP_ATR * atr:
            t2_price, t2_basis = lv["price"], "하락폭 38.2% 되돌림"
    if t2_price is None:
        nxt = _next_level(cands, t1["price"], True, atr)
        if nxt:
            t2_price, t2_basis = nxt["price"], nxt["basis"]
    if t2_price is None:
        t2_price, t2_basis, t2_source = avg + FILL_T2_ATR * atr, "ATR 대체", "atr"
    t2 = _target("t2", t2_price, t2_basis, t2_source, avg, stop, True, action="take_rest")
    conditions = _long_conditions(zone, primary, ref, atr, h.primary_k, regime["flags"]["climax_recent"])
    return {"setup": setup, "reason": None, "size_hint": SIZE[rid], "flat_by_close": h.flat_by_close,
            "zone": zone, "entries": entries, "avg_entry": _round(avg), "stop": stop, "risk": _round(avg - stop),
            "targets": [t1, t2],
            "time_stop": {"bars": TIME_STOP_BARS[h.id], "interval": h.primary, "required": rid == "crash",
                          "rule": f"마지막 체결 뒤 {TIME_STOP_BARS[h.id]}봉 안에 평단 + 0.5 ATR 을 못 넘으면 정리"},
            "conditions": conditions, "score": _score(zone["score"], conditions),
            "source": {k: "desk" for k in ("zone", "entries", "stop", "t1", "t2", "time_stop")}, "model": None}


def _short(regime: dict, sr: dict, cands: list[dict], primary: list[dict], ref: float, atr: float,
           h: horizons.Horizon, vwap: float | None, sma20: float | None) -> dict:
    rid = regime["id"]
    if rid == "uptrend" and not regime["flags"]["overextended"]:
        return {"setup": "none", "reason": "상승 추세에 과열 신호가 없음 — 숏은 쉬어 간다", "zone": None,
                "size_hint": SIZE[rid], "flat_by_close": True}
    # a strong support just above the price = a breakdown to retest; else fade the bounce
    broken = [lv for lv in sr.get("levels") or [] if lv["price"] > ref and lv["price"] - ref <= atr
              and (lv.get("strength") or 0) >= 2 and lv.get("kind") == "support"]
    setup = "breakdown" if rid in ("crash", "downtrend") and broken else "fade"
    found = zones(cands, ref, atr, below=False, max_atr=MAX_ZONE_ATR[h.id])
    zone = found[0] if found else _fallback_zone(ref, atr, below=False)
    if setup == "breakdown":
        lv = min(broken, key=lambda x: x["price"])
        zone = {**zone, "low": _round(lv["price"] - 0.1 * atr), "high": _round(lv["price"] + 0.2 * atr),
                "mid": _round(lv["price"])}
        trigger = {"price": zone["low"], "kind": "limit", "text": "지지 종가 이탈 + 거래량 동반 뒤 되돌림에 매도"}
    else:
        trigger = {"price": _round(zone["low"] - TRIGGER_ATR * atr), "kind": "stop",
                   "text": "구역에서 거절 봉 · 낮아진 고점 확인 뒤 구역 아래로 내려오면 매도"}
    entry = trigger["price"]
    stop = _round(zone["high"] + SHORT_STOP_ATR * atr)
    t1c = _next_level(cands, entry, False, atr, prefer=("sr", "vp", "trend", "vwap", "ma"))
    t1p, t1b, t1s = (t1c["price"], t1c["basis"], "desk") if t1c else (entry - FILL_T1_ATR * atr, "ATR 대체", "atr")
    t2c = _next_level(cands, t1p, False, atr, prefer=("sr", "vp", "trend"))
    t2p, t2b, t2s = (t2c["price"], t2c["basis"], "desk") if t2c else (entry - FILL_T2_ATR * atr, "ATR 대체", "atr")
    targets = [t for t in (_target("t1", t1p, t1b, t1s, entry, stop, False, action="take_half", then_stop=entry),
                           _target("t2", t2p, t2b, t2s, entry, stop, False, action="take_rest")) if t["price"] > 0]
    conditions = _short_conditions(zone, primary, ref, atr, h.primary_k, vwap, sma20, setup)
    bars = SHORT_TIME_STOP_BARS[h.id]
    return {"setup": setup, "reason": None, "size_hint": SIZE[rid], "flat_by_close": True, "zone": zone,
            "entries": [{"step": 1, "price": entry, "share": 1.0, "kind": trigger["kind"]}], "trigger": trigger,
            "avg_entry": entry, "stop": stop, "risk": _round(stop - entry), "targets": targets,
            "time_stop": {"bars": bars, "interval": h.primary, "required": True,
                          "rule": f"진입 뒤 {bars}봉 안에 1차 목표가 없으면 정리, 장 마감 전 청산"},
            "conditions": conditions, "score": _score(zone["score"], conditions),
            "source": {k: "desk" for k in ("zone", "entries", "stop", "t1", "t2", "time_stop")}, "model": None}


def _desk_bias(regime: dict, macro: dict | None) -> dict:
    """Without the model's line: the macro backdrop's directly relevant surging theme tilts the
    weighting, else the regime's default. Never a price."""
    for row in (macro or {}).get("themes") or []:
        if row.get("relevance") == 2 and (row.get("spike") or 0) >= 1.5 and row.get("tone") is not None \
                and not row.get("stale"):
            if row["tone"] <= -1:
                return {"side": "short", "text": f"{row.get('label') or row['id']} 뉴스가 급증하며 부정적 — 숏 쪽에 무게, 롱은 확인 뒤 작게",
                        "source": "desk"}
            if row["tone"] >= 1:
                return {"side": "long", "text": f"{row.get('label') or row['id']} 뉴스가 급증하며 긍정적 — 롱 쪽에 무게",
                        "source": "desk"}
    default = {"crash": ("both", "폭락 국면 — 숏 단타가 주, 롱 하따는 확인 뒤 절반만"),
               "downtrend": ("short", "하락 추세 — 반등 실패 숏 우선, 롱은 강한 구역에서만"),
               "range": ("both", "횡보 — 하단 롱 · 상단 숏"),
               "uptrend": ("long", "상승 추세 — 눌림 롱 우선, 숏은 과열 때만"),
               "unknown": ("none", "봉이 부족해 국면을 판단하지 못함")}[regime["id"]]
    return {"side": default[0], "text": default[1], "source": "desk"}


def build(sr: dict | None, daily: list[dict], primary: list[dict], h: horizons.Horizon, *,
          live: dict | None = None, vix: float | None = None, macro: dict | None = None,
          today: dict | None = None, now: str | None = None) -> dict | None:
    """Both playbooks from the frame's bars. None without levels or an ATR."""
    if not sr or not primary:
        return None
    atr = sr.get("atr")
    ref = (live or {}).get("price") or sr.get("reference_price") or primary[-1]["close"]
    if not atr or not ref:
        return None
    f = features(daily, primary if h.primary != "1d" else None, vix=vix, today=today)
    regime = detect_regime(f)
    intraday = h.primary != "1d"
    cands = candidates(sr, primary, intraday)
    closes = [b["close"] for b in primary]
    vwap = _vwap(primary) if intraday else None
    sma20 = _sma(closes, 20)
    fib = sr.get("fibonacci")
    return {
        "version": VERSION, "computed_at": now or datetime.now(UTC).isoformat(timespec="seconds"),
        "frame": {"primary": h.primary, "daily": "1d", "atr": _round(atr), "atr_daily": (f or {}).get("atr_daily"),
                  "reference_price": _round(ref), "fallback": (sr.get("fallback") or {}).get("from")},
        "regime": regime,
        "bias_note": _desk_bias(regime, macro),
        "long": _long(regime, cands, primary, ref, atr, h, fib),
        "short": _short(regime, sr, cands, primary, ref, atr, h, vwap, sma20),
    }


def refresh(playbooks: dict | None, live: dict | None) -> dict | None:
    """The price-distance conditions again at a newer quote (the end of the run). No bars."""
    if not playbooks or not live or not live.get("price"):
        return playbooks
    price, atr = live["price"], playbooks["frame"]["atr"] or 1e-9
    out = {**playbooks, "frame": {**playbooks["frame"], "reference_price": _round(price)}}
    for side, cid in (("long", "in_zone"), ("short", "at_resistance")):
        pb = out.get(side) or {}
        zone = pb.get("zone")
        if not zone or not pb.get("conditions"):
            continue
        conditions = []
        for c in pb["conditions"]:
            if c["id"] == cid:
                if side == "long":
                    c = {**c, "met": zone["low"] <= price <= zone["high"] + 0.25 * atr,
                         "value": round((price - zone["high"]) / atr, 2)}
                else:
                    c = {**c, "met": zone["low"] - 0.25 * atr <= price <= zone["high"],
                         "value": round((zone["low"] - price) / atr, 2)}
            conditions.append(c)
        out[side] = {**pb, "conditions": conditions}
    return out


# --- the agents -------------------------------------------------------------------------------

_SETUP_EN = {"dip_catch": "dip-catch", "range_low": "range-low buy", "pullback": "pullback buy",
             "fade": "fade the failed bounce", "breakdown": "breakdown retest", "none": "no setup"}


def _fmt(p: float | None) -> str:
    return "-" if p is None else (f"{p:,.0f}" if p >= 1000 else f"{p:g}")


def prompt_note(pb: dict | None) -> str | None:
    """The desk's candidates for the deciding agents (bounded, numbers only from the desk)."""
    if not pb:
        return None
    reg, f = pb["regime"], pb["regime"]["features"]
    bits = []
    if f.get("dd20") is not None:
        bits.append(f"20-day drawdown {f['dd20'] * 100:.1f}% ({f.get('dd20_atr')} ATR)")
    if f.get("down_streak"):
        bits.append(f"{f['down_streak']} down sessions")
    if f.get("rsi_d") is not None:
        bits.append(f"daily RSI {f['rsi_d']:.0f}")
    if f.get("ma20_atr") is not None:
        bits.append(f"{f['ma20_atr']} ATR from the 20-day average")
    if f.get("vol_x"):
        bits.append(f"volume {f['vol_x']}x its 20-day average")
    if f.get("vix") is not None:
        bits.append(f"VIX {f['vix']:.0f}")
    lines = [f"Desk-computed two-sided playbook candidates (arithmetic from the bars, not a forecast). "
             f"Regime: {reg['id']}" + (f" - {', '.join(bits)}." if bits else ".")]
    for side in ("long", "short"):
        s = pb.get(side) or {}
        if s.get("setup") in (None, "none"):
            lines.append(f"{side.upper()}: no setup ({s.get('reason') or 'n/a'}).")
            continue
        z = s["zone"]
        members = "; ".join(f"{m['basis']} {_fmt(m['price'])}" for m in z["members"][:3])
        entries = " / ".join(f"{_fmt(e['price'])}" + (" on confirmation" if e["kind"] == "stop" and side == "long" else "")
                             for e in s["entries"])
        shares = "/".join(f"{round(e['share'] * 100)}" for e in s["entries"])
        tg = s.get("targets") or []
        met = ", ".join(f"{c['id']} {'yes' if c['met'] else 'no'}" for c in s["conditions"] if c["how"] == "computed"
                        and c["met"] is not None)
        lines.append(
            f"{side.upper()} {_SETUP_EN[s['setup']]}: zone {_fmt(z['low'])}-{_fmt(z['high'])} ({members}), "
            f"entries {entries} ({shares}%), stop {_fmt(s['stop'])}, "
            + ", ".join(f"{t['role'].upper()} {_fmt(t['price'])} ({t['basis']})" for t in tg)
            + f", time stop {s['time_stop']['bars']} x {s['time_stop']['interval']} bars, size {s['size_hint']}"
            + (", flat before the close" if s.get("flat_by_close") else "") + f". Conditions now: {met or 'n/a'}.")
    note = "\n".join(lines)
    return note[:1200]


def mandate() -> str:
    return (
        "Two-sided playbook, every run and every rating: right after the 'Win Probability' line write exactly three "
        "more lines with these English labels in any output language: 'Long Playbook: zone <low>-<high> / entries "
        "<p1>, <p2>, <p3> / confirm <what unlocks the last entry> / stop <price> / T1 <price> / T2 <price> / time "
        "stop <N bars>', 'Short Playbook: zone <low>-<high> / entry <price> / confirm <the rejection or breakdown you "
        "need to see> / stop <price> / T1 <price> / T2 <price> / time stop <N bars>', and 'Playbook Bias: <long|short|"
        "both|none> - <one sentence>'. Start from the desk's candidate levels; move a price only to another listed "
        "level and say why in the confirm text. The Playbook Bias weighs the two sides using the regime and the macro "
        "& policy backdrop; it is a weighting, never a price. Write 'none' for a side with no setup."
    )


_NUM = r"[-+]?\d[\d,]*(?:\.\d+)?"
_LINE = re.compile(r"^\W*(Long|Short)\s+Playbook\W*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_BIAS = re.compile(r"^\W*Playbook\s+Bias\W*:\s*\**\s*(long|short|both|none)\b\s*[-–:]?\s*(.*)$",
                   re.IGNORECASE | re.MULTILINE)


def _nums(text: str) -> list[float]:
    out = []
    for raw in re.findall(_NUM, text or ""):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return out


def _parse_side(body: str) -> dict | None:
    if re.match(r"\s*\**\s*none\b", body, re.IGNORECASE):
        return None
    out: dict = {"raw": body.strip()[:400]}
    for part in body.split("/"):
        key, _, rest = part.strip().partition(" ")
        key = key.lower().strip("*:")
        if key == "zone":
            n = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", rest)]   # "178-180" is a range
            if len(n) >= 2:
                out["zone"] = (min(n[:2]), max(n[:2]))
        elif key in ("entries", "entry"):
            out["entries"] = _nums(rest)[:3]
        elif key == "confirm":
            out["confirm_text"] = rest.strip()[:200]
        elif key in ("stop", "t1", "t2"):
            n = _nums(rest)
            if n:
                out[key] = n[0]
        elif key == "time":
            n = _nums(rest)
            if n:
                out["time_stop"] = int(n[0])
    return out


def parse(text: str) -> dict:
    """{'long', 'short', 'bias'} from the decision text; the last occurrence of each line wins."""
    found: dict = {"long": None, "short": None, "bias": None}
    for side, body in _LINE.findall(text or ""):
        found[side.lower()] = _parse_side(body)
    bias = _BIAS.findall(text or "")
    if bias:
        side, sentence = bias[-1]
        found["bias"] = {"side": side.lower(), "text": sentence.strip()[:240], "source": "model"}
    return found


def _sane(price: float | None, ref: float) -> bool:
    return price is not None and price > 0 and abs(price - ref) <= LIVE_MAX_GAP * ref


def merge(pb: dict | None, parsed: dict, *, convert=None) -> dict | None:
    """The model's prices replace the desk's only when sane: near the reference, on the right
    side, and in order. ``convert`` maps an underlying price to the product's (leveraged runs);
    None when the model's prices are already the instrument's."""
    if not pb:
        return pb
    out = dict(pb)
    ref = pb["frame"]["reference_price"]
    if parsed.get("bias"):
        out["bias_note"] = parsed["bias"]
    for side in ("long", "short"):
        desk, model = pb.get(side), parsed.get(side)
        if not desk or desk.get("setup") == "none" or not model:
            continue
        up = side == "long"
        s = {**desk, "source": dict(desk["source"]), "model": {"raw": model["raw"], "confirm_text": model.get("confirm_text"),
                                                               "ignored": {}}}
        ignored = s["model"]["ignored"]

        def take(name, value, ok, s=s, ignored=ignored):
            if value is None:
                return None
            v = convert(value) if convert else value
            if v is not None and _sane(v, ref) and ok(v):
                s["source"][name] = "converted" if convert else "model"
                return _round(v)
            ignored[name] = value
            return None
        stop = take("stop", model.get("stop"), lambda v, s=s, up=up: (v < s["avg_entry"]) if up else (v > s["avg_entry"]))
        if stop is not None:
            s["stop"] = stop
            s["risk"] = _round(abs(s["avg_entry"] - stop))
        targets = []
        for t in s.get("targets") or []:
            v = take(t["role"], model.get(t["role"]),
                     lambda v, s=s, up=up: (v > s["avg_entry"]) if up else (0 < v < s["avg_entry"]))
            if v is not None:
                t = {**t, "price": v, "source": s["source"][t["role"]], "basis": "모델 제시"}
            risk = abs(s["avg_entry"] - s["stop"]) or 1e-9
            t = {**t, "rr": round(((t["price"] - s["avg_entry"]) if up else (s["avg_entry"] - t["price"])) / risk, 2)}
            targets.append(t)
        if len(targets) == 2 and ((targets[0]["price"] >= targets[1]["price"]) if up else (targets[0]["price"] <= targets[1]["price"])):
            ignored["t2_order"] = targets[1]["price"]
            targets[1] = desk["targets"][1] if len(desk.get("targets") or []) > 1 else targets[1]
        s["targets"] = targets
        if model.get("time_stop") and 1 <= model["time_stop"] <= 200:
            s["time_stop"] = {**s["time_stop"], "bars": model["time_stop"]}
            s["source"]["time_stop"] = "model"
        out[side] = s
    return out


# A scripted run's three lines (desk.runner._fake_decision), around the fake plan's 182.5.
FAKE_LINES = (
    "Long Playbook: zone 178-180 / entries 180, 179, 181 / confirm reclaim of 180 with a higher low / stop 176.5 / "
    "T1 184 / T2 188 / time stop 12 bars\n"
    "Short Playbook: zone 186-187.5 / entry 185.8 / confirm a rejection bar at 187 / stop 188.2 / T1 182.5 / "
    "T2 180 / time stop 10 bars\n"
    "Playbook Bias: both - the regime is mixed; take the short only on a failed bounce and the long only after a reclaim."
)
