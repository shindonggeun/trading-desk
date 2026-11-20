"""Chart only (#631): one read of a ticker's chart across timeframes, in plain words, without an analysis run.

For each timeframe (5m · 15m · 1h · 4h · 1d by default) it gathers what the desk already draws on the
chart - the channels, short trendlines, their breaks and the converging pattern (desk.channels #611,
desk.patterns #629) - and adds:

- **trend**: up when the last finished close is above its 20-bar EMA, that above the 50-bar EMA and
  the 20-bar EMA rising over the last 5 bars; down the mirror; otherwise sideways (mixed);
- **levels**: the nearest lines above and below the price - channel and pattern lines, unbroken
  trendlines (and one freshly broken: the line just lost often stops the way back), the last swing
  highs / lows not yet closed through, the Fibonacci levels of the window's move and, on the daily,
  the open gaps (desk.line_basis, #577) - lines within a quarter ATR of each other merged, the three
  nearest each side kept and the next three in ``far``. Each carries ``refs``: what it is on the chart
  (``kind`` channel · trendline · pattern · swing · fib · gap, the line ``id`` the chart draws from
  /api/lines, the channel ``edge``) - the chart page numbers these lines and lights them up (#672);
- **volume**: the last finished bar's volume against the median of the 20 before (VOLUME_HOT and up);
- a plain sentence per timeframe, and where timeframes disagree (one up, another down) a sentence
  saying so.

Rule-based only: no model call, no quota. Arithmetic on price history - neither a forecast nor
a recommendation (``note``).
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime

from . import channels, trend

TIMEFRAMES = ("5m", "15m", "60m", "240m", "1d")
EMA_FAST, EMA_SLOW, SLOPE_BARS = 20, 50, 5
SWING_BARS = 120           # the swings a level may come from: this many bars back
MERGE_ATR = 0.25
PER_SIDE = 3
FAR_PER_SIDE = 3          # the next lines a side, for a folded "further away" list (#672)
VOLUME_BARS = 20
VOLUME_HOT = 2.0
NOTE = "차트 선과 숫자로 읽은 지금 모습이에요 — 예측도, 매수 · 매도 추천도 아니에요."
TREND_KO = {"up": "상승 흐름", "down": "하락 흐름", "sideways": "횡보"}
MAX_DISTANCE_ATR = 10.0    # a line further than this from the price is not "near"
PATTERN_STATE_KO = {"converging": "진행 중", "broke_up": "위로 돌파", "broke_down": "아래로 돌파"}


def _ema(values: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out = []
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


def trend_state(bars: list[dict]) -> dict:
    """The finished bars' trend by their 20 / 50-bar EMAs."""
    closes = [b["close"] for b in bars]
    if len(closes) < EMA_SLOW + SLOPE_BARS:
        return {"state": "sideways", "basis": "봉이 모자라요"}
    fast, slow = _ema(closes, EMA_FAST), _ema(closes, EMA_SLOW)
    rising = fast[-1] > fast[-1 - SLOPE_BARS]
    if closes[-1] > fast[-1] > slow[-1] and rising:
        state = "up"
    elif closes[-1] < fast[-1] < slow[-1] and not rising:
        state = "down"
    else:
        state = "sideways"
    return {"state": state, "ema20": channels._price(fast[-1]), "ema50": channels._price(slow[-1]),
            "basis": f"종가 · 20봉 평균 · 50봉 평균 순서{'' if state == 'sideways' else '가 ' + TREND_KO[state] + ' 쪽'}"}


