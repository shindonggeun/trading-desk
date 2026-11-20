"""Where the chart says "accumulation could happen here" - by view (short · mid · long) - and the legs
of the moves that got the price here (#751).

The picture is the one an analyst draws on a gold daily chart: each up leg boxed with its rise and its
length (34.86% in 245 days, then 27.86% in 35 days: faster and faster), Fibonacci extensions from the
bottom, and an old sideways box the price broke out of and never came back to - "if it comes back,
that is where buyers sat before". Per view the same functions run on that view's bars (VIEWS):

- **legs**: a zigzag of the highs' and lows' swings at least leg_atr typical bars wide; each leg's rise or fall
  (%), its days and bars, its slope (% a day) and, over the last up legs of the advance, whether they
  are getting shorter and steeper (an overheating sign, not a top call);
- **zones**: candidate bands made of
  ① an old **sideways box** (BOX_MIN bars within BOX_ATR x ATR) the price left upward and has not
     come back into since - the volume drying inside it says more;
  ② **Fibonacci** levels: the extensions of the advance's first leg from its low (0 · 1 · 1.618 · 2 ·
     2.618 · 3 · 3.618 · 4) and the retracements of the last up leg (0.382 · 0.5 · 0.618);
  ③ an old swing high the price closed through and stayed above - **resistance turned support**;
  ④ the **volume profile**'s thick nodes and its point of control (desk.volume_profile);
  a level joins a box it falls in (JOIN_ATR around it), the rest group when within CLUSTER_ATR; the
  more kinds overlap the higher the score. A band below the price is an accumulation candidate, one
  above (a box the price fell out of, a support turned resistance ...) the supply overhead.
- **stats**: how often the price bounced after reaching such a band in the past, against the same walk
  on the same ticker's returns shuffled (desk.accumulation_bt) - ``reference_only`` without an edge,
  ``small_sample`` when there were few.

Arithmetic on price history: neither a forecast nor a recommendation (``NOTE``).
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from . import volume_profile

STATS_FILE = Path(__file__).parent / "data" / "backtest" / "accumulation.json"
NOTE = "과거 가격 구조로 본 후보 자리예요 — 예측도, 매수 · 매도 추천도 아니에요."
NO_EDGE_KO = "참고용 · 통계적 우위 없음"
NO_STATS_KO = "참고용 · 과거 통계 없음"

# view -> the bars it reads and how wide its pieces are
# (box_max_pct: a box wider than this is no sideways box on that view; max_pct: a band further than this
# from the price is not near on that view - beside MAX_DISTANCE_ATR, for the wild ATRs of a leveraged ETF)
VIEWS: dict[str, dict] = {
    "short": {"label": "단기", "timeframe": "240m", "box_min": 24, "leg_atr": 6.0, "swing_k": 3,
              "box_max_pct": 6.0, "max_pct": 15.0},
    "mid": {"label": "중기", "timeframe": "1d", "box_min": 20, "leg_atr": 8.0, "swing_k": 4,
            "box_max_pct": 15.0, "max_pct": 35.0},
    "long": {"label": "장기", "timeframe": "1w", "box_min": 10, "leg_atr": 5.0, "swing_k": 3,
             "box_max_pct": 35.0, "max_pct": 60.0},
}
VIEW_TF_KO = {"240m": "4시간봉", "60m": "1시간봉", "1d": "일봉", "1w": "주봉"}
ATR_N = 14
BOX_ATR = 3.0              # a sideways box spans at most this many ATR high to low
BREAK_ATR = 0.5            # the breakout: a close this far beyond the box
BREAK_WITHIN = 10          # ...within this many bars of the box's end
DRYING = 0.85              # the box's second half traded under this share of its first half: volume drying
FLIP_ATR = 0.25            # a swing high closed through by this much ...
FLIP_HOLD_ATR = 0.5        # ... and never closed back under it by this much: resistance turned support
JOIN_ATR = 0.5             # a level this close to a box joins it
CLUSTER_ATR = 0.5          # levels this close group
MIN_WIDTH_ATR = 0.3        # a band of levels is at least this wide
MAX_DISTANCE_ATR = 25.0    # bands further than this from the price are not "near"
PER_SIDE = 2
MIN_SCORE = 2.0            # a box alone, or two kinds overlapping
WEIGHT = {"box": 2.0, "drying": 0.5, "flip": 1.0, "fib": 1.0, "volume": 1.0}
FIB_EXT = (0.0, 1.0, 1.618, 2.0, 2.618, 3.0, 3.618, 4.0)
FIB_RET = (0.382, 0.5, 0.618)
LEGS_KEPT = 6
ACCEL_LEGS = 3             # this many up legs in a row, each shorter and steeper: "faster and faster"
MIN_BARS = 60
MIN_ODDS_N = 30            # fewer touches than this in the record: a small sample
KIND_KO = {"box": "옛 횡보 상자", "fib": "피보나치", "flip": "저항→지지", "volume": "매물대",
           "flip_down": "지지→저항", "box_down": "무너진 횡보 상자"}


def _r(x: float) -> float:
    return round(x, 2) if x >= 1 else round(x, 4)


def _fmt(p: float) -> str:
    return f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}"


def clean(bars: list[dict]) -> list[dict]:
    return [b for b in bars or [] if b.get("low", 0) > 0 and b.get("close", 0) > 0 and b["high"] >= b["low"]]


def atrs(bars: list[dict], n: int = ATR_N) -> list[float]:
    """Each bar's ATR: the mean true range of the n bars up to it (fewer at the start)."""
    tr, out = [], []
    for i, b in enumerate(bars):
        prev = bars[i - 1]["close"] if i else b["close"]
        tr.append(max(b["high"], prev) - min(b["low"], prev))
        window = tr[-n:]
        out.append(sum(window) / len(window))
    return out


