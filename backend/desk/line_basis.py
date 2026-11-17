"""Where each plan line comes from, the gaps not yet filled, and plain sentences for a beginner (#465).

The chart shows the analysis' entry, stop and target and its long / short trigger lines, but not
what each number is anchored on - and a Hold plan's entry / stop / target are often a short
scenario's numbers drawn next to the long trigger. This reads, from the levels the page already
has (desk.sr: zones, Fibonacci, trendlines) plus the unfilled gaps found here:

- ``find_gaps(daily, weekly, ticker)``: the price gaps on the daily and weekly bars that the price
  has not gone back to close - the unfilled part of each (it shrinks as the price comes back in),
  the fill price (the far edge: the bar before the gap) and the market's own date; ``place`` adds
  the side and distance from the price now (the found ones can be cached, the price moves);
- ``basis(plan, sr, gaps)``: per plan line (``LINES``) its scenario - long, short, or the side its
  levels point when the rating is Hold (desk.plan.sides) - and the zones, Fibonacci levels,
  trendlines and gaps it sits on (within ``MATCH_ATR`` ATR or ``MATCH_PCT``);
- ``explain(sr, gaps, lines)``: short Korean sentences, rule based (no LLM): which zone the price is
  at or between, what to do there (take some profit under resistance, a stop under support, a close
  over it as the breakout check), the nearest gap to fill, and one line per plan line.

The numbers are the desk's own; the sentences say tendencies, not promises. Pure functions.
"""

from __future__ import annotations

from . import plan as plan_mod
from .sr import _atr, _local_day

LINES = (("entry_price", "진입"), ("stop_loss", "손절"), ("price_target", "목표"),
         ("long_trigger", "롱 조건"), ("short_trigger", "숏 · 축소 조건"))
MATCH_ATR = 0.5            # a line this close (in ATR) to a zone edge, level or gap counts as on it
MATCH_PCT = 0.005          # ...or this share of the price, when the ATR is missing or tiny
GAP_LOOKBACK = {"1d": 120, "1w": 104}
GAP_MAX = 3                # per direction and timeframe, the nearest first
GAP_MIN_ATR = 0.1          # a gap smaller than this (daily ATR) is noise
NEAR_ATR = 0.5             # the price "at" a zone: inside it or this close to its edge
GAP_EXPLAIN_PCT = 15.0     # a gap farther than this from the price is listed, not talked about
BASIS_PARTS = 2            # the zone's reasons named in a sentence (the rest are in the levels)
SCENARIO_KO = {"long": "롱", "short": "숏", "flat": "관망"}
TF_KO = {"1d": "일봉", "1w": "주봉", "1mo": "월봉"}


def _pct(a: float, ref: float) -> float:
    return round((a / ref - 1) * 100, 2) if ref else 0.0


def _fmt(p: float) -> str:
    return f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}"


def _signed(v: float) -> str:
    return f"{v:+.1f}%"


# ------------------------------------------------------------------ gaps

def _gaps_on(bars: list[dict], tf: str, atr: float, ticker: str) -> list[dict]:
    """Unfilled gaps of ``bars`` (oldest first), the newest first per direction - the newest open
    gap of a direction is also the nearest to the price (every bar since stayed beyond the older one)."""
    out = {"up": [], "down": []}
    n = len(bars)
    lookback = GAP_LOOKBACK.get(tf, 120)
    lows_after = highs_after = None
    for i in range(n - 1, max(0, n - lookback) - 1, -1):
        bar = bars[i]
        lows_after = bar["low"] if lows_after is None else min(lows_after, bar["low"])
        highs_after = bar["high"] if highs_after is None else max(highs_after, bar["high"])
        if i == 0:
            break
        prev = bars[i - 1]
        # an up gap: today's low over yesterday's high; still open while every bar since stayed over it
        day = _local_day(bar["at"], ticker).isoformat()   # a Korean bar opens at 15:00 UTC the day before
        if prev["high"] < bar["low"] and lows_after > prev["high"] and lows_after - prev["high"] >= GAP_MIN_ATR * atr:
            out["up"].append({"direction": "up", "bottom": prev["high"], "top": lows_after, "fill_price": prev["high"],
                              "formed_at": day})
        elif prev["low"] > bar["high"] and highs_after < prev["low"] and prev["low"] - highs_after >= GAP_MIN_ATR * atr:
            out["down"].append({"direction": "down", "bottom": highs_after, "top": prev["low"], "fill_price": prev["low"],
                                "formed_at": day})
    return [{**g, "timeframe": tf, "top": round(g["top"], 4), "bottom": round(g["bottom"], 4),
             "fill_price": round(g["fill_price"], 4)} for g in out["up"][:GAP_MAX] + out["down"][:GAP_MAX]]