def _candidates(frame: dict, bars: list[dict], tf: str, price: float,
                gaps: list[dict]) -> list[tuple[float, str, dict]]:
    """(price, name, ref) of every line the read may call near; ``ref`` says what it is on the chart."""
    out: list[tuple[float, str, dict]] = []
    for ch in (frame.get("channels") or [])[:1]:         # the best channel only: two would share line names
        word = {"up": "상승", "down": "하락", "flat": "횡보"}[ch["direction"]]
        for key, ko in (("upper", "상단"), ("lower", "하단"), ("mid", "가운데"), ("outer_upper", "바깥 상단"),
                        ("outer_lower", "바깥 하단")):
            out.append((ch[key]["end"]["price"], f"{word} 채널 {ko}",
                        {"kind": "channel", "id": ch.get("id"), "edge": key, "direction": ch["direction"]}))
    for t in frame.get("trendlines") or []:
        ref = {"kind": "trendline", "id": t.get("id"), "side": t["kind"]}
        if t["state"] != "broken":
            out.append((t["end"]["price"], "상승 추세선" if t["kind"] == "support" else "하락 추세선", ref))
        elif t.get("fresh_break"):                       # #672 the line just lost: the way back often stops there
            out.append((t["end"]["price"], f"방금 깨진 {'상승' if t['kind'] == 'support' else '하락'} 추세선",
                        {**ref, "broken": True}))
    for p in frame.get("patterns") or []:
        after = "" if p.get("state", "converging") == "converging" else "(돌파 후)"
        ref = {"kind": "pattern", "pattern": p.get("kind"), "state": p.get("state", "converging")}
        out.append((p["upper"]["to"]["price"], f"{p['label']} 윗선{after}", {**ref, "edge": "upper"}))
        out.append((p["lower"]["to"]["price"], f"{p['label']} 아랫선{after}", {**ref, "edge": "lower"}))
    k = channels.SWING_K[tf]
    recent = bars[-SWING_BARS:]
    closes = [b["close"] for b in recent]
    for i in trend._swing_idx(recent, k, "high"):
        if max(closes[i + 1:], default=0) <= recent[i]["high"]:   # not closed through since
            out.append((recent[i]["high"], "최근 고점", {"kind": "swing", "side": "high", "at": recent[i].get("at")}))
    for i in trend._swing_idx(recent, k, "low"):
        if min(closes[i + 1:], default=math.inf) >= recent[i]["low"]:
            out.append((recent[i]["low"], "최근 저점", {"kind": "swing", "side": "low", "at": recent[i].get("at")}))
    fib = trend.fibonacci(recent)
    for lv in (fib or {}).get("levels") or []:
        if 0 < lv["ratio"] < 1:
            out.append((lv["price"], f"피보나치 {lv['ratio']:g}", {"kind": "fib", "ratio": lv["ratio"]}))
    for g in gaps:
        out.append((g["fill_price"], f"{'상승' if g['direction'] == 'up' else '하락'} 갭 메움({g['formed_at']})",
                    {"kind": "gap", "direction": g["direction"], "formed_at": g["formed_at"]}))
    return [(p, n, r) for p, n, r in out if p and p > 0]


def levels(frame: dict, bars: list[dict], tf: str, price: float, atr: float, gaps: list[dict] = ()) -> dict:
    """The nearest lines above and below ``price`` (within MAX_DISTANCE_ATR): each side on its own,
    nearest first, a line within MERGE_ATR of the group's nearest one joining it; PER_SIDE a side and
    the next FAR_PER_SIDE in ``far``. A group's ``refs`` are its lines' chart refs, nearest first."""
    found = [x for x in _candidates(frame, bars, tf, price, list(gaps)) if abs(x[0] - price) <= MAX_DISTANCE_ATR * atr]
    out: dict = {"far": {}}
    for side, keep in (("above", lambda p: p > price), ("below", lambda p: p <= price)):
        groups: list[dict] = []
        for p, name, ref in sorted((x for x in found if keep(x[0])), key=lambda x: abs(x[0] - price)):
            if groups and abs(p - groups[-1]["price"]) <= MERGE_ATR * atr:
                if name not in groups[-1]["names"]:
                    groups[-1]["names"].append(name)
                groups[-1]["refs"].append(ref)          # same name, another line: the chart still lights it up
                continue
            groups.append({"price": p, "names": [name], "refs": [ref]})
        rows = [{"price": channels._price(g["price"]), "name": " · ".join(g["names"]), "refs": g["refs"],
                 "distance_pct": round((g["price"] / price - 1) * 100, 2)} for g in groups[:PER_SIDE + FAR_PER_SIDE]]
        out[side], out["far"][side] = rows[:PER_SIDE], rows[PER_SIDE:]
    return out


def volume(bars: list[dict]) -> dict | None:
    """The last finished bar's volume against the median of the VOLUME_BARS before it."""
    vols = [b.get("volume") or 0 for b in bars]
    base = [v for v in vols[-1 - VOLUME_BARS:-1] if v > 0]
    if len(base) < VOLUME_BARS // 2 or not vols[-1]:
        return None
    ratio = vols[-1] / statistics.median(base)
    return {"ratio": round(ratio, 2), "hot": ratio >= VOLUME_HOT}


def _fmt(p: float) -> str:
    return f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}"


