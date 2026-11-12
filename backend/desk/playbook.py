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

import math
import re
from datetime import UTC, datetime
from itertools import pairwise
from statistics import median

from . import calibration, direction, events_calendar, exits, horizons, value_swing
from .indicators import rsi
from .related import LEVERAGED

VERSION = 1
MIN_DAILY = 60

# zones and levels, in the frame's ATR
MERGE_ATR = 0.5
ZONE_WIDTH_ATR = 1.0     # a zone never spans more than this: dense levels make several zones, not one range
MIN_GAP_ATR = 0.3
EDGE_BANDS = (0, 40, 60, 80, 101)  # #285: the score band by the edges met (desk.calibration.BANDS + the top)
MAX_ZONE_ATR = {"scalp": 3.0, "intraday": 3.0, "short_swing": 4.0, "swing": 6.0}
# ...and never further than this share of the price (#151): after a -32% day an ATR swollen by that
# one bar times 6 reaches back into the range the price fell out of, +50% away
MAX_ZONE_PCT = {"scalp": 0.03, "intraday": 0.06, "short_swing": 0.12, "swing": 0.25}
CALM_TR = 1.25           # zones, stops and distances use min(ATR, this x the median true range as a share
#                          of the price, at today's price): a shock bar does not stretch them, and after a
#                          fall from 9 to 5 a dollar ATR from the 9s does not either
SHOCK_PCT, SHOCK_ATR, SHOCK_BARS = 0.08, 2.0, 5    # a recent day that moved this much leaves its own levels
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
# The range low's and the pullback's ladder (#166): "split" two limits 50 / 50 (zone top, zone middle),
# "first" the zone top only, "confirm" the zone top then the rest on a reclaim above it (a stop entry,
# as the dip catch's third step). With "split" the second limit filled on the way to the stop: swing
# 1+2 fills made -0.25R (pullback, 2,485) and -0.26R (range low, 1,544) against +0.26R / +0.24R for
# the top alone. Basket backtest, costs in, gated, all / train / held-out:
#   swing 2019-   split -0.122 / -0.125 / -0.105R   first -0.124 / -0.126 / -0.111R   confirm -0.103 / -0.108 / -0.085R
#   2~3 days      split -0.348 / -0.373 / -0.307R   first -0.346 / -0.369 / -0.313R   confirm -0.292 / -0.301 / -0.283R
#   intraday      split -0.991 / -1.074 / -0.821R   first -0.990 / -1.075 / -0.816R   confirm -0.844 / -0.878 / -0.766R
# (scalp: too few minute bars to measure; it follows the others). desk.backtest sweeps it.
PAIR_LADDER = "confirm"
LIVE_MAX_GAP = 0.25      # a model price further than this from the reference is not taken

# #145: more zone candidates from the daily bars. A weight of 0 leaves the source out - the basket
# backtest sweeps them (desk.backtest.SWEEP) and only a source that lifts the expectancy stays on.
# Swing basket 2019-, costs in (train 70% / held-out 30%): anchored VWAP off -0.132R / -0.152R,
# on (1.0) -0.129R / -0.123R, T1-first 0.511 -> 0.540 held out - kept. Gap fills 0 -0.132 / -0.122,
# 1.0 -0.129 / -0.123 - no held-out gain, so off (the code stays for the next sweep).
AVWAP_WEIGHT = 1.0       # the VWAP anchored at the recent top, the last swing low and the last big gap
AVWAP_LOOKBACK = 60      # daily bars the anchors are looked for in
GAP_FILL_WEIGHT = 0.0    # the edge of a gap the price has not gone back to fill
GAP_LOOKBACK = 40
GAP_MIN_ATR = 0.5        # a gap smaller than this (in daily ATR) is noise
GAP_MAX = 2              # per direction, the most recent first

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

    # -10% is a crash for a stock and an ordinary pullback for a 3x product: the % bar widens with
    # the instrument's daily ATR (2.5 ATR at least)
    vol_dd = -2.5 * f["atr_daily"] / f["close"] if f.get("atr_daily") and f.get("close") else 0.0
    dd_hit = (f["dd20"] is not None and f["dd20"] <= min(CRASH_DD, vol_dd)) or \
        (f["dd20_atr"] is not None and f["dd20_atr"] <= CRASH_DD_ATR)
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


def _calm_atr(bars: list[dict], atr: float, ref: float | None = None, n: int = 20) -> float:
    """``atr`` unless the recent true ranges say it is swollen by a shock bar or two, or (with
    ``ref``) was earned at a much higher price: the median true range as a share of the price
    before it, times ``ref``."""
    pairs = [(p, b) for p, b in pairwise(bars[-(n + 1):]) if p["close"]]
    if len(pairs) < 5:
        return atr
    tr = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"])) for p, b in pairs]
    if ref:
        typical = median(t / p["close"] for t, (p, _b) in zip(tr, pairs, strict=True)) * ref
    else:
        typical = median(tr)
    return min(atr, CALM_TR * typical) if typical > 0 else atr


def shock(daily: list[dict], atr: float) -> dict | None:
    """The most recent of the last SHOCK_BARS sessions that moved SHOCK_PCT and SHOCK_ATR ATR
    (a crash or a squeeze): {"bar", "prev", "down", "change"}."""
    for i in range(len(daily) - 1, max(0, len(daily) - 1 - SHOCK_BARS), -1):
        bar, prev = daily[i], daily[i - 1]
        if not prev["close"]:
            continue
        before = _atr(daily[:i])                       # the ATR before the bar, not swollen by it
        before = _calm_atr(daily[:i], before) if before else atr
        change = bar["close"] / prev["close"] - 1
        gap = abs(bar["open"] - prev["close"])
        if abs(change) >= SHOCK_PCT and (abs(change) * prev["close"] >= SHOCK_ATR * before or gap >= before):
            return {"bar": bar, "prev": prev, "down": change < 0, "change": round(change, 4)}
    return None


