"""Daily trend-following evidence (#977): a first rebound, a test of resistance, a breakout or a failed one.

Read on the daily bars chart-read already holds (GET /api/chart-read, field ``trend_follow``) and on its 1d
frame's lines (desk.chart_read.frame: channels and trendlines). Arithmetic on finished bars, not a forecast and
not a recommendation.

- **Finished bars only.** The last bar counts once its session closed (plus the feed's lag); before that the
  read stands on the bar before it and says ``in_progress``. A swing is a pivot of ``SWING_K`` bars each side,
  usable from the bar after the one that confirmed it - nothing reads a bar ahead of the bar it judges.
- **Resistance.** The falling trendlines and a falling channel's upper edge of the frame (from the bar after
  their second anchor was confirmed), and every swing high not yet closed above. A close more than
  ``BREAK_ATR`` ATRs beyond a line breaks it; a close back under it by as much is a failed breakout.
- **Stage.** ``breakout_confirmed`` / ``failed_breakout`` (the latest break in ``LOOKBACK`` bars and what
  followed), else ``awaiting_confirmation`` when the last bar's high reached the nearest resistance and closed
  under it (``wick_touch``), else ``first_rebound`` when the last bar closed back over a support it had closed
  under (``support_reclaim``) or is the first strong up bar after a decline (``first_up_bar``), else
  ``awaiting_confirmation`` when such a rebound came within ``REBOUND_BARS``, else ``none``.
- **Evidence.** The 25% lines of the swing range (linear), the 25% lines of the frame's best channel (log - the
  channels are log lines), the 1.414 / 1.618 / 2.618 extensions of the last up wave (linear, the log ones in
  ``meta``), lower lows with higher RSI lows on confirmed swing lows, the stochastic's climb from its floor to
  its ceiling, resistance over a thick volume node, and (KOSPI, SK hynix, Samsung Electronics) which of them
  broke out first on the same rule. A drawing's source values are never hard-coded: a missing input is
  ``insufficient``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import channels, chart_read, indicators, trend, volume_profile
from . import market_calendar as calendar

MIN_BARS = 60              # finished daily bars a read needs
SWING_K = channels.SWING_K["1d"]
SWING_BARS = chart_read.SWING_BARS
BREAK_ATR = trend.BREAK_ATR          # a close this far beyond a line breaks it (and back under it, fails it)
TOUCH_ATR = 0.25           # a high this close under a line touches it
LOOKBACK = 20              # bars a break and its failure are looked for in
REBOUND_BARS = 10          # a rebound this recent keeps the read waiting for the breakout
RECLAIM_BARS = 3           # a close under support this recent, then one back over it: a reclaim
DECLINE_BARS = 20          # a first rebound follows a fall from the highest close this many bars back ...
DECLINE_ATR = 3.0          # ... of at least this many ATRs
STRONG_BODY_ATR = 0.6      # a strong up bar: a body of 0.6 ATR or more ...
STRONG_CLOSE = 0.7         # ... closing in the top 30% of its range
MAX_DISTANCE_ATR = chart_read.MAX_DISTANCE_ATR
RANGE_BARS = 60            # the swing range the 25% lines split
QUARTER = 0.25
EXTENSIONS = (1.414, 1.618, 2.618)
DIV_BARS = 120             # swing lows looked at for a divergence ...
DIV_PIVOTS = 4             # ... the last this many
STOCH = {"k": indicators.STOCH_K, "smooth": indicators.STOCH_SMOOTH, "d": indicators.STOCH_D}   # the chart's own
STOCH_FLOOR, STOCH_CEILING = 20.0, 80.0
STOCH_LOOK = 60            # bars the floor is looked for in
STOCH_MAX_BARS = 15        # floor to ceiling within this many bars ...
STOCH_PULLBACK = 15.0      # ... dipping at most this far from its high on the way
PROFILE_BARS = 120
OVERLAP_PCT = 1.5          # resistance within this of a thick volume node overlaps it
LAG_MINUTES = {"kr": 20, "us": 15, "cme": 10}
KR_LEAD = ("^KS11", "000660.KS", "005930.KS")     # benchmark first
LEAD_CONDITION = f"직전 스윙 고점 위 일봉 종가 마감(0.3 ATR 넘게, 최근 {LOOKBACK}봉)"
UNSUPPORTED = frozenset({"SOXLUSDT", "SOXLUSDT.P", "SOXL-USDT", "NAS100", "NAS100.CFD", "NQ1!"})
ETFS = frozenset({"SOXX", "SOXL", "SOXS", "SMH", "KORU", "QQQ", "TQQQ", "SQQQ", "SPY", "TECL", "UPRO"})
NOTE = "지난 일봉으로 계산한 관찰 근거예요 — 예측도, 매수 · 매도 추천도 아니에요."


# ---- the instrument and its finished bars ---------------------------------------------------------------------

def _market(ticker: str) -> str | None:
    t = ticker.upper()
    if t.endswith("=F"):
        return "cme"
    if t.endswith((".KS", ".KQ")) or t.startswith(("^KS", "^KQ")):
        return "kr"
    if t.endswith(("-USD", "=X")):
        return None                                  # trades round the clock: no session close
    return "us"


def instrument(ticker: str, provider: str | None) -> dict:
    """What the bars are of: kind, unit, provider, session and roll - SOXX, SOXL, a Binance perpetual and
    NQ=F against TradingView's NQ1! are different products, never one another's stand-in."""
    t, market = ticker.upper(), _market(ticker)
    rollover = note = None
    if market == "cme":
        kind, unit, session = "continuous_future", "index_points", "cme_globex"
        rollover = "yahoo_front_month"
        note = "야후 연속 선물이에요 — TradingView NQ1! 과 공급자 · 롤오버 시점이 달라 같은 날 값이 다를 수 있어요."
    elif market == "kr":
        index = t.startswith("^")
        kind, unit, session = ("cash_index", "index_points", "krx_regular") if index else ("stock", "KRW", "krx_regular")
    elif market is None:
        kind, unit, session = "spot", "USD", "24h"
    elif t.startswith("^"):
        kind, unit, session = "cash_index", "index_points", "us_regular"
    else:
        kind, unit, session = ("etf" if t in ETFS else "stock"), "USD", "us_regular"
    return {"kind": kind, "unit": unit, "symbol": t, "provider": provider, "session": session,
            "rollover": rollover, "note": note}


def _when(at: object) -> datetime | None:
    if not isinstance(at, str):
        return None
    try:
        stamp = datetime.fromisoformat(at)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def final(ticker: str, bar: dict, now: datetime) -> bool:
    """Whether a daily bar's session has closed (and the feed caught up) by ``now``."""
    at = _when(bar.get("at"))
    if at is None:
        return False
    market = _market(ticker)
    if market is None:
        return at + timedelta(days=1) <= now
    day = calendar.bar_day(bar["at"], market == "kr")
    if market == "cme":                               # the session dated D ends 17:00 New York on D
        close = datetime.combine(day, calendar.CME_CLOSES, calendar.ET)
    else:
        close = calendar.session_close(market, day)
    return now >= close + timedelta(minutes=LAG_MINUTES[market])