def sentence(f: dict) -> str:
    """One plain sentence for a timeframe."""
    tf = channels.TF_KO.get(f["timeframe"], f["timeframe"])
    parts = [f"{tf} — {TREND_KO[f['trend']['state']]}"]
    ch = (f.get("channels") or [None])[0]
    if ch:
        where = {"inside": "안", "near_upper": "상단 근처", "near_lower": "하단 근처", "above": "위로 벗어남",
                 "below": "아래로 벗어남"}[ch["state"]]
        parts[0] += f", {({'up': '상승', 'down': '하락', 'flat': '횡보'})[ch['direction']]} 채널 {where}"
    text = parts[0] + "."
    up, down = f["levels"]["above"], f["levels"]["below"]
    if up:
        text += f" 위 {up[0]['name']} {_fmt(up[0]['price'])}({up[0]['distance_pct']:+.2f}%)"
    if down:
        text += f"{',' if up else ''} 아래 {down[0]['name']} {_fmt(down[0]['price'])}({down[0]['distance_pct']:+.2f}%)"
    if up or down:
        text += "."
    for p in f.get("patterns") or []:
        text += (f" {p['label']}{'수렴' if 'wedge' not in p['kind'] else ''} "
                 f"{PATTERN_STATE_KO.get(p.get('state', 'converging'), '진행 중')}.")
    t = fresh_break(f)
    if t:
        text += f" {'상승' if t['kind'] == 'support' else '하락'}추세선 이탈(방금)."
    v = f.get("volume")
    if v and v["hot"]:
        text += f" 거래량 평소의 {v['ratio']:.1f}배."
    return text


def fresh_break(f: dict) -> dict | None:
    """The timeframe's most recent trendline break still fresh (desk.channels' ``fresh_break``), or None."""
    lines = {t["id"]: t for t in f.get("trendlines") or []}
    for e in reversed(f.get("events") or []):
        t = lines.get(e["line_id"]) if e["kind"] == "trend_break" else None
        if t and t.get("fresh_break"):
            return t
    return None


def conflicts(frames: list[dict]) -> list[str]:
    """Sentences where timeframes disagree: a longer one up and a shorter one down (or the reverse),
    or a shorter one breaking a line against a longer one's channel."""
    out = []
    order = [f for f in frames if f.get("trend")]
    ups = [channels.TF_KO[f["timeframe"]] for f in order if f["trend"]["state"] == "up"]
    downs = [channels.TF_KO[f["timeframe"]] for f in order if f["trend"]["state"] == "down"]
    if ups and downs:
        out.append(f"{' · '.join(ups)}은 상승 흐름, {' · '.join(downs)}은 하락 흐름 — 시간봉끼리 엇갈려요.")
    told = set()
    for long_f in reversed(order):                     # the longest channel first, one sentence a shorter one
        if long_f["timeframe"] in told:
            continue
        ch = (long_f.get("channels") or [None])[0]
        if not ch or ch["direction"] == "flat" or ch.get("state") in ("above", "below"):
            continue                                   # no channel, or the price already out of it
        for short_f in order:
            if channels.MINUTES[short_f["timeframe"]] >= channels.MINUTES[long_f["timeframe"]]:
                continue
            t = fresh_break(short_f)
            if t and short_f["timeframe"] not in told:
                told.add(short_f["timeframe"])
                along = (t["kind"] == "support") == (ch["direction"] == "down")   # broke the long one's way
                out.append(f"{channels.TF_KO[long_f['timeframe']]}은 {'하락' if ch['direction'] == 'down' else '상승'} "
                           f"채널, {channels.TF_KO[short_f['timeframe']]}은 "
                           f"{'상승' if t['kind'] == 'support' else '하락'}추세 이탈 — "
                           f"{'짧은 봉도 큰 흐름 쪽으로 꺾였어요' if along else '짧은 봉이 큰 흐름과 반대로 움직였어요'}.")
    return list(dict.fromkeys(out))


def frame(bars: list[dict], tf: str, live: bool, rates: dict | None = None, gaps: list[dict] = ()) -> dict:
    """One timeframe read on its bars (oldest first; ``live``: the last one still trading)."""
    got = channels.lines(bars, tf, rates, live)
    usable = [b for b in bars if b["low"] > 0 and b["close"] > 0 and b["high"] >= b["low"]]   # as channels.lines
    done = usable[:-1] if live and len(usable) > 1 else usable
    price = got.get("reference_price") or (bars[-1]["close"] if bars else None)
    out = {"timeframe": tf, "as_of": got.get("as_of"), "price": price, "live_bar": live,
           "channels": got["channels"], "trendlines": got["trendlines"], "events": got["events"],
           "patterns": got.get("patterns") or [], "patterns_error": got.get("patterns_error"),
           "trend": trend_state(done),
           "levels": {"above": [], "below": [], "far": {"above": [], "below": []}}, "volume": volume(done)}
    if price and got.get("atr"):
        out["levels"] = levels(got, done, tf, price, got["atr"], gaps)
    out["summary"] = sentence(out)
    return out


def read(ticker: str, frames: list[dict]) -> dict:
    """The whole read: the timeframes, where they disagree, one summary."""
    disagree = conflicts(frames)
    summary = " ".join([f["summary"] for f in frames if f.get("summary")] + disagree + ["예측 · 추천 아님."])
    return {"ticker": ticker, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "timeframes": frames, "conflicts": disagree, "summary": summary, "note": NOTE}