def shock_candidates(s: dict | None) -> list[dict]:
    """A crash day's own levels: its low (where the dip catch starts), mid, open and high (where a
    bounce stalls), and the gap it left (filled first on the way back). Source "sr", the basis says which."""
    if not s:
        return []
    bar, prev, down = s["bar"], s["prev"], s["down"]
    word = "급락일" if down else "급등일"
    out = [{"price": bar["low"], "source": "sr", "weight": 2.0 if down else 1.0, "basis": f"{word} 저가", "tag": "shock"},
           {"price": bar["high"], "source": "sr", "weight": 1.0 if down else 2.0, "basis": f"{word} 고가", "tag": "shock"},
           {"price": (bar["high"] + bar["low"]) / 2, "source": "sr", "weight": 1.0, "basis": f"{word} 중간값", "tag": "shock"},
           {"price": bar["open"], "source": "sr", "weight": 1.0, "basis": f"{word} 시가", "tag": "shock"},
           {"price": prev["close"], "source": "sr", "weight": 1.0, "basis": f"{word} 전 종가", "tag": "shock"}]
    if down and prev["low"] > bar["high"]:
        out.append({"price": prev["low"], "source": "sr", "weight": 1.5, "basis": "갭 상단(전일 저가)", "tag": "shock"})
    if not down and prev["high"] < bar["low"]:
        out.append({"price": prev["high"], "source": "sr", "weight": 1.5, "basis": "갭 하단(전일 고가)", "tag": "shock"})
    return out


def round_levels(ref: float, cap: float) -> list[dict]:
    """Two round numbers each side of the price within ``cap`` (5.13 -> 4.5 5.0 / 5.5 6.0)."""
    mag = 10 ** math.floor(math.log10(ref))
    step = mag * (0.5 if ref / mag >= 3 else 0.1)
    base = math.floor(ref / step) * step
    prices = [base - step, base, base + step, base + 2 * step]
    return [{"price": round(p, 6), "source": "sr", "weight": 0.5, "basis": "라운드 넘버", "tag": "round"}
            for p in prices if p > 0 and p != ref and abs(p - ref) <= cap]


def _avwap_from(daily: list[dict], i: int) -> float | None:
    bars = [b for b in daily[i:] if (b.get("volume") or 0) > 0]
    vol = sum(b["volume"] for b in bars)
    if not vol:
        return None
    return sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars) / vol


def anchored_vwaps(daily: list[dict], atr: float) -> list[dict]:
    """#145: the VWAP since the recent top (what the buyers since the fall paid on average), since
    the last swing low (the bounce's buyers) and since the last big gap - institutions defend these.
    One candidate per anchor bar; none without volume (an index)."""
    if AVWAP_WEIGHT <= 0 or len(daily) < 10:
        return []
    start = max(1, len(daily) - AVWAP_LOOKBACK)
    window = range(start, len(daily) - 1)             # an anchor needs a bar after it
    if not window:
        return []
    anchors: dict[int, list[str]] = {}
    top = max(window, key=lambda i: daily[i]["high"])
    anchors.setdefault(top, []).append("고점")
    lows = [i for i in _swings([b["low"] for b in daily], 3, low=True) if i >= start]
    if lows:
        anchors.setdefault(lows[-1], []).append("스윙 저점")
    gaps = [i for i in window if abs(daily[i]["open"] - daily[i - 1]["close"]) >= 2 * GAP_MIN_ATR * atr]
    if gaps:
        anchors.setdefault(gaps[-1], []).append("갭")
    out = []
    for i, names in sorted(anchors.items()):
        price = _avwap_from(daily, i)
        if price is not None:
            out.append({"price": price, "source": "avwap", "weight": AVWAP_WEIGHT,
                        "basis": f"앵커드 VWAP({' · '.join(names)} {daily[i]['at'][:10]}부터)"})
    return out


def gap_fills(daily: list[dict], atr: float, skip_at: str | None = None) -> list[dict]:
    """#145: gaps the price has not gone back to fill - the far edge (the bar before the gap) is
    where a move back stalls or turns. ``skip_at`` is a shock day, whose gap shock_candidates has."""
    if GAP_FILL_WEIGHT <= 0 or len(daily) < 3:
        return []
    out: list[dict] = []
    ups = downs = 0
    for i in range(len(daily) - 1, max(0, len(daily) - GAP_LOOKBACK), -1):
        bar, prev = daily[i], daily[i - 1]
        if bar["at"] == skip_at:
            continue
        after = daily[i:]
        if ups < GAP_MAX and bar["low"] - prev["high"] >= GAP_MIN_ATR * atr \
                and min(b["low"] for b in after) > prev["high"]:
            ups += 1
            out.append({"price": prev["high"], "source": "gap", "weight": GAP_FILL_WEIGHT,
                        "basis": f"상승 갭 메움({bar['at'][:10]} 전 고가)"})
        elif downs < GAP_MAX and prev["low"] - bar["high"] >= GAP_MIN_ATR * atr \
                and max(b["high"] for b in after) < prev["low"]:
            downs += 1
            out.append({"price": prev["low"], "source": "gap", "weight": GAP_FILL_WEIGHT,
                        "basis": f"하락 갭 메움({bar['at'][:10]} 전 저가)"})
    return out


def zones(cands: list[dict], ref: float, atr: float, below: bool, max_atr: float) -> list[dict]:
    """Levels on one side of ``ref`` merged within MERGE_ATR, ranked by score / distance."""
    side = sorted((c for c in cands if (c["price"] < ref if below else c["price"] > ref)
                   and abs(c["price"] - ref) <= max_atr * atr),
                  key=lambda c: c["price"], reverse=below)
    groups: list[list[dict]] = []
    for c in side:
        # next to the last member AND within the width cap from the first: a chain of close
        # levels (dense on a volatile 3x product) must not grow one zone across the whole fall
        if groups and abs(c["price"] - groups[-1][-1]["price"]) <= MERGE_ATR * atr                 and abs(c["price"] - groups[-1][0]["price"]) <= ZONE_WIDTH_ATR * atr:
            groups[-1].append(c)
        else:
            groups.append([c])
    out = []
    for g in groups:
        if all(c.get("tag") == "round" for c in g):
            continue                       # a round number adds to a zone, it is never one by itself
        # round numbers add to the score but do not move the edges
        prices = [c["price"] for c in g if c.get("tag") != "round"]
        low, high = min(prices), max(prices)
        if high - low < 0.2 * atr:         # a single level still has a little width
            low, high = low - 0.1 * atr, high + 0.1 * atr
            # ...but never across the price: a long's limit above it would fill at once
            low, high = (low, min(high, ref)) if below else (max(low, ref), high)
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