def find_gaps(daily: list[dict], weekly: list[dict] | None, ticker: str) -> list[dict]:
    """The unfilled daily and weekly gaps, with no price-now fields (``place`` adds them) - so the
    found ones can be kept a while. The noise floor is in the daily bars' own ATR, whatever horizon."""
    if not daily:
        return []
    atr = _atr(daily) or daily[-1]["close"] * 0.01
    return _gaps_on(daily, "1d", atr, ticker) + (_gaps_on(weekly, "1w", atr, ticker) if weekly else [])


def place(found: list[dict], reference: float) -> list[dict]:
    """The gaps against the price now: side, distance and a line of text, nearest first."""
    if not reference:
        return []
    rows = []
    for g in found:
        side = "below" if g["fill_price"] < reference else "above"
        dist = _pct(g["fill_price"], reference)
        what = "상승 갭" if g["direction"] == "up" else "하락 갭"
        rows.append({**g, "side": side, "distance_pct": dist,
                     "text": f"{TF_KO.get(g['timeframe'], g['timeframe'])} {what}({g['formed_at']}) 메움 가격 "
                             f"{_fmt(g['fill_price'])} — {'위' if side == 'above' else '아래'}로 {_signed(dist)}"})
    return sorted(rows, key=lambda g: abs(g["distance_pct"]))


def gaps(daily: list[dict], weekly: list[dict] | None, reference: float, ticker: str = "") -> list[dict]:
    """``find_gaps`` placed against ``reference``."""
    return place(find_gaps(daily, weekly, ticker), reference)


# ------------------------------------------------------------------ what a price sits on

def _tolerance(price: float, atr: float | None) -> float:
    return max((atr or 0) * MATCH_ATR, price * MATCH_PCT)


def anchors(price: float, sr: dict, gap_rows: list[dict]) -> list[dict]:
    """The zones, Fibonacci levels, trendlines and gaps ``price`` sits on, nearest first."""
    tol = _tolerance(price, sr.get("atr"))
    out = []
    for lv in sr.get("levels") or []:
        lo, hi = (lv.get("zone") or [lv["price"], lv["price"]])[:2]
        if lo - tol <= price <= hi + tol:
            kind = "지지" if lv["kind"] == "support" else "저항"
            where = "안" if lo <= price <= hi else "하단 근처" if price < lo else "상단 근처"
            parts = [p for p in (lv.get("basis") or "").split(" · ") if p]
            why = " · ".join(parts[:BASIS_PARTS]) + (" 등" if len(parts) > BASIS_PARTS else "")
            out.append({"kind": "sr_zone", "price": lv["price"], "zone": [lo, hi], "distance": abs(price - lv["price"]),
                        "text": f"{TF_KO.get(lv.get('timeframe'), lv.get('timeframe') or '')} {kind} 구역"
                                f"({_fmt(lo)}~{_fmt(hi)}) {where}" + (f"({why})" if why else "")})
    fib = sr.get("fibonacci") or {}
    for f in fib.get("levels") or []:
        if f["ratio"] in (0.0, 1.0):          # the swing's own high and low, not a retracement
            continue
        if abs(f["price"] - price) <= tol:
            out.append({"kind": "fib", "price": f["price"], "distance": abs(price - f["price"]),
                        "text": f"피보나치 {f['ratio']} 되돌림({_fmt(f['price'])})"})
    for t in sr.get("trendlines") or []:
        now = t.get("price_now")
        if now is not None and abs(now - price) <= tol:
            kind = "지지" if t["kind"] == "support" else "저항"
            out.append({"kind": "trendline", "price": now, "distance": abs(price - now),
                        "text": f"{TF_KO.get(t.get('timeframe'), t.get('timeframe') or '')} {kind} 추세선(지금 {_fmt(now)})"})
    for g in gap_rows:
        if abs(g["fill_price"] - price) <= tol:
            out.append({"kind": "gap", "price": g["fill_price"], "distance": abs(price - g["fill_price"]),
                        "text": f"{TF_KO.get(g['timeframe'], g['timeframe'])} 갭 메움 가격({_fmt(g['fill_price'])})"})
    out.sort(key=lambda a: a["distance"])
    for a in out:
        a["distance"] = round(a["distance"], 4)
    return out