def _date(at: str | None) -> str | None:
    return at[:10] if at else None


def _days(a: str | None, b: str | None) -> int | None:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- legs

def zigzag(bars: list[dict], thr: float) -> tuple[list[tuple[int, str]], tuple[int, str] | None]:
    """The swing points (index, "low" | "high") that a move of ``thr`` (a share, 0.12 = 12%) confirmed,
    and the last extreme not confirmed yet (where the leg in progress has got to)."""
    piv: list[tuple[int, str]] = []
    if len(bars) < 3:
        return piv, None
    trend, hi, lo = 0, 0, 0
    for i in range(1, len(bars)):
        b = bars[i]
        if trend == 0:
            if b["high"] > bars[hi]["high"]:
                hi = i
            if b["low"] < bars[lo]["low"]:
                lo = i
            if lo < hi and bars[hi]["high"] >= bars[lo]["low"] * (1 + thr):
                piv.append((lo, "low"))
                trend = 1
            elif hi < lo and bars[lo]["low"] <= bars[hi]["high"] * (1 - thr):
                piv.append((hi, "high"))
                trend = -1
        elif trend == 1:
            if b["high"] >= bars[hi]["high"]:
                hi = i
            elif b["low"] <= bars[hi]["high"] * (1 - thr):
                piv.append((hi, "high"))
                trend = -1
                lo = min(range(hi + 1, i + 1), key=lambda j: bars[j]["low"])
        else:
            if b["low"] <= bars[lo]["low"]:
                lo = i
            elif b["high"] >= bars[lo]["low"] * (1 + thr):
                piv.append((lo, "low"))
                trend = 1
                hi = max(range(lo + 1, i + 1), key=lambda j: bars[j]["high"])
    last = (hi, "high") if trend == 1 else (lo, "low") if trend == -1 else None
    return piv, last


def leg_threshold(bars: list[dict], leg_atr: float) -> float:
    """A leg's least size as a share: leg_atr typical bar ranges (the median of ATR(14) over the close)."""
    a = atrs(bars)
    rel = [x / b["close"] for x, b in zip(a[ATR_N:], bars[ATR_N:], strict=False) if b["close"] > 0] or [0.01]
    return max(0.01, leg_atr * statistics.median(rel))