def _next_level(cands: list[dict], above: float, up: bool, atr: float, prefer=("vwap", "ma", "sr", "vp", "trend"),
                within: float | None = None, ref: float | None = None) -> dict | None:
    """The nearest candidate beyond ``above`` (by MIN_GAP_ATR) in the trade's direction, and with
    ``within`` no further than that from ``ref`` (the price now)."""
    ahead = [c for c in cands if (c["price"] - above if up else above - c["price"]) > MIN_GAP_ATR * atr
             and c["source"] in prefer and (within is None or abs(c["price"] - (ref or above)) <= within)]
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
    """Zone confluence 60 + met conditions 40. With the #285 edge conditions (desk.setup_filter) - the
    only part the backtest found to separate good plans from bad - the number met picks the band
    (none 0-39 · one 40-59 · two 60-79 · all 80-100, desk.calibration.BANDS) and the rest places the
    score inside it."""
    known = [c for c in conditions if c["how"] == "computed" and c["met"] is not None]
    met = sum(1 for c in known if c["met"])
    base = min(zone_score, 6) / 6 * 60 + (met / len(known) * 40 if known else 0)
    edges = [c for c in conditions if c["how"] == "edge"]
    if not edges:
        return round(base)
    k = round(sum(1 for c in edges if c["met"]) / len(edges) * (len(EDGE_BANDS) - 2))
    lo, hi = EDGE_BANDS[k], EDGE_BANDS[k + 1]
    return int(lo + base / 100 * (hi - lo - 1))


def _target(role: str, price: float, basis: str, source: str, entry: float, stop: float, up: bool, **extra) -> dict:
    risk = abs(entry - stop) or 1e-9
    rr = round((price - entry) / risk if up else (entry - price) / risk, 2)
    return {"role": role, "price": _round(price), "basis": basis, "source": source, "rr": rr, **extra}


# --- the two sides -----------------------------------------------------------------------------

def _none_side(reason: str, size_hint: str, flat_by_close: bool) -> dict:
    """A side with no setup still carries every key, empty (the page's type is one shape)."""
    return {"setup": "none", "reason": reason, "size_hint": size_hint, "flat_by_close": flat_by_close,
            "zone": None, "entries": [], "avg_entry": None, "stop": None, "risk": None, "targets": [],
            "time_stop": None, "conditions": [], "score": 0, "source": {}, "model": None}


def _pair_entries(zone: dict, zatr: float) -> list[dict]:
    """A range low's / pullback's entries per PAIR_LADDER (see there)."""
    top = {"step": 1, "price": zone["high"], "share": 0.5, "kind": "limit"}
    if PAIR_LADDER == "first":
        return [{**top, "share": 1.0}]
    if PAIR_LADDER == "confirm":
        return [top, {"step": 2, "price": _round(zone["high"] + CONFIRM_ATR * zatr), "share": 0.5, "kind": "stop",
                      "after": ["reclaim"]}]
    return [top, {"step": 2, "price": zone["mid"], "share": 0.5, "kind": "limit"}]


def _long(regime: dict, cands: list[dict], primary: list[dict], ref: float, atr: float, h: horizons.Horizon,
          fib: dict | None, zatr: float | None = None, cap: float | None = None, low: dict | None = None) -> dict:
    rid = regime["id"]
    zatr = zatr or atr
    cap = cap if cap is not None else MAX_ZONE_ATR[h.id] * zatr
    setup = {"crash": "dip_catch", "downtrend": "dip_catch", "unknown": "range_low", "range": "range_low",
             "uptrend": "pullback"}[rid]
    found = zones(cands, ref, zatr, below=True, max_atr=cap / zatr)
    weak = not found or (rid == "downtrend" and found[0]["score"] < 3)
    if setup == "dip_catch" and weak and low and (rid == "crash" or low.get("shock") or regime["flags"]["oversold"]):
        # nothing to catch below (a new low): the dip catch starts at the low just made (#151)
        return _reclaim_long(regime, cands, primary, ref, atr, zatr, h, low)
    zone = found[0] if found else _fallback_zone(ref, zatr, below=True)
    if setup == "dip_catch" and rid == "downtrend" and zone["score"] < 3:
        return _none_side("하락 추세에서 받칠 만한 구역(합류 점수 3 이상)이 없음", SIZE[rid], h.flat_by_close)
    shares = SHARES["crash"] if rid == "crash" else SHARES["default"]
    entries = [{"step": 1, "price": zone["high"], "share": shares[0], "kind": "limit"},
               {"step": 2, "price": zone["mid"], "share": shares[1], "kind": "limit"},
               {"step": 3, "price": _round(zone["high"] + CONFIRM_ATR * zatr), "share": shares[2], "kind": "stop",
                "after": ["reclaim", "higher_low"]}]
    if setup != "dip_catch":     # a range low / pullback: PAIR_LADDER's two steps (or one)
        entries = _pair_entries(zone, zatr)
        avg = sum(e["price"] * e["share"] for e in entries) / sum(e["share"] for e in entries)
    else:                        # the dip catch's average is its two limits (the third waits for the turn)
        avg = (entries[0]["price"] * entries[0]["share"] + entries[1]["price"] * entries[1]["share"]) / \
            (entries[0]["share"] + entries[1]["share"])
    stop = _round(zone["low"] - LONG_STOP_ATR * zatr)
    ups = [c for c in cands if c.get("tag") != "round"]
    t1c = _next_level(ups, max(avg, zone["high"]), True, zatr, within=2 * cap, ref=ref)
    t1 = _target("t1", t1c["price"], t1c["basis"], "desk", avg, stop, True, action="take_half", then_stop=_round(avg)) \
        if t1c else _target("t1", avg + FILL_T1_ATR * zatr, "ATR 대체", "atr", avg, stop, True, action="take_half",
                            then_stop=_round(avg))
    t2_price, t2_basis, t2_source = None, None, "desk"
    if fib and fib.get("direction") == "down":
        lv = next((x for x in fib.get("levels") or [] if x.get("ratio") == 0.382), None)
        if lv and MIN_GAP_ATR * atr < lv["price"] - t1["price"] and lv["price"] - ref <= 2 * cap:
            t2_price, t2_basis = lv["price"], "하락폭 38.2% 되돌림"
    if t2_price is None:
        nxt = _next_level(ups, t1["price"], True, zatr, within=2 * cap, ref=ref)
        if nxt:
            t2_price, t2_basis = nxt["price"], nxt["basis"]
    if t2_price is None:
        t2_price, t2_basis, t2_source = avg + FILL_T2_ATR * zatr, "ATR 대체", "atr"
    t2 = _target("t2", t2_price, t2_basis, t2_source, avg, stop, True, action="take_rest")
    conditions = _long_conditions(zone, primary, ref, atr, h.primary_k, regime["flags"]["climax_recent"])
    return {"setup": setup, "reason": None, "size_hint": SIZE[rid], "flat_by_close": h.flat_by_close,
            "zone": zone, "entries": entries, "avg_entry": _round(avg), "stop": stop, "risk": _round(avg - stop),
            "targets": [t1, t2],
            "time_stop": {"bars": TIME_STOP_BARS[h.id], "interval": h.primary, "required": rid == "crash",
                          "rule": f"마지막 체결 뒤 {TIME_STOP_BARS[h.id]}봉 안에 평단 + 0.5 ATR 을 못 넘으면 정리"},
            "conditions": conditions, "score": _score(zone["score"], conditions),
            "source": {k: "desk" for k in ("zone", "entries", "stop", "t1", "t2", "time_stop")}, "model": None}