# ------------------------------------------------------------------ plan lines

def _scenario(field: str, sides: dict, bias: str | None, plan: dict) -> str | None:
    """The trigger lines are their own side. Entry, stop and target are the side their levels point
    (desk.plan.sides - what the numbers mean, also on a Buy / Sell whose levels contradict the rating,
    which ``side_conflict`` flags); with no stop to tell, the target above / under the entry; then the
    rating's side; None when nothing says (the sentence leaves the scenario out)."""
    if field == "long_trigger":
        return "long"
    if field == "short_trigger":
        return "short"
    if sides.get("levels_side"):
        return sides["levels_side"]
    entry, target = plan.get("entry_price"), plan.get("price_target")
    if entry is not None and target is not None and entry != target:
        return "long" if target > entry else "short"
    return bias if bias in ("long", "short") else None


def _role_sentence(field: str, scenario: str | None, anchors_: list[dict], hold: bool) -> str:
    top = anchors_[0]["text"] if anchors_ else None
    on = f"근거: {top}" if top else "근거: 가까운 지지 · 저항 · 갭과 겹치지 않음(모델이 정한 숫자)"
    note = "(관망 계획이라 지금 바로 할 일은 아님)" if hold and field in ("entry_price", "stop_loss", "price_target") else ""
    who = f"{SCENARIO_KO[scenario]} 시나리오{note}" if scenario else f"방향을 알 수 없는 계획{note}"
    role = {
        "entry_price": "진입 자리(여기 오면 들어감)",
        "stop_loss": "손절 자리(여기를 넘으면 시나리오가 틀린 것으로 보고 나옴)",
        "price_target": "익절 자리(여기서 일부 또는 전부 익절)",
        "long_trigger": "롱 검토 기준(이 위로 회복 · 돌파하면)",
        "short_trigger": "숏 · 비중 축소 검토 기준(이 아래로 이탈하면)",
    }[field]
    return f"{who}의 {role}. {on}."


def basis(plan: dict | None, sr: dict, gap_rows: list[dict]) -> list[dict]:
    """One row per plan line with a price: its scenario, what it sits on and a sentence."""
    if not plan:
        return []
    sides = plan_mod.sides(plan)
    bias = plan.get("bias")
    hold = bias == "flat"
    ref = sr.get("reference_price") or 0
    rows = []
    for field, label in LINES:
        price = plan.get(field)
        if price is None:
            continue
        scen = _scenario(field, sides, bias, plan)
        found = anchors(float(price), sr, gap_rows)
        rows.append({
            "line": field, "label": label, "price": price, "source": "plan",
            "scenario": scen, "scenario_ko": SCENARIO_KO.get(scen) if scen else None,
            "hold_plan": hold and field in ("entry_price", "stop_loss", "price_target"),
            "from_now_pct": _pct(float(price), ref) if ref else None,
            "anchors": found, "text": f"{label} {_fmt(float(price))} — " + _role_sentence(field, scen, found, hold),
        })
    return rows