def legs(bars: list[dict], leg_atr: float) -> dict:
    """The legs between the zigzag's swing points (and the one in progress), the last LEGS_KEPT, with a
    sentence on the up legs of the current advance when they get shorter and steeper."""
    bars = clean(bars)
    if len(bars) < MIN_BARS:
        return {"items": [], "accelerating": False, "sentence": None, "threshold_pct": None}
    thr = leg_threshold(bars, leg_atr)
    piv, last = zigzag(bars, thr)
    points = piv + ([last] if last and (not piv or last[0] > piv[-1][0]) else [])
    items = []
    for k in range(1, len(points)):
        (i, a_kind), (j, _) = points[k - 1], points[k]
        a = bars[i]["low" if a_kind == "low" else "high"]
        b = bars[j]["high" if a_kind == "low" else "low"]
        days = _days(bars[i].get("at"), bars[j].get("at"))
        pct = (b / a - 1) * 100
        items.append({"direction": "up" if a_kind == "low" else "down",
                      "from": {"at": bars[i].get("at"), "price": _r(a)}, "to": {"at": bars[j].get("at"), "price": _r(b)},
                      "pct": round(pct, 2), "bars": j - i, "days": days,
                      "pct_per_day": round(pct / days, 3) if days else None,
                      "live": k == len(points) - 1 and last is not None and points[-1] == last})
    items = items[-LEGS_KEPT:]
    ups = _advance([x for x in items if x["direction"] == "up"])
    if len(ups) >= 2 and ups[-1]["live"] and ups[-1]["pct"] < ups[-2]["pct"]:
        ups = ups[:-1]          # a rise just begun is no leg yet: short and steep only for having just started
    accel = len(ups) >= ACCEL_LEGS and all(
        (b["days"] or 0) < (a["days"] or 0) and (b["pct_per_day"] or 0) > (a["pct_per_day"] or 0)
        for a, b in zip(ups[-ACCEL_LEGS:], ups[-ACCEL_LEGS + 1:], strict=False))
    return {"items": items, "accelerating": accel, "threshold_pct": round(thr * 100, 2),
            "sentence": leg_sentence(ups, accel)}


def _advance(ups: list[dict]) -> list[dict]:
    """The up legs of the current staircase: each starting above the one before (higher lows)."""
    out: list[dict] = []
    for x in ups:
        if out and x["from"]["price"] <= out[-1]["from"]["price"]:
            out = []
        out.append(x)
    return out


def _leg_words(x: dict) -> str:
    return f"{x['pct']:+.2f}% · {x['days']}일" if x.get("days") else f"{x['pct']:+.2f}% · {x['bars']}봉"


def leg_sentence(ups: list[dict], accel: bool) -> str | None:
    if len(ups) < 2:
        return None
    chain = " → ".join(_leg_words(x) for x in ups[-4:])
    return f"상승 다리 {chain}" + (" — 점점 짧고 가팔라져요(과열 신호일 수 있어요)." if accel else ".")


# ---------------------------------------------------------------- zones

def boxes(bars: list[dict], a: list[float], box_min: int, max_pct: float = 100.0) -> list[dict]:
    """Sideways boxes: runs of at least ``box_min`` bars whose high-to-low stays within BOX_ATR x ATR
    (and ``max_pct`` of the low), each with where the price left it (``broke``: up · down · None) and
    whether it came back since."""
    out, n, i = [], len(bars), 0
    while i < n - box_min:
        hi, lo, j = bars[i]["high"], bars[i]["low"], i + 1
        while j < n:
            h, low = max(hi, bars[j]["high"]), min(lo, bars[j]["low"])
            if h - low > BOX_ATR * a[j] or h > low * (1 + max_pct / 100):
                break
            hi, lo, j = h, low, j + 1
        if j - i < box_min:
            i += 1
            continue
        end = j - 1
        unit = a[end]
        broke, at = None, None
        for k in range(j, min(n, j + BREAK_WITHIN)):
            if bars[k]["close"] > hi + BREAK_ATR * unit:
                broke, at = "up", k
                break
            if bars[k]["close"] < lo - BREAK_ATR * unit:
                broke, at = "down", k
                break
        after = bars[at + 1:] if at is not None else []
        back = (any(b["low"] <= hi for b in after) if broke == "up" else
                any(b["high"] >= lo for b in after) if broke == "down" else None)
        vols = [b.get("volume") or 0 for b in bars[i:j]]
        half = len(vols) // 2
        drying = (all(vols) and statistics.median(vols[half:]) < DRYING * statistics.median(vols[:half])) if half else False
        out.append({"start": i, "end": end, "low": lo, "high": hi, "broke": broke, "broke_at": at, "back": back,
                    "drying": bool(drying)})
        i = j
    return out


def _swings(bars: list[dict], k: int, field: str) -> list[int]:
    """Bars whose ``field`` is the extreme of the k bars each side."""
    pick = max if field == "high" else min
    return [i for i in range(k, len(bars) - k)
            if bars[i][field] == pick(b[field] for b in bars[i - k:i + k + 1])]