def _reclaim_long(regime: dict, cands: list[dict], primary: list[dict], ref: float, atr: float, zatr: float,
                  h: horizons.Horizon, low: dict) -> dict:
    """The dip catch at a new low (#151): a zone around the low just made; limit steps at its top and
    at the low, the last half only on a reclaim / higher low. Below the low already: buy the reclaim."""
    level = low["price"]
    zone = {"low": _round(level - 0.25 * zatr), "high": _round(level + 0.25 * zatr), "mid": _round(level)}
    members = [c for c in cands if abs(c["price"] - level) <= 0.5 * zatr]
    if not any(c.get("basis") == low["basis"] for c in members):
        members.insert(0, {"price": level, "source": "sr", "weight": 2.0, "basis": low["basis"]})
    score = round(sum(c["weight"] for c in members), 2)
    distance = abs(level - ref) / zatr
    zone.update(score=score, distance_atr=round(distance, 2), rank=score / (1 + distance / 2),
                members=[{"price": _round(c["price"]), "source": c["source"], "basis": c["basis"],
                          "weight": c["weight"]} for c in members])
    shares = SHARES["crash"]
    if ref >= level:
        # a buy limit never sits above the price: it would fill at once
        entries = [{"step": 1, "price": _round(min(zone["high"], ref)), "share": shares[0], "kind": "limit"},
                   {"step": 2, "price": _round(min(zone["mid"], ref)), "share": shares[1], "kind": "limit"},
                   {"step": 3, "price": _round(max(zone["high"], ref) + CONFIRM_ATR * zatr), "share": shares[2],
                    "kind": "stop", "after": ["reclaim", "higher_low"]}]
        stop = _round(zone["low"] - 0.25 * zatr)
    else:   # under the low already: nothing is bought until it is taken back - both steps are stops above
        entries = [{"step": 1, "price": zone["high"], "share": 0.5, "kind": "stop", "after": ["reclaim"]},
                   {"step": 2, "price": _round(zone["high"] + CONFIRM_ATR * zatr), "share": 0.5, "kind": "stop",
                    "after": ["reclaim", "higher_low"]}]
        stop = _round(min(zone["low"], ref, *(b["low"] for b in primary[-3:])) - 0.25 * zatr)
    avg = (entries[0]["price"] * entries[0]["share"] + entries[1]["price"] * entries[1]["share"]) / \
        (entries[0]["share"] + entries[1]["share"])
    ups = [c for c in cands if c.get("tag") != "round"]
    within = 2 * min(MAX_ZONE_ATR[h.id] * zatr, MAX_ZONE_PCT[h.id] * ref)
    t1c = _next_level(ups, max(avg, zone["high"]), True, zatr, within=within, ref=ref)
    t1 = _target("t1", t1c["price"], t1c["basis"], "desk", avg, stop, True, action="take_half", then_stop=_round(avg)) \
        if t1c else _target("t1", avg + FILL_T1_ATR * zatr, "ATR 대체", "atr", avg, stop, True, action="take_half",
                            then_stop=_round(avg))
    t2c = _next_level(ups, t1["price"], True, zatr, within=within, ref=ref)
    t2 = _target("t2", t2c["price"], t2c["basis"], "desk", avg, stop, True, action="take_rest") if t2c else \
        _target("t2", avg + FILL_T2_ATR * zatr, "ATR 대체", "atr", avg, stop, True, action="take_rest")
    conditions = _long_conditions(zone, primary, ref, atr, h.primary_k, regime["flags"]["climax_recent"])
    return {"setup": "dip_catch", "variant": "reclaim", "reason": None, "size_hint": "half",
            "flat_by_close": h.flat_by_close, "zone": zone, "entries": entries, "avg_entry": _round(avg), "stop": stop,
            "risk": _round(avg - stop), "targets": [t1, t2],
            "time_stop": {"bars": TIME_STOP_BARS[h.id], "interval": h.primary, "required": True,
                          "rule": f"마지막 체결 뒤 {TIME_STOP_BARS[h.id]}봉 안에 평단 + 0.5 ATR 을 못 넘으면 정리"},
            "conditions": conditions, "score": _score(zone["score"], conditions),
            "source": {k: "desk" for k in ("zone", "entries", "stop", "t1", "t2", "time_stop")}, "model": None}