def _usable(bars: list) -> list[dict]:
    out = []
    for b in bars:
        if not isinstance(b, dict) or _when(b.get("at")) is None:
            continue
        vals = [b.get(k) for k in ("open", "high", "low", "close")]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in vals) \
                and b["high"] >= max(b["open"], b["close"]) and b["low"] <= min(b["open"], b["close"]):
            out.append(b)
    return out


def finished(ticker: str, bars: list, now: datetime) -> tuple[list[dict], bool]:
    """(the finished bars, whether the last bar given was left out as still trading)."""
    rows = _usable(bars)
    if rows and not final(ticker, rows[-1], now):
        return rows[:-1], True
    return rows, False


# ---- lines over bar indexes -------------------------------------------------------------------------------------

@dataclass
class Line:
    """A price line over bar indexes, usable from ``start`` (the bar after the one confirming it)."""
    name: str
    side: str                     # resistance | support
    ref: dict
    start: int
    value: Callable[[int], float]
    falling: bool = False


def _pivots(bars: list[dict], field: str) -> list[int]:
    # a swing at i needs bars i-k .. i+k only: the same on any later bar list, so confirmed at i+k
    return trend._swing_idx(bars, SWING_K, field)


def _swing_lines(bars: list[dict], field: str) -> list[Line]:
    side = "resistance" if field == "high" else "support"
    first = max(0, len(bars) - SWING_BARS)
    out = []
    for i in _pivots(bars, field):
        if i < first:
            continue
        price = bars[i][field]
        out.append(Line("직전 스윙 고점" if field == "high" else "직전 스윙 저점", side,
                        {"kind": "swing", "side": field, "at": bars[i]["at"]}, i + SWING_K + 1,
                        lambda x, p=price: p))
    return out


def _log_line(i0: int, p0: float, i1: int, p1: float) -> Callable[[int], float]:
    slope = (math.log(p1) - math.log(p0)) / (i1 - i0)
    return lambda x: math.exp(math.log(p0) + slope * (x - i0))