def flips(bars: list[dict], a: list[float], k: int, price: float) -> list[dict]:
    """Old swing highs the price closed through by FLIP_ATR and never closed back under by FLIP_HOLD_ATR
    (resistance turned support, below the price); old swing lows the mirror (above it)."""
    out = []
    for i in _swings(bars, k, "high"):
        h = bars[i]["high"]
        cross = next((j for j in range(i + 1, len(bars)) if bars[j]["close"] > h + FLIP_ATR * a[j]), None)
        if cross is not None and price > h and all(b["close"] >= h - FLIP_HOLD_ATR * a[j]
                                                     for j, b in enumerate(bars[cross:], cross)):
            out.append({"price": h, "kind": "flip", "at": bars[i].get("at")})
    for i in _swings(bars, k, "low"):
        lo = bars[i]["low"]
        cross = next((j for j in range(i + 1, len(bars)) if bars[j]["close"] < lo - FLIP_ATR * a[j]), None)
        if cross is not None and price < lo and all(b["close"] <= lo + FLIP_HOLD_ATR * a[j]
                                                     for j, b in enumerate(bars[cross:], cross)):
            out.append({"price": lo, "kind": "flip_down", "at": bars[i].get("at")})
    return out


def fibs(bars: list[dict], leg_atr: float) -> list[dict]:
    """Extensions of the advance's first leg from its low, retracements of its last up leg."""
    thr = leg_threshold(bars, leg_atr)
    piv, last = zigzag(bars, thr)
    points = piv + ([last] if last and (not piv or last[0] > piv[-1][0]) else [])
    lows = [p for p in points if p[1] == "low"]
    if not lows:
        return []
    # the advance: from the lowest swing low, its first leg up
    base = min(lows, key=lambda p: bars[p[0]]["low"])
    after = [p for p in points if p[0] > base[0] and p[1] == "high"]
    out = []
    if after:
        lo, hi = bars[base[0]]["low"], bars[after[0][0]]["high"]
        for r in FIB_EXT:
            out.append({"price": lo + r * (hi - lo), "kind": "fib", "name": f"피보 확장 {r:g}", "ratio": r,
                        "at": bars[base[0]].get("at")})
    ups = [(points[k - 1][0], points[k][0]) for k in range(1, len(points)) if points[k - 1][1] == "low"]
    if ups:
        i, j = ups[-1]
        lo, hi = bars[i]["low"], bars[j]["high"]
        for r in FIB_RET:
            out.append({"price": hi - r * (hi - lo), "kind": "fib", "name": f"피보 되돌림 {r:g}", "ratio": r,
                        "at": bars[i].get("at")})
    return out


def volume_levels(bars: list[dict], atr: float, tf: str) -> list[dict]:
    vp = volume_profile.profile(bars, atr, tf) if any(b.get("volume") for b in bars) else None
    if not vp:
        return []
    out = [{"price": vp["poc"], "kind": "volume", "name": "매물대 중심"}]
    out += [{"price": nd["price"], "kind": "volume", "name": "매물대 두꺼운 곳"}
            for nd in vp.get("nodes") or [] if nd["kind"] == "thick"]
    return out


def _box_words(bx: dict) -> str:
    return f"{_date(bx['from_at'])}~{_date(bx['to_at'])} 횡보 {bx['bars']}봉"