def reach(side: dict | None, ref: float, cap: float, atr: float, up: bool) -> dict | None:
    """How far the side's first entry is from the price and whether it can come within the horizon
    (inside the zone cap); ``reason`` says why not. A target on the near side of the price is no
    reason: a ladder below waits for the pullback, and its targets count from the fill."""
    if not side or side.get("setup") in (None, "none") or not side.get("entries") or not ref:
        return None
    entry = side["entries"][0]["price"]
    gap = entry - ref
    far = abs(gap) > cap
    return {"entry": entry, "gap_pct": round(gap / ref * 100, 2), "gap_atr": round(abs(gap) / atr, 2) if atr else None,
            "reachable": not far, "cap_pct": round(cap / ref * 100, 2), "reason": "too_far" if far else None}


def _reach_again(pb: dict, ref: float) -> None:
    """``reach`` at a newer price (refresh / track), on the cap the playbooks were built with."""
    frame = pb.get("frame") or {}
    cap_pct, zatr = frame.get("zone_cap_pct"), frame.get("atr_zone") or frame.get("atr")
    if not cap_pct or not ref:
        return
    for name, up in (("long", True), ("short", False)):
        side = pb.get(name)
        if side and side.get("reach") is not None:
            pb[name] = {**side, "reach": reach(side, ref, cap_pct / 100 * ref, zatr, up)}


def _short(regime: dict, sr: dict, cands: list[dict], primary: list[dict], ref: float, atr: float,
           h: horizons.Horizon, vwap: float | None, sma20: float | None, zatr: float | None = None,
           cap: float | None = None) -> dict:
    rid = regime["id"]
    zatr = zatr or atr
    cap = cap if cap is not None else MAX_ZONE_ATR[h.id] * zatr
    if rid == "uptrend" and not regime["flags"]["overextended"]:
        return _none_side("상승 추세에 과열 신호가 없음 — 숏은 쉬어 간다", SIZE[rid], True)
    # a strong support just above the price = a breakdown to retest; else fade the bounce
    broken = [lv for lv in sr.get("levels") or [] if lv["price"] > ref and lv["price"] - ref <= zatr
              and (lv.get("strength") or 0) >= 2 and lv.get("kind") == "support"]
    setup = "breakdown" if rid in ("crash", "downtrend") and broken else "fade"
    found = zones(cands, ref, zatr, below=False, max_atr=cap / zatr)
    zone = found[0] if found else _fallback_zone(ref, zatr, below=False)
    if setup == "breakdown":
        lv = min(broken, key=lambda x: x["price"])
        zone = {**zone, "low": _round(max(lv["price"] - 0.1 * zatr, ref)), "high": _round(lv["price"] + 0.2 * zatr),
                "mid": _round(lv["price"])}
        trigger = {"price": zone["low"], "kind": "limit", "text": "지지 종가 이탈 + 거래량 동반 뒤 되돌림에 매도"}
    else:
        trigger = {"price": _round(zone["low"] - TRIGGER_ATR * zatr), "kind": "stop",
                   "text": "구역에서 거절 봉 · 낮아진 고점 확인 뒤 구역 아래로 내려오면 매도"}
    entry = trigger["price"]
    stop = _round(zone["high"] + SHORT_STOP_ATR * zatr)
    downs = [c for c in cands if c.get("tag") != "round"]
    t1c = _next_level(downs, entry, False, zatr, prefer=("sr", "vp", "trend", "vwap", "ma"), within=2 * cap, ref=ref)
    t1p, t1b, t1s = (t1c["price"], t1c["basis"], "desk") if t1c else \
        (max(entry - FILL_T1_ATR * zatr, entry * (1 - MAX_ZONE_PCT[h.id])), "ATR 대체", "atr")
    t2c = _next_level(downs, t1p, False, zatr, prefer=("sr", "vp", "trend"), within=2 * cap, ref=ref)
    t2p, t2b, t2s = (t2c["price"], t2c["basis"], "desk") if t2c else \
        (max(t1p - (FILL_T2_ATR - FILL_T1_ATR) * zatr, entry * (1 - 1.5 * MAX_ZONE_PCT[h.id])), "ATR 대체", "atr")
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
          today: dict | None = None, now: str | None = None, calibrate: bool = True,
          market: dict | None = None) -> dict | None:
    """Both playbooks from the frame's bars. None without levels or an ATR. ``calibrate=False``
    (the backtest) leaves out the desk's record - a backtest must not be graded by its own results."""
    if not sr or not primary:
        return None
    atr = sr.get("atr")
    ref = (live or {}).get("price") or sr.get("reference_price") or primary[-1]["close"]
    if not atr or not ref:
        return None
    f = features(daily, primary if h.primary != "1d" else None, vix=vix, today=today)
    regime = detect_regime(f)
    intraday = h.primary != "1d"
    zatr = _calm_atr(primary, atr, ref)
    cap = min(MAX_ZONE_ATR[h.id] * zatr, MAX_ZONE_PCT[h.id] * ref)
    jolt = shock(daily, _calm_atr(daily, _atr(daily) or atr)) if len(daily) > 21 else None
    if jolt and abs(jolt["bar"]["close"] - ref) > 2 * cap:
        jolt = None                                   # a shock the price has long left behind
    datr = _atr(daily) or atr                         # the gaps and anchors are daily bars
    cands = [*candidates(sr, primary, intraday), *shock_candidates(jolt), *round_levels(ref, cap),
             *anchored_vwaps(daily, datr), *gap_fills(daily, datr, jolt["bar"]["at"] if jolt else None)]
    low = None
    recent_low = min(b["low"] for b in primary[-3:]) if primary else None
    if jolt and jolt["down"]:
        # the crash low, unless the days after it went lower: the new low is where the catch starts
        if recent_low is not None and recent_low < jolt["bar"]["low"]:
            low = {"price": recent_low, "basis": "급락 뒤 신저가", "shock": True}
        else:
            low = {"price": jolt["bar"]["low"], "basis": "급락일 저가", "shock": True}
    elif recent_low is not None:
        low = {"price": recent_low, "basis": "최근 저점", "shock": False}
    if low and abs(low["price"] - ref) > cap:
        low = None
    closes = [b["close"] for b in primary]
    vwap = _vwap(primary) if intraday else None
    sma20 = _sma(closes, 20)
    fib = sr.get("fibonacci")
    long_side = _long(regime, cands, primary, ref, atr, h, fib, zatr=zatr, cap=cap, low=low)
    short_side = _short(regime, sr, cands, primary, ref, atr, h, vwap, sma20, zatr=zatr, cap=cap)
    for side, up in ((long_side, True), (short_side, False)):
        if side.get("setup") not in (None, "none"):
            side["reach"] = reach(side, ref, cap, zatr, up)
    built = {
        "version": VERSION, "computed_at": now or datetime.now(UTC).isoformat(timespec="seconds"),
        "frame": {"primary": h.primary, "daily": "1d", "atr": _round(atr), "atr_daily": (f or {}).get("atr_daily"),
                  "atr_zone": _round(zatr), "zone_cap_pct": round(cap / ref * 100, 2),
                  "reference_price": _round(ref), "fallback": (sr.get("fallback") or {}).get("from")},
        "regime": regime,
        "shock": ({"at": jolt["bar"]["at"], "change": jolt["change"], "down": jolt["down"],
                   "open": _round(jolt["bar"]["open"]), "high": _round(jolt["bar"]["high"]),
                   "low": _round(jolt["bar"]["low"]), "prev_close": _round(jolt["prev"]["close"])} if jolt else None),
        "bias_note": _desk_bias(regime, macro),
        "long": long_side,
        "short": short_side,
    }
    # #166: the trend's side at full size, the other halved or dropped
    # #166 stage 2: the market's selling score (desk.risk_regime) and the ticker's news theme vote too
    out = direction.apply(built, direction.score(daily, market, macro), h,
                          empty=lambda reason: _none_side(reason, "half", True))
    # #148: only a same-day (live) playbook looks at the calendar — a past date or a backtest has no "now"
    if out and live:
        hit = events_calendar.CACHE.blackout()
        hold_for_event(out, {**hit.to_dict(), "until": (hit.at + events_calendar.BLACKOUT_AFTER).isoformat()}
                       if hit else None)
    if out and calibrate:
        calibrate_sides(out, h)
    return out