def _channel_pivots(ch: dict) -> tuple[str, str] | None:
    """The two anchors' stamps from a channel id (``1d-channel-<at i>-<at j>``, both stamps one format)."""
    rest = str(ch.get("id") or "").removeprefix("1d-channel-")
    if len(rest) % 2 != 1 or rest[len(rest) // 2] != "-":
        return None
    return rest[:len(rest) // 2], rest[len(rest) // 2 + 1:]


def _frame_lines(frame: dict | None, index: dict[str, int]) -> list[Line]:
    """The frame's trendlines, and a falling channel's edges, as lines over our own bar indexes."""
    out: list[Line] = []
    for t in (frame or {}).get("trendlines") or []:
        anchors = t.get("anchors") or []
        if len(anchors) != 2 or any(a.get("at") not in index for a in anchors):
            continue
        (i0, i1) = (index[anchors[0]["at"]], index[anchors[1]["at"]])
        if i1 <= i0:
            continue
        side = t.get("kind")
        falling = side == "resistance"
        out.append(Line("하락 추세선" if falling else "상승 추세선", side,
                        {"kind": "trendline", "id": t.get("id"), "side": side}, i1 + SWING_K + 1,
                        _log_line(i0, anchors[0]["price"], i1, anchors[1]["price"]), falling))
    for ch in ((frame or {}).get("channels") or [])[:1]:
        fn = _channel_fn(ch, index)
        pivots = _channel_pivots(ch)
        if fn is None or pivots is None or pivots[1] not in index:
            continue
        start = index[pivots[1]] + SWING_K + 1
        word = {"up": "상승", "down": "하락", "flat": "횡보"}.get(ch.get("direction"), "")
        for edge, side in (("upper", "resistance"), ("lower", "support")):
            out.append(Line(f"{word} 채널 {'상단' if edge == 'upper' else '하단'}", side,
                            {"kind": "channel", "id": ch.get("id"), "edge": edge}, start,
                            fn[edge], ch.get("direction") == "down"))
    return out


def _channel_fn(ch: dict, index: dict[str, int]) -> dict[str, Callable[[int], float]] | None:
    try:
        up, low = ch["upper"], ch["lower"]
        i0, i1 = index[up["start"]["at"]], index[up["end"]["at"]]
    except (KeyError, TypeError):
        return None
    if i1 <= i0:
        return None
    return {"upper": _log_line(i0, up["start"]["price"], i1, up["end"]["price"]),
            "lower": _log_line(i0, low["start"]["price"], i1, low["end"]["price"])}


def _atr(bars: list[dict]) -> float | None:
    return channels.atr(bars)


# ---- the stage ---------------------------------------------------------------------------------------------------

def _events(line: Line, bars: list[dict], tol: float, first: int) -> list[tuple[str, int]]:
    """(break | fail, index) of a resistance line from bar ``first`` on: a close above it after one at or
    under it, then a close back under it."""
    s = max(line.start, 1)
    if s >= len(bars) or bars[s - 1]["close"] > line.value(s - 1) + tol:
        return []                                       # already through it when it became a line
    out: list[tuple[str, int]] = []
    above = False
    for x in range(s, len(bars)):
        level = line.value(x)
        close = bars[x]["close"]
        if not above and close > level + tol:
            above = True
            if x >= first:
                out.append(("break", x))
        elif above and close < level - tol:
            above = False
            if x >= first and out:
                out.append(("fail", x))
    return out


def _strong_up(bar: dict, atr: float) -> bool:
    span = bar["high"] - bar["low"]
    return (bar["close"] > bar["open"] and bar["close"] - bar["open"] >= STRONG_BODY_ATR * atr
            and span > 0 and (bar["close"] - bar["low"]) / span >= STRONG_CLOSE)


def _first_up(bars: list[dict], x: int, atr: float) -> bool:
    """A strong up bar at ``x`` after a fall of DECLINE_ATR, the first one since the fall's low."""
    if x < DECLINE_BARS or not _strong_up(bars[x], atr):
        return False
    window = bars[x - DECLINE_BARS:x]
    peak = max(b["close"] for b in window)
    low_at = min(range(x - DECLINE_BARS, x + 1), key=lambda i: bars[i]["low"])
    if peak - bars[low_at]["low"] < DECLINE_ATR * atr:
        return False
    return not any(_strong_up(bars[i], atr) for i in range(low_at, x))


def _support_at(lines: list[Line], bars: list[dict], x: int, tol: float) -> Line | None:
    """The nearest support under bar ``x``'s previous close, as known at ``x`` (not closed under before)."""
    best = None
    prev = bars[x - 1]["close"]
    for ln in lines:
        if ln.side != "support" or x < ln.start:
            continue
        if ln.ref.get("kind") == "swing" and any(
                bars[i]["close"] < ln.value(i) - tol for i in range(ln.start - SWING_K, x)):
            continue                                    # a swing low already closed under is spent
        level = ln.value(x)
        if level <= prev and (best is None or level > best[0]):
            best = (level, ln)
    return best[1] if best else None


def _reclaim(lines: list[Line], bars: list[dict], x: int, tol: float) -> tuple[Line, float] | None:
    """Bar ``x`` closed back over a support one of the last RECLAIM_BARS bars closed under."""
    for d in range(max(1, x - RECLAIM_BARS), x):
        ln = _support_at(lines, bars, d, tol)
        if ln is None:
            continue
        level = ln.value(d)
        if bars[d]["close"] < level - tol and all(bars[i]["close"] <= ln.value(i) for i in range(d, x)) \
                and bars[x]["close"] > ln.value(x):
            return ln, ln.value(x)
    return None


def _resistance_now(lines: list[Line], bars: list[dict], x: int, tol: float) -> Line | None:
    """The nearest resistance over bar ``x``'s close not yet closed through."""
    close, best = bars[x]["close"], None
    for ln in lines:
        if ln.side != "resistance" or x < ln.start:
            continue
        if any(bars[i]["close"] > ln.value(i) + tol for i in range(ln.start, x + 1)):
            continue
        level = ln.value(x)
        if level >= close and (best is None or level < best[0]):
            best = (level, ln)
    return best[1] if best else None


def _support_now(lines: list[Line], bars: list[dict], x: int, tol: float) -> Line | None:
    close, best = bars[x]["close"], None
    for ln in lines:
        if ln.side != "support" or x < ln.start:
            continue
        if any(bars[i]["close"] < ln.value(i) - tol for i in range(ln.start, x + 1)):
            continue
        level = ln.value(x)
        if level <= close and (best is None or level > best[0]):
            best = (level, ln)
    return best[1] if best else None


def _latest_break(lines: list[Line], bars: list[dict], tol: float) -> tuple[Line, int, int | None] | None:
    """(line, break index, fail index or None): the most recent break of a resistance in LOOKBACK bars."""
    first = len(bars) - LOOKBACK
    best = None
    for ln in lines:
        if ln.side != "resistance":
            continue
        evs = _events(ln, bars, tol, first)
        brk = [x for kind, x in evs if kind == "break"]
        if not brk:
            continue
        b = brk[-1]
        fail = next((x for kind, x in evs if kind == "fail" and x > b), None)
        key = (max(b, fail or -1), ln.value(b))
        if best is None or key > best[0]:
            best = (key, (ln, b, fail))
    return best[1] if best else None


def _stage(lines: list[Line], bars: list[dict], atr: float) -> dict:
    """The stage at the last bar and what it rests on (line objects kept for the key prices)."""
    n, tol = len(bars), BREAK_ATR * atr
    t = n - 1
    last = bars[t]
    out: dict = {"state": "none", "signal": None, "at": None, "line": None, "level": None, "break_at": None}
    got = _latest_break(lines, bars, tol)
    if got is not None:
        ln, b, fail = got
        if fail is not None:
            return {**out, "state": "failed_breakout", "signal": "close_back_below", "at": fail, "line": ln,
                    "level": ln.value(t), "break_at": b}
        return {**out, "state": "breakout_confirmed", "signal": "close_above", "at": b, "line": ln,
                "level": ln.value(t), "break_at": b}
    res = _resistance_now(lines, bars, t, tol)
    if res is not None and last["high"] >= res.value(t) - TOUCH_ATR * atr:
        return {**out, "state": "awaiting_confirmation", "signal": "wick_touch", "at": t, "line": res,
                "level": res.value(t)}
    rebound = None
    for x in range(t, max(DECLINE_BARS, t - REBOUND_BARS) - 1, -1):
        rec = _reclaim(lines, bars, x, tol)
        if rec is not None:
            rebound = (x, "support_reclaim", rec[0], rec[1])
            break
        if _first_up(bars, x, atr):
            rebound = (x, "first_up_bar", None, None)
            break
    if rebound is None:
        return out
    x, signal, ln, level = rebound
    if x == t:
        return {**out, "state": "first_rebound", "signal": signal, "at": t, "line": ln, "level": level}
    return {**out, "state": "awaiting_confirmation", "signal": None, "at": x, "line": ln,
            "level": ln.value(t) if ln is not None else None, "rebound_signal": signal}


# ---- evidence ----------------------------------------------------------------------------------------------------

def _p(x: float) -> float:
    return channels._price(x)


def _pt(bars: list[dict], i: int, field: str) -> dict:
    return {"at": bars[i]["at"], "price": _p(bars[i][field])}


def _item(kind: str, status: str, label: str, detail: str, prices=(), anchors=(), confirmed_at=None,
          meta=None) -> dict:
    return {"id": f"1d-{kind}", "kind": kind, "status": status, "label": label, "detail": detail,
            "prices": [_p(p) for p in prices], "anchors": list(anchors), "confirmed_at": confirmed_at,
            "meta": {k: v for k, v in (meta or {}).items() if v is not None}}


def extension(low: float, high: float, ratio: float, scale: str) -> float:
    """The ``ratio`` extension of the wave ``low`` -> ``high``: linear on prices, log on their logs."""
    if scale == "log":
        return math.exp(math.log(low) + ratio * (math.log(high) - math.log(low)))
    return low + ratio * (high - low)


def _range_pivots(bars: list[dict]) -> tuple[int, int] | None:
    first = max(0, len(bars) - RANGE_BARS)
    highs = [i for i in _pivots(bars, "high") if i >= first]
    lows = [i for i in _pivots(bars, "low") if i >= first]
    if not highs or not lows:
        return None
    return max(highs, key=lambda i: bars[i]["high"]), min(lows, key=lambda i: bars[i]["low"])


def quarter_level(bars: list[dict], tol_pct: float) -> dict:
    got = _range_pivots(bars)
    label = "수평 구간 25% 되돌림"
    if got is None:
        return _item("quarter_level", "insufficient", label, f"최근 {RANGE_BARS}봉에 확정된 스윙 고점 · 저점이 없어요.")
    h, lo = got
    high, low = bars[h]["high"], bars[lo]["low"]
    upper, lower = high - QUARTER * (high - low), low + QUARTER * (high - low)
    close = bars[-1]["close"]
    where = ("위 25% 선 위" if close > upper else "아래 25% 선 아래" if close < lower else "두 25% 선 사이")
    confirmed = bars[min(len(bars) - 1, max(h, lo) + SWING_K)]["at"]
    return _item("quarter_level", "confirmed", label,
                 f"스윙 고점 {_p(high):,} · 저점 {_p(low):,} 구간의 25% 선 {_p(upper):,} · {_p(lower):,} — 종가는 {where}.",
                 (upper, lower), (_pt(bars, h, "high"), _pt(bars, lo, "low")), confirmed,
                 {"ratio": QUARTER, "high": _p(high), "low": _p(low), "scale": "linear", "tolerance_pct": tol_pct})


def channel_quarter(frame: dict | None, bars: list[dict], index: dict[str, int]) -> dict:
    label = "채널 폭 25% 보조선"
    ch = next(iter((frame or {}).get("channels") or []), None)
    fn = _channel_fn(ch, index) if ch else None
    pivots = _channel_pivots(ch) if ch else None
    if fn is None or pivots is None or any(p not in index for p in pivots):
        return _item("channel_quarter", "insufficient", label, "두 피벗으로 그은 일봉 채널이 없어요.")
    t = len(bars) - 1
    up, low = math.log(fn["upper"](t)), math.log(fn["lower"](t))
    width = up - low
    lines = [math.exp(up), math.exp(up - QUARTER * width), math.exp(low + QUARTER * width), math.exp(low)]
    base = fn["upper"] if ch.get("base") == "upper" else fn["lower"]
    i, j = index[pivots[0]], index[pivots[1]]
    anchors = [{"at": pivots[0], "price": _p(base(i))}, {"at": pivots[1], "price": _p(base(j))}]
    confirmed = bars[min(t, j + SWING_K)]["at"]
    return _item("channel_quarter", "confirmed", label,
                 f"{'위' if ch.get('base') == 'upper' else '아래'} 두 피벗을 잇고 폭 {ch.get('width_pct')}% 만큼 평행 이동한 채널의 "
                 f"25% 선 {_p(lines[1]):,} · {_p(lines[2]):,} (로그 눈금).",
                 lines, anchors, confirmed,
                 {"ratio": QUARTER, "scale": "log", "channel_id": ch.get("id"), "width_pct": ch.get("width_pct")})


def fib_extension(bars: list[dict]) -> dict:
    label = "기준 파동 확장"
    highs, lows = _pivots(bars, "high"), _pivots(bars, "low")
    if not highs:
        return _item("fib_extension", "insufficient", label, "확정된 스윙 고점이 없어요.")
    h = highs[-1]
    before = [i for i in lows if i < h]
    if not before or bars[before[-1]]["low"] >= bars[h]["high"]:
        return _item("fib_extension", "insufficient", label, "마지막 스윙 고점 앞에 그보다 낮은 스윙 저점이 없어요.")
    lo = before[-1]
    low, high = bars[lo]["low"], bars[h]["high"]
    levels = [extension(low, high, r, "linear") for r in EXTENSIONS]
    logs = [extension(low, high, r, "log") for r in EXTENSIONS]
    return _item("fib_extension", "confirmed", label,
                 f"{_p(low):,} → {_p(high):,} 상승 파동의 1.414 · 1.618 · 2.618 확장 "
                 + " · ".join(f"{_p(v):,}" for v in levels) + " (선형).",
                 levels, (_pt(bars, lo, "low"), _pt(bars, h, "high")), bars[min(len(bars) - 1, h + SWING_K)]["at"],
                 {"ratios": ",".join(f"{r:g}" for r in EXTENSIONS), "scale": "linear", "wave_low": _p(low),
                  "wave_high": _p(high), "log_prices": ",".join(f"{_p(v)}" for v in logs)})


def divergence(bars: list[dict]) -> dict:
    label = "가격 저점 ↓ · RSI 저점 ↑"
    rsis = indicators.rsi([b["close"] for b in bars])
    first = max(0, len(bars) - DIV_BARS)
    piv: list[int] = []
    for i in _pivots(bars, "low"):
        if i < first or rsis[i] is None:
            continue
        if piv and i - piv[-1] <= SWING_K:              # one trough read twice (equal lows side by side)
            if bars[i]["low"] < bars[piv[-1]]["low"]:
                piv[-1] = i
            continue
        piv.append(i)
    piv = piv[-DIV_PIVOTS:]
    meta = {"indicator": f"rsi{indicators.RSI_PERIOD}", "pivot_k": SWING_K}
    if len(piv) < 2:
        return _item("divergence", "insufficient", label, "비교할 확정 스윙 저점이 둘 미만이에요.", meta={**meta, "comparisons": 0})
    count = 0
    for a, b in zip(reversed(piv[:-1]), reversed(piv[1:]), strict=True):
        if bars[b]["low"] < bars[a]["low"] and rsis[b] > rsis[a]:
            count += 1
        else:
            break
    used = piv[-(count + 1):] if count else piv[-2:]
    anchors = [{**_pt(bars, i, "low"), "rsi": round(rsis[i], 1)} for i in used]
    confirmed = bars[min(len(bars) - 1, used[-1] + SWING_K)]["at"]
    if count:
        detail = f"확정 스윙 저점 {count + 1}개가 가격은 낮아지고 RSI 는 높아졌어요(연속 {count}번)."
    else:
        detail = "마지막 두 확정 스윙 저점에서 가격은 낮아지고 RSI 는 높아지는 모습이 없어요."
    return _item("divergence", "confirmed" if count else "pending", label, detail, (), anchors,
                 confirmed if count else None, {**meta, "comparisons": count})


def stochastic(bars: list[dict], k: int = STOCH["k"], smooth: int = STOCH["smooth"], d: int = STOCH["d"]) -> dict:
    """The %K's climb from its floor (≤ STOCH_FLOOR) to its ceiling (≥ STOCH_CEILING) on finished bars."""
    label = f"스토캐스틱 {k}-{smooth}-{d} 바닥 → 80"
    pk, _ = indicators.stochastic([b["high"] for b in bars], [b["low"] for b in bars], [b["close"] for b in bars],
                                  k, smooth, d)
    meta = {"k": k, "smooth": smooth, "d": d, "floor": STOCH_FLOOR, "ceiling": STOCH_CEILING,
            "max_bars": STOCH_MAX_BARS, "pullback_allowed": STOCH_PULLBACK}
    first = max(0, len(bars) - STOCH_LOOK)
    floors = [i for i in range(first, len(bars)) if pk[i] is not None and pk[i] <= STOCH_FLOOR]
    if not floors:
        return _item("stochastic", "insufficient", label, f"최근 {STOCH_LOOK}봉에 %K 가 {STOCH_FLOOR:g} 이하로 간 적이 없어요.",
                     meta=meta)
    b = floors[-1]
    top, pull, e = pk[b], 0.0, None
    for i in range(b + 1, len(bars)):
        if pk[i] is None:
            continue
        top = max(top, pk[i])
        pull = max(pull, top - pk[i])
        if pk[i] >= STOCH_CEILING:
            e = i
            break
    anchors = [{"at": bars[b]["at"], "price": _p(bars[b]["close"]), "k": pk[b]}]
    if e is None:
        return _item("stochastic", "pending", label,
                     f"%K 가 {bars[b]['at'][:10]} 바닥 뒤 {len(bars) - 1 - b}봉째 {pk[-1]} — 아직 80 전이에요.",
                     (), anchors, None, {**meta, "pullback_max": round(pull, 2)})
    anchors.append({"at": bars[e]["at"], "price": _p(bars[e]["close"]), "k": pk[e]})
    took = e - b
    ok = took <= STOCH_MAX_BARS and pull <= STOCH_PULLBACK
    why = "" if ok else (f" — {STOCH_MAX_BARS}봉보다 오래 걸렸어요" if took > STOCH_MAX_BARS
                         else f" — 오는 길에 {pull:.1f} 되밀렸어요")
    return _item("stochastic", "confirmed" if ok else "pending", label,
                 f"%K 가 바닥에서 {took}봉 만에 80 이상에 닿았어요(되밀림 최대 {pull:.1f}){why}.",
                 (), anchors, bars[e]["at"] if ok else None, {**meta, "bars_to_80": took, "pullback_max": round(pull, 2)})


def overlap(bars: list[dict], resistance: tuple[float, Line] | None, atr: float) -> dict:
    label = "하락 저항 · 매물 구역 중첩"
    meta = {"tolerance_pct": OVERLAP_PCT}
    if resistance is None:
        return _item("overlap", "insufficient", label, "위에 남은 저항선이 없어요.", meta=meta)
    level, ln = resistance
    vp = volume_profile.profile(bars[-PROFILE_BARS:], atr, "1d")
    nodes = [n["price"] for n in (vp or {}).get("nodes") or [] if n["kind"] == "thick"]
    if vp and vp.get("poc"):
        nodes.append(vp["poc"])
    if not nodes:
        return _item("overlap", "insufficient", label, "거래량이 없어 매물 구역을 계산할 수 없어요.", (level,), meta=meta)
    node = min(nodes, key=lambda p: abs(p - level))
    dist = round((node / level - 1) * 100, 2)
    close_dist = round((level / bars[-1]["close"] - 1) * 100, 2)
    hit = abs(dist) <= OVERLAP_PCT
    detail = (f"{ln.name} {_p(level):,} 와 매물 구역 {node:,} 이 {abs(dist)}% 안에서 겹쳐요."
              if hit else f"{ln.name} {_p(level):,} 와 가장 가까운 매물 구역 {node:,} 은 {abs(dist)}% 떨어져 있어요.")
    return _item("overlap", "confirmed" if hit else "pending", label, detail + f" 종가에서 {close_dist:+}%.",
                 (level, node), (), bars[-1]["at"] if hit else None,
                 {**meta, "distance_pct": dist, "close_distance_pct": close_dist, "falling": "yes" if ln.falling else "no"})


# ---- lead (KOSPI, SK hynix, Samsung Electronics) -------------------------------------------------------------

def _break_day(ticker: str, bars: list[dict]):
    """The day of the latest break of a swing high in LOOKBACK bars (the lead rule: swing highs only, so the
    three are read alike), and the day of the last bar."""
    atr = _atr(bars)
    if len(bars) < MIN_BARS or not atr:
        return None
    got = _latest_break(_swing_lines(bars, "high"), bars, BREAK_ATR * atr)
    day = calendar.bar_day(bars[-1]["at"], True)
    return (calendar.bar_day(bars[got[1]]["at"], True) if got else None), day, (bars[got[1]]["at"] if got else None)


def lead(ticker: str, own: list[dict], peers: dict[str, list[dict] | None], now: datetime) -> dict | None:
    if ticker not in KR_LEAD:
        return None
    rows = {ticker: own}
    for t in KR_LEAD:
        if t != ticker:
            peer = peers.get(t)
            rows[t] = finished(t, peer, now)[0] if peer else None
    reads = {t: (_break_day(t, b) if b else None) for t, b in rows.items()}
    bench = reads.get(KR_LEAD[0])
    items = []
    for t in KR_LEAD[1:]:
        mine = reads.get(t)
        if bench is None or mine is None or mine[1] != bench[1]:
            items.append({"ticker": t, "state": "not_comparable", "at": mine[2] if mine else None})
            continue
        if mine[0] is None:
            state = "waiting"
        elif bench[0] is None or mine[0] < bench[0]:
            state = "led"
        else:
            state = "same" if mine[0] == bench[0] else "lagged"
        items.append({"ticker": t, "state": state, "at": mine[2]})
    return {"benchmark": KR_LEAD[0], "benchmark_at": bench[2] if bench else None, "condition": LEAD_CONDITION,
            "items": items}


def _lead_item(got: dict) -> dict:
    states = {i["ticker"]: i["state"] for i in got["items"]}
    comparable = [t for t, s in states.items() if s != "not_comparable"]
    ko = {"led": "코스피보다 먼저", "same": "코스피와 같은 날", "lagged": "코스피보다 늦게", "waiting": "아직 돌파 전"}
    names = {"000660.KS": "SK하이닉스", "005930.KS": "삼성전자"}
    if not comparable:
        return _item("lead", "insufficient", "선행 돌파 비교", "같은 거래일의 확정 일봉이 없어 비교할 수 없어요.")
    detail = " · ".join(f"{names[t]} {ko[states[t]]}" for t in comparable) + f" ({got['condition']}). 돌파 시점 비교예요."
    return _item("lead", "confirmed", "선행 돌파 비교", detail, (), (), None,     # the times are in ``lead``
                 {"benchmark": got["benchmark"], "compared": len(comparable)})


# ---- the read ----------------------------------------------------------------------------------------------------

def _utc(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="seconds")


def unavailable(ticker: str, now: datetime, reason: str, provider: str | None = None) -> dict:
    t = ticker.upper()
    return {"ticker": t, "timeframe": "1d", "status": "unavailable", "reason": reason,
            "instrument": None if t in UNSUPPORTED else instrument(t, provider),
            "observed_at": _utc(now), "as_of": None, "last_bar": None, "trend": None, "stage": None,
            "key_prices": {"hold": None, "break": None}, "conditions": [], "evidence": [], "lead": None,
            "note": NOTE}


_STAGE_KO = {"first_rebound": "첫 반등", "awaiting_confirmation": "확인 대기", "breakout_confirmed": "돌파 확인",
             "failed_breakout": "재이탈", "none": "해당 없음"}
_SIGNAL_KO = {"first_up_bar": "하락 뒤 첫 강한 양봉", "support_reclaim": "지지 아래 마감 뒤 회복",
              "wick_touch": "저항에 꼬리만 닿고 아래 마감", "close_above": "저항 위 종가 마감",
              "close_back_below": "돌파한 선 아래로 다시 마감"}


def _key(level: float | None, ln: Line | None, close: float, label: str | None = None) -> dict | None:
    if level is None or ln is None:
        return None
    return {"price": _p(level), "label": label or ln.name, "distance_pct": round((level / close - 1) * 100, 2),
            "ref": ln.ref}


def build(ticker: str, daily: dict | None, now: datetime, frame: dict | None = None,
          peers: dict[str, list[dict] | None] | None = None) -> dict:
    """The trend-following read of ``ticker``'s daily bars (``daily``: desk.prices' candles answer) at ``now``.
    ``frame``: chart-read's 1d frame (its trendlines and channels); ``peers``: the other KOSPI-lead tickers'
    daily bars."""
    t = ticker.upper()
    if t in UNSUPPORTED:
        return unavailable(t, now, "product_mismatch")
    if not isinstance(daily, dict) or not isinstance(daily.get("bars"), list) or not daily["bars"]:
        return unavailable(t, now, "missing_daily_source")
    provider = daily.get("source")
    bars, pending = finished(t, daily["bars"][-channels.BARS["1d"]:], now)
    atr = _atr(bars) if len(bars) >= MIN_BARS else None
    if not atr:
        return unavailable(t, now, "insufficient_bars", provider)
    index = {b["at"]: i for i, b in enumerate(bars)}
    lines = _frame_lines(frame, index) + _swing_lines(bars, "high") + _swing_lines(bars, "low")
    tol = BREAK_ATR * atr
    last, n = bars[-1], len(bars)
    close = last["close"]
    stage = _stage(lines, bars, atr)
    state = stage["state"]

    res = _resistance_now(lines, bars, n - 1, tol)
    sup = _support_now(lines, bars, n - 1, tol)
    res_level = res.value(n - 1) if res is not None else None
    sup_level = sup.value(n - 1) if sup is not None else None
    if res_level is None or res_level - close > MAX_DISTANCE_ATR * atr:
        res, res_level = None, None
    if sup_level is None or close - sup_level > MAX_DISTANCE_ATR * atr:
        sup, sup_level = None, None
    if state == "breakout_confirmed":
        hold = _key(stage["level"], stage["line"], close, f"돌파한 {stage['line'].name}")
        brk = _key(res_level, res, close)
    elif state == "failed_breakout":
        hold = _key(sup_level, sup, close)
        brk = _key(stage["level"], stage["line"], close, f"다시 넘어야 할 {stage['line'].name}")
    else:
        if state in ("first_rebound", "awaiting_confirmation") and stage.get("line") is not None \
                and stage["line"].side == "support":
            hold = _key(stage["level"], stage["line"], close, f"회복한 {stage['line'].name}")
        else:
            hold = _key(sup_level, sup, close)
        brk = _key(res_level, res, close)

    stoch = stochastic(bars)
    div = divergence(bars)
    conditions = []
    if state == "breakout_confirmed":
        after = n - 1 - stage["break_at"]
        conditions.append({"id": "hold_after_break", "kind": "confirm",
                           "text": f"돌파한 선 {hold['price']:,} 위에서 다음 일봉도 마감", "price": hold["price"],
                           "met": True if after >= 1 else None})
    elif brk is not None:
        conditions.append({"id": "close_above_resistance", "kind": "confirm",
                           "text": f"일봉 종가가 {brk['price']:,} 위에서 마감", "price": brk["price"], "met": False})
    conditions.append({"id": "stochastic_80", "kind": "confirm",
                       "text": f"스토캐스틱 %K 가 바닥({STOCH_FLOOR:g} 이하)에서 {STOCH_MAX_BARS}봉 안에 {STOCH_CEILING:g} 이상",
                       "price": None,
                       "met": None if stoch["status"] == "insufficient" else stoch["status"] == "confirmed"})
    conditions.append({"id": "rsi_divergence", "kind": "confirm",
                       "text": "확정 스윙 저점에서 가격 저점 ↓ · RSI 저점 ↑", "price": None,
                       "met": None if div["status"] == "insufficient" else div["status"] == "confirmed"})
    if state == "failed_breakout":
        conditions.append({"id": "close_back_below", "kind": "invalidate",
                           "text": f"돌파한 선 {brk['price']:,} 아래로 다시 마감", "price": brk["price"], "met": True})
    if hold is not None:
        conditions.append({"id": "close_below_hold", "kind": "invalidate",
                           "text": f"일봉 종가가 {hold['price']:,} 아래로 마감", "price": hold["price"],
                           "met": close < hold["price"]})

    tol_pct = round(tol / close * 100, 2)
    evidence = [quarter_level(bars, tol_pct), channel_quarter(frame, bars, index), fib_extension(bars), div, stoch,
                overlap(bars, (res_level, res) if res is not None else None, atr)]
    led = lead(t, bars, peers or {}, now)
    if led is not None:
        evidence.append(_lead_item(led))

    trend_read = chart_read.trend_state(bars)
    signal = stage["signal"] or stage.get("rebound_signal")
    basis = _SIGNAL_KO.get(signal, "최근에 반등 · 돌파 신호가 없어요")
    if state == "awaiting_confirmation" and stage["signal"] is None:
        basis = f"{bars[stage['at']]['at'][:10]} {_SIGNAL_KO[signal]} 뒤 저항 돌파 확인 전"
    return {
        "ticker": t, "timeframe": "1d", "status": "in_progress" if pending else "confirmed", "reason": None,
        "instrument": instrument(t, provider), "observed_at": _utc(now), "as_of": last["at"],
        "last_bar": {k: last[k] for k in ("at", "open", "high", "low", "close")},
        "trend": {"state": trend_read["state"], "basis": trend_read["basis"]},
        "stage": {"state": state, "signal": stage["signal"],
                  "confirmed_at": bars[stage["at"]]["at"] if stage["at"] is not None else None,
                  "basis": f"{_STAGE_KO[state]} — {basis}"},
        "key_prices": {"hold": hold, "break": brk}, "conditions": conditions, "evidence": evidence,
        "lead": led, "note": NOTE,
    }