def zones(bars: list[dict], view: str, price: float | None = None, tf: str | None = None) -> dict:
    """The view's candidate bands below (accumulation) and above (supply) ``price`` (the last close)."""
    spec = VIEWS[view]
    bars = clean(bars)
    if len(bars) < MIN_BARS:
        return {"below": [], "above": [], "atr": None}
    a = atrs(bars)
    atr = a[-1]
    price = price or bars[-1]["close"]
    bands: list[dict] = []
    for bx in boxes(bars, a, spec["box_min"], spec["box_max_pct"]):
        if bx["broke"] is None or bx["back"]:
            continue                                 # never left, or already came back: not an untested box
        up = bx["broke"] == "up"
        info = {"from_at": bars[bx["start"]].get("at"), "to_at": bars[bx["end"]].get("at"),
                "bars": bx["end"] - bx["start"] + 1, "width_pct": round((bx["high"] / bx["low"] - 1) * 100, 2),
                "drying": bx["drying"], "broke": bx["broke"], "broke_at": bars[bx["broke_at"]].get("at")}
        kinds = {"box" if up else "box_down"}
        basis = [f"{_box_words(info)} 뒤 {'위로' if up else '아래로'} 벗어나 다시 오지 않은 자리"]
        if bx["drying"]:
            basis.append("횡보 중 거래량 감소")
        bands.append({"low": bx["low"], "high": bx["high"], "kinds": kinds, "basis": basis, "box": info,
                      "levels": [], "score": WEIGHT["box"] + (WEIGHT["drying"] if bx["drying"] else 0.0),
                      "from_at": info["from_at"]})
    levels = flips(bars, a, spec["swing_k"], price) + fibs(bars, spec["leg_atr"]) + volume_levels(bars, atr, tf or spec["timeframe"])
    loose = []
    for lv in levels:
        p = lv["price"]
        home = next((b for b in bands if b["low"] - JOIN_ATR * atr <= p <= b["high"] + JOIN_ATR * atr), None)
        if home is None:
            loose.append(lv)
            continue
        home["levels"].append(lv)
    loose.sort(key=lambda lv: lv["price"])
    group: list[dict] = []
    for lv in loose + [None]:
        if lv is not None and (not group or lv["price"] - group[-1]["price"] <= CLUSTER_ATR * atr):
            group.append(lv)
            continue
        if group:
            lo, hi = group[0]["price"], group[-1]["price"]
            pad = max(0.0, (MIN_WIDTH_ATR * atr - (hi - lo)) / 2)
            bands.append({"low": lo - pad, "high": hi + pad, "kinds": set(), "basis": [], "box": None,
                          "levels": group, "score": 0.0, "from_at": min(g.get("at") or "~" for g in group)})
        group = [lv] if lv is not None else []
    out: dict = {"below": [], "above": [], "atr": _r(atr)}
    for b in bands:
        for lv in b["levels"]:
            if lv["kind"] not in b["kinds"]:
                b["kinds"].add(lv["kind"])
                b["score"] += WEIGHT.get(lv["kind"].replace("_down", ""), 1.0)
            name = lv.get("name") or (f"{_date(lv.get('at'))} 고점 저항→지지" if lv["kind"] == "flip" else
                                      f"{_date(lv.get('at'))} 저점 지지→저항" if lv["kind"] == "flip_down" else KIND_KO[lv["kind"]])
            word = f"{name} {_fmt(lv['price'])}"
            if word not in b["basis"]:
                b["basis"].append(word)
        if b["score"] < MIN_SCORE or len(b["kinds"]) < (1 if b["box"] else 2):
            continue
        side = "below" if b["high"] < price else "above" if b["low"] > price else "inside"
        if side == "inside":
            dist = 0.0
        else:
            edge = b["high"] if side == "below" else b["low"]
            if abs(edge - price) > MAX_DISTANCE_ATR * atr or abs(edge / price - 1) * 100 > spec["max_pct"]:
                continue
            dist = (edge / price - 1) * 100
        # a box the price left downward, a support turned resistance: supply, not accumulation
        role = "supply" if side == "above" or ("box_down" in b["kinds"]) else "accumulation"
        row = {"low": _r(b["low"]), "high": _r(b["high"]), "mid": _r((b["low"] + b["high"]) / 2),
               "score": round(b["score"], 1), "kinds": sorted(b["kinds"]), "basis": b["basis"],
               "box": b["box"], "role": role, "from_at": b["from_at"] if b["from_at"] != "~" else None,
               "distance_pct": round(dist, 2), "distance_atr": round(abs(dist) / 100 * price / atr, 1)}
        if side == "inside":
            row["inside"] = True
            out["below"].append(row)
        else:
            out[side].append(row)
    for side in ("below", "above"):
        out[side] = sorted(out[side], key=lambda r: abs(r["distance_pct"]))[:PER_SIDE]
    return out


# ---------------------------------------------------------------- the past record

@lru_cache(maxsize=1)
def _table(path: str = str(STATS_FILE)) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stats_for(view: str, table: dict | None = None) -> dict | None:
    got = ((_table() if table is None else table).get("views") or {}).get(view)
    return got or None