def calibrate_sides(pb: dict, h: horizons.Horizon, table: calibration.Table | None = None) -> dict:
    """#147: each side's score as the desk's record - T1 rate, expected R, sample (``p_t1``,
    ``exp_r``, ``n``, the rest under ``calibration``). A thin sample gives no numbers. A setup
    negative on enough trades is only marked unless calibration.DISABLE turns it off."""
    table = table or calibration.TABLE
    regime = (pb.get("regime") or {}).get("id")
    for side in ("long", "short"):
        s = pb.get(side) or {}
        if s.get("setup") in (None, "none"):
            continue
        cal = table.look(h.id, side, s["setup"], regime, s.get("score"))
        if cal["negative"] and calibration.DISABLE:
            reason = f"데스크 실적 기대 {cal['exp_r']:+.2f}R ({cal['n']}건) — 음수라 쉼"
            pb[side] = {**_none_side(reason, s.get("size_hint") or "half", bool(s.get("flat_by_close"))),
                        "direction": s.get("direction"), "calibration": cal}
            continue
        s.update(p_t1=cal["p_t1"], exp_r=cal["exp_r"], n=cal["n"], calibration=cal)
    return pb


def hold_for_event(pb: dict, event: dict | None) -> dict:
    """#148: a market-wide release (FOMC · CPI · jobs) is hours away or just out — no fresh entry
    until it passes: every side gets an unmet ``event_clear`` condition (its score drops) and only the
    first ladder step stays live, the later ones are marked ``held``. ``pb["event_hold"]`` names it.
    With no event the marks of an earlier hold come off (a tracked analysis outlives its window)."""
    pb["event_hold"] = event
    for side in ("long", "short"):
        s = pb.get(side) or {}
        if s.get("setup") in (None, "none") or not s.get("entries"):
            continue
        entries = [{k: v for k, v in e.items() if k != "held"} for e in s["entries"]]
        conditions = [c for c in s.get("conditions") or [] if c["id"] != "event_clear"]
        if event:
            entries = [e if e["step"] == 1 else {**e, "held": "event"} for e in entries]
            conditions.append({"id": "event_clear", "met": False, "how": "computed", "value": event["label"]})
        s["entries"], s["conditions"] = entries, conditions
        s["score"] = _score((s.get("zone") or {}).get("score") or 0, conditions)
    return pb


# #239 채택: the backtest's exit comparison (desk.exits, `python -m desk.cli backtest`) - on swing plans
# of stocks the 2-ATR trail after T1 beat the fixed T2 exit on both the training and the held-out dates
# with a smaller drawdown; on leveraged products it did not (worse training average and drawdown), and
# on short_swing it lost (held out -0.328R vs -0.300R); scalp / intraday were not measured. Only the
# measured win changes; the rest keeps T1 half -> T2.
TRAIL_HORIZONS = ("swing",)