# ------------------------------------------------------------------ sentences

def _edges(lv: dict) -> tuple[float, float]:
    lo, hi = (lv.get("zone") or [lv["price"], lv["price"]])[:2]
    return lo, hi


def _zones_around(sr: dict) -> tuple[dict | None, dict | None, dict | None]:
    """The zone the price is at (inside it, or the nearest edge within NEAR_ATR), and the nearest
    other zone wholly over it and under it."""
    ref, atr = sr.get("reference_price"), sr.get("atr") or 0
    levels = sr.get("levels") or []

    def edge_gap(lv):
        lo, hi = _edges(lv)
        return 0.0 if lo <= ref <= hi else min(abs(ref - lo), abs(ref - hi))

    near = [lv for lv in levels if edge_gap(lv) <= NEAR_ATR * atr]
    at = min(near, key=edge_gap) if near else None
    rest = [lv for lv in levels if lv is not at]
    over = [lv for lv in rest if _edges(lv)[0] > ref]
    under = [lv for lv in rest if _edges(lv)[1] < ref]
    above = min(over, key=lambda lv: _edges(lv)[0]) if over else None
    below = max(under, key=lambda lv: _edges(lv)[1]) if under else None
    return at, below, above


def explain(sr: dict, gap_rows: list[dict], lines: list[dict] | None = None) -> list[str]:
    """Short sentences for a beginner, in price order of what they talk about (top to bottom)."""
    ref = sr.get("reference_price")
    if not ref:
        return []
    out = []
    at, below, above = _zones_around(sr)

    def zone_txt(lv):
        lo, hi = (lv.get("zone") or [lv["price"], lv["price"]])[:2]
        kind = "저항" if lv["kind"] == "resistance" else "지지"
        return lo, hi, f"{TF_KO.get(lv.get('timeframe'), lv.get('timeframe') or '')} {kind} 구역({_fmt(lo)}~{_fmt(hi)})"

    if above:
        lo, hi, txt = zone_txt(above)
        out.append(f"위로는 {txt}까지 {_signed(_pct(lo, ref))} — 여기서 막혀 내려올 수 있어 롱이면 일부 익절을 고려하고, "
                   f"종가로 {_fmt(hi)} 위에 서면 돌파로 본다.")
    if at:
        lo, hi, txt = zone_txt(at)
        if at["kind"] == "resistance":
            out.append(f"지금가 {_fmt(ref)} 는 {txt}에 붙어 있다 — 돌파 확인(종가 {_fmt(hi)} 위) 전 추격 매수는 피한다.")
        else:
            out.append(f"지금가 {_fmt(ref)} 는 {txt}에 붙어 있다 — 롱이면 손절은 {_fmt(lo)} 아래, 종가로 {_fmt(lo)} 를 깨면 지지가 무너진 것으로 본다.")
    else:
        out.append(f"지금가 {_fmt(ref)} 는 지지 · 저항 구역 사이 — 어느 쪽에도 붙어 있지 않아 새로 들어가기엔 애매한 자리다.")
    if below:
        lo, hi, txt = zone_txt(below)
        out.append(f"아래로는 {txt}까지 {_signed(_pct(hi, ref))} — 눌림이 오면 다음 받침으로 이 구역에서 반등하는지 본다.")
    near_gap = next((g for g in gap_rows if abs(g["distance_pct"]) <= GAP_EXPLAIN_PCT), None)
    if near_gap:
        what = ("오르면 닿을 수 있는 위쪽 목표 후보" if near_gap["side"] == "above"
                else "눌림 때 내려올 수 있는 자리")
        out.append(f"{near_gap['text']}. 갭은 나중에 메워지는 일이 많지만 언제 메울지는 알 수 없어, {what}로만 본다.")
    for row in lines or []:
        out.append(row["text"])
    return out