def odds(view: str, table: dict | None = None, tf: str | None = None) -> dict:
    """The view's past record in the shape the chart reads quote (#664 · #679): the bounce share after a
    touch, the shuffled baseline's, the verdict - and whether to lean on it. A record measured on other
    bars (the short view read on 1h bars, measured on 4h) is no record of these."""
    s = stats_for(view, table)
    if s and tf and s.get("timeframe") and s["timeframe"] != tf:
        s = None
    verdict = (s or {}).get("verdict")
    n = (s or {}).get("n") or 0
    return {"stats": s, "reference_only": verdict != "edge", "small_sample": n < MIN_ODDS_N,
            "reference_label": None if verdict == "edge" else NO_EDGE_KO if verdict in ("no_edge", "unstable") else NO_STATS_KO}


def _odds_words(o: dict) -> str:
    s = o.get("stats")
    if not s or s.get("bounce_pct") is None:
        return "과거 검증 없음"
    base = s.get("baseline_pct")
    words = (f"과거 닿았을 때 {s.get('horizon_bars')}봉 안 반등 {s['bounce_pct']:.0f}%"
             f"(섞은 가격 {base:.0f}%, 표본 {s['n']:,}{' · 적음' if o['small_sample'] else ''})" if base is not None else
             f"과거 닿았을 때 반등 {s['bounce_pct']:.0f}%(표본 {s['n']:,})")
    return words


# ---------------------------------------------------------------- one view, all views

def view(bars: list[dict], name: str, tf: str | None = None, table: dict | None = None,
         price: float | None = None) -> dict:
    """One view: the bands, the legs, the record and one sentence. The structure is read on ``bars``
    (finished ones); above / below and the distances on ``price`` (the price now; else the last close)."""
    spec = VIEWS[name]
    tf = tf or spec["timeframe"]
    bars = clean(bars)
    if len(bars) < MIN_BARS:
        return {"view": name, "label": spec["label"], "timeframe": tf, "error": "봉이 모자라요", "below": [], "above": [],
                "legs": None, "sentence": None}
    price = price or bars[-1]["close"]
    z = zones(bars, name, price, tf)
    o = odds(name, table, tf)
    out = {"view": name, "label": spec["label"], "timeframe": tf, "timeframe_ko": VIEW_TF_KO.get(tf, tf),
           "as_of": bars[-1].get("at"), "price": _r(price), "atr": z["atr"],
           "below": z["below"], "above": z["above"], "legs": legs(bars, spec["leg_atr"]), **o}
    out["sentence"] = sentence(out)
    return out


def sentence(v: dict) -> str:
    """e.g. "중기(일봉): 3,319~3,409 — 2025-03-03~2025-04-01 횡보 22봉 뒤 위로 벗어나 다시 오지 않은 자리 · 피보 확장 2
    3,380. 지금보다 −4.00%. 과거 닿았을 때 20봉 안 반등 43%(섞은 가격 39%, 표본 2,323) · 참고용 · 통계적 우위 없음"."""
    head = f"{v['label']}({v.get('timeframe_ko') or v['timeframe']})"
    acc = [z for z in v.get("below") or [] if z["role"] == "accumulation"]
    if not acc:
        return f"{head}: 지금 아래에 매집 후보 구간이 없어요."
    z = acc[0]
    where = "지금 구간 안" if z.get("inside") else f"지금보다 {z['distance_pct']:+.2f}%".replace("-", "−")
    basis = " · ".join(z["basis"][:2])
    tail = _odds_words(v)
    label = f" · {v['reference_label']}" if v.get("reference_label") else ""
    return f"{head}: {_fmt(z['low'])}~{_fmt(z['high'])} — {basis}. {where}. {tail}{label}"


def views(bars_by_view: dict[str, list[dict] | None], tfs: dict[str, str] | None = None,
          table: dict | None = None, price: float | None = None) -> tuple[dict, dict]:
    """(accumulation, legs) for /api/chart-read: per view its bands and sentence, and its legs - the
    bands placed against ``price`` (the price now)."""
    acc: dict = {"note": NOTE}
    leg: dict = {}
    for name in VIEWS:
        bars = bars_by_view.get(name)
        if not bars:
            acc[name] = None
            leg[name] = None
            continue
        v = view(bars, name, (tfs or {}).get(name), table, price)
        leg[name] = v.pop("legs")
        acc[name] = v
    return acc, leg