def exit_rule(side: dict, up: bool, ticker: str, h: horizons.Horizon, atr: float | None) -> dict | None:
    """How the side's position is closed. None for no setup and for the oversold swing (its own trail)."""
    if side.get("setup") in (None, "none", "value_swing") or not side.get("targets"):
        return None
    if h.id in TRAIL_HORIZONS and ticker.upper() not in LEVERAGED and not side.get("flat_by_close") and atr:
        k = exits.TRAIL_ATR
        way = "최고가에서" if up else "최저가에서"
        follow = "아래로 따라 올린다" if up else "위로 따라 내린다"
        return {"rule": "trail_atr", "trail_atr": k, "distance": _round(k * atr),
                "text": f"T1 에서 절반 익절 · 손절을 평단으로 → 남은 절반은 {way} {k:g} ATR({_fmt(k * atr)}) "
                        f"{follow}. T1 뒤엔 시간 손절 대신 추적, T2 는 참고 목표"}
    return {"rule": "fixed", "text": "T1 에서 절반 익절 · 손절을 평단으로 → 나머지는 T2 에서"}


def set_exits(pb: dict | None, ticker: str, h: horizons.Horizon) -> dict | None:
    """``exit`` on each side of a finished playbook (#239). In place; returns ``pb``."""
    if not pb:
        return pb
    atr = (pb.get("frame") or {}).get("atr")
    for name, up in (("long", True), ("short", False)):
        side = pb.get(name)
        if isinstance(side, dict):
            side["exit"] = exit_rule(side, up, ticker, h, atr)
    return pb


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
        out[side] = {**pb, "conditions": conditions, "score": _score(zone.get("score") or 0, conditions)}
    _reach_again(out, price)
    return out


# --- the agents -------------------------------------------------------------------------------

_SETUP_EN = {"dip_catch": "dip-catch", "range_low": "range-low buy", "pullback": "pullback buy",
             "fade": "fade the failed bounce", "breakdown": "breakdown retest", "none": "no setup",
             "value_swing": "oversold swing buy (2-8 weeks)"}


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
    jolt = pb.get("shock")
    if jolt:
        lines.append(f"Shock session {jolt['at'][:10]}: {jolt['change'] * 100:+.1f}% (open {_fmt(jolt['open'])}, "
                     f"high {_fmt(jolt['high'])}, low {_fmt(jolt['low'])}, prior close {_fmt(jolt['prev_close'])}). "
                     f"Levels from before it are far from the price now; trade the levels it made.")
    hold = pb.get("event_hold")
    if hold:
        lines.append(f"Event window: {hold['label']} at {hold['at_et']} - no fresh entry before it passes; "
                     f"only the first ladder step, the later steps wait for the reaction.")
    cap = (pb.get("frame") or {}).get("zone_cap_pct")
    if cap:
        lines.append(f"Reach: levels further than {cap:g}% from the price are watch levels, not entries or triggers.")
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
        name = "reclaim of the new low" if s.get("variant") == "reclaim" else _SETUP_EN[s["setup"]]
        lines.append(
            f"{side.upper()} {name}: zone {_fmt(z['low'])}-{_fmt(z['high'])} ({members}), "
            f"entries {entries} ({shares}%), stop {_fmt(s['stop'])}, "
            + ", ".join(f"{t['role'].upper()} {_fmt(t['price'])} ({t['basis']})" for t in tg)
            + f", time stop {s['time_stop']['bars']} x {s['time_stop']['interval']} bars, size {s['size_hint']}"
            + (", flat before the close" if s.get("flat_by_close") else "")
            + (f", after T1 trail the rest {s['exit']['trail_atr']:g} ATR behind the best price (T2 only a reference)"
               if (s.get("exit") or {}).get("rule") == "trail_atr" else "")
            + f". Conditions now: {met or 'n/a'}."
            + (f" {bit}" if (bit := calibration.prompt_bit(s.get("calibration"))) else ""))
        if (vs_line := value_swing.prompt_line(s)):
            lines.append(vs_line)                       # #167: the evidence and the risk budget
    if any((pb.get(s) or {}).get("calibration") for s in ("long", "short")):
        lines.append(calibration.PROMPT_RULE)
    note = "\n".join(lines)
    return note[:1500]          # #147: the records and their rule added ~250 characters to the 1200


def mandate() -> str:
    return (
        "Two-sided playbook, every run and every rating: right after the 'Win Probability' line write exactly three "
        "more lines with these English labels in any output language: 'Long Playbook: zone <low>-<high> / entries "
        "<p1>, <p2>, <p3> / confirm <what unlocks the last entry> / stop <price> / T1 <price> / T2 <price> / time "
        "stop <N bars>', 'Short Playbook: zone <low>-<high> / entry <price> / confirm <the rejection or breakdown you "
        "need to see> / stop <price> / T1 <price> / T2 <price> / time stop <N bars>', and 'Playbook Bias: <long|short|"
        "both|none> - <one sentence>'. Start from the desk's candidate levels; move a price only to another listed "
        "level and say why in the confirm text. Prefer levels the price can reach within the horizon: right after a "
        "shock session (a crash or squeeze day) use the levels that session made (its low, mid, open, high, the gap), "
        "not the old range the price left. The Playbook Bias weighs the two sides using the regime and the macro "
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


_ALIASES = {"구역": "zone", "진입": "entries", "확인": "confirm", "손절": "stop", "stop-loss": "stop",
            "stoploss": "stop", "목표1": "t1", "목표2": "t2", "1차": "t1", "2차": "t2", "시간": "time",
            "time-stop": "time"}


def _parse_side(body: str) -> dict | None:
    if re.match(r"\s*\**\s*none\b", body, re.IGNORECASE):
        return None
    out: dict = {"raw": body.strip()[:400]}
    for part in body.split("/"):
        key, _, rest = part.strip().strip("*").strip().partition(" ")
        key = _ALIASES.get(key.lower().strip("*: "), key.lower().strip("*: "))
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


# #263: a model T1 closer than this reward/risk (from the average entry, to the stop) is ignored -
# the desk dropped such targets on purpose (value_swing.T1_MIN_RR); the live SOXL run got T1 at 0.19R.
MODEL_T1_MIN_RR = 1.0


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
        lowest = min(e["price"] for e in s["entries"] if e["kind"] == "limit") if any(
            e["kind"] == "limit" for e in s["entries"]) else s["avg_entry"]
        highest = max(e["price"] for e in s["entries"])
        stop = take("stop", model.get("stop"),
                    lambda v, up=up, lo=lowest, hi=highest: (v < lo) if up else (v > hi))
        if stop is not None:
            s["stop"] = stop
            s["risk"] = _round(abs(s["avg_entry"] - stop))
        targets = []
        risk0 = abs(s["avg_entry"] - s["stop"]) or 1e-9

        def pays(v, role, s=s, up=up, risk0=risk0):
            # #263: the model's T1 must pay what the desk's own first targets must (value_swing.T1_MIN_RR)
            gain = (v - s["avg_entry"]) if up else (s["avg_entry"] - v)
            return gain > 0 and (role != "t1" or gain / risk0 >= MODEL_T1_MIN_RR)
        for t in s.get("targets") or []:
            v = take(t["role"], model.get(t["role"]), lambda v, role=t["role"], up=up: (v > 0) and pays(v, role))
            if v is not None:
                # the reach record (hit_rate · hit_n) was the desk target's, not this price's
                t = {k: x for k, x in t.items() if k not in ("hit_rate", "hit_n")}
                t = {**t, "price": v, "source": s["source"][t["role"]], "basis": "모델 제시"}
            risk = abs(s["avg_entry"] - s["stop"]) or 1e-9
            t = {**t, "rr": round(((t["price"] - s["avg_entry"]) if up else (s["avg_entry"] - t["price"])) / risk, 2)}
            targets.append(t)
        def ordered(a, b, up=up):
            return a["price"] < b["price"] if up else a["price"] > b["price"]
        if len(targets) == 2 and not ordered(targets[0], targets[1]):
            ignored["t2_order"] = targets[1]["price"]
            back = desk["targets"][1] if len(desk.get("targets") or []) > 1 else None
            if back and ordered(targets[0], back):
                risk = abs(s["avg_entry"] - s["stop"]) or 1e-9
                targets[1] = {**back, "rr": round(((back["price"] - s["avg_entry"]) if up
                                                   else (s["avg_entry"] - back["price"])) / risk, 2)}
                s["source"]["t2"] = back.get("source", "desk")
            else:
                targets = targets[:1]
                s["source"]["t2"] = "desk"
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


# --- live tracking (#128): the same playbooks against the bars and the price now --------------

STATES = ("waiting", "near", "in_zone", "confirmed", "invalid")
NEAR_ATR = 1.0


def side_state(side: dict | None, up: bool, price: float | None, atr: float) -> str | None:
    """waiting -> near (within 1 ATR of the zone) -> in_zone -> confirmed (the bars show the
    turn: a reclaim / higher low / rejection wick for a long, a rejection bar / lower high for a
    short) -> invalid (the price is through the stop). None for a side with no setup or price."""
    if not side or side.get("setup") in (None, "none") or not side.get("zone") or price is None:
        return None
    zone, stop = side["zone"], side.get("stop")
    met = {c["id"] for c in side.get("conditions") or [] if c.get("met")}
    if stop is not None and ((price <= stop) if up else (price >= stop)):
        return "invalid"
    # Only a turn *at the zone* confirms: a zone-bound signal (reclaim / wick / rejection bar)
    # carries up to 1 ATR past the zone; the swing structure (higher low / lower high) counts only
    # with the price in the zone - above a long's zone it is just the trend, not a dip bought.
    if up:
        inside = price <= zone["high"] + 0.25 * atr        # below the zone but above the stop: still in play
        if met & {"reclaim", "rejection_wick"} and price <= zone["high"] + NEAR_ATR * atr:
            return "confirmed"
        if inside:
            return "confirmed" if "higher_low" in met else "in_zone"
        return "near" if price - zone["high"] <= NEAR_ATR * atr else "waiting"
    inside = price >= zone["low"] - 0.25 * atr
    if "rejection_bar" in met and price >= zone["low"] - NEAR_ATR * atr:
        return "confirmed"
    if inside:
        return "confirmed" if "lower_high" in met else "in_zone"
    return "near" if zone["low"] - price <= NEAR_ATR * atr else "waiting"


def track(fresh: dict, run_pb: dict | None, primary: list[dict], h: horizons.Horizon) -> dict:
    """Live playbooks. With an analysis' playbooks (``run_pb``) its zones, ladder, stop, targets,
    time stop and sources stay - only the conditions, score and state follow the bars now, so the
    page never shows levels the plan did not have. Without, the fresh computation as it is."""
    out = dict(run_pb) if run_pb else dict(fresh)
    out["regime"] = fresh["regime"]
    ref = fresh["frame"]["reference_price"]
    out["frame"] = {**(run_pb or fresh)["frame"], "reference_price": ref}
    atr = out["frame"]["atr"] or fresh["frame"]["atr"]
    closes = [b["close"] for b in primary]
    vwap = _vwap(primary) if h.primary != "1d" else None
    for side, up in (("long", True), ("short", False)):
        s = dict(out.get(side) or {})
        if run_pb and s.get("zone") and s.get("setup") not in (None, "none"):
            zone = s["zone"]
            if up:
                conditions = _long_conditions(zone, primary, ref, atr, h.primary_k,
                                              fresh["regime"]["flags"]["climax_recent"])
                # #285: the chart edges the plan was scored on stay (desk.setup_filter)
                conditions += [c for c in s.get("conditions") or [] if c.get("how") == "edge"]
            else:
                conditions = _short_conditions(zone, primary, ref, atr, h.primary_k, vwap, _sma(closes, 20),
                                               s.get("setup"))
            s = {**s, "conditions": conditions, "score": _score(zone.get("score") or 0, conditions)}
        s["state"] = side_state(s, up, ref, atr)
        out[side] = s
    _reach_again(out, ref)
    # the run's sides keep the run's direction; the trend now is shown beside it (#166)
    out["direction_now"] = fresh.get("direction")
    if run_pb:
        hold_for_event(out, fresh.get("event_hold"))     # the calendar now, not at the analysis (#148)
    return out
