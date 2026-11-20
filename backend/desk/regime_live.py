"""A ticker's regime now - trend, range (box) or turning (#694), with its numbers.

The same signal means different things by regime: in a trend one follows it (a trailing stop), in a
range (위아래로 오가는 박스권 · 와리가리) one takes profit near the top and holds near the bottom.
``classify`` reads one frame's bars (oldest first, finished or not):

- ADX(``ADX_N``) with +DI / -DI (Wilder): how strong the directional move is, and which way;
- the box: the highest high and lowest low of the last ``BOX_N`` bars, its width in ATRs and %, and how
  many separate times price came within ``TOUCH_ATR`` of each edge (a round trip = a top touch then a
  bottom touch, or the other way round);
- EMA20 / EMA50 and the close.

Regimes, first that fits:

- ``turning`` 전환: the last close is ``BREAK_ATR`` or more outside the box of the bars before it (a box
  breaking), or +DI and -DI crossed in the last ``CROSS_N`` bars with ADX under ``TREND_ADX``;
- ``trend_up`` / ``trend_down`` 상승 · 하락 추세: ADX at or over ``TREND_ADX``, the DI that leads and the
  close beyond EMA20 beyond EMA50 on that side;
- ``range`` 박스권: ADX under ``RANGE_ADX`` and each edge of a box no wider than ``BOX_MAX_ATR``
  touched at least ``EDGE_TOUCHES`` separate times;
- ``unknown`` 판단 보류: none of these, or fewer than ``MIN_BARS`` bars.

Shared with #693 (accuracy checks) and #696 (the horizon that fits a ticker): ``classify`` and ``view``
are the public calls; fields are only added.
"""

from __future__ import annotations

import asyncio
import logging
from itertools import pairwise

log = logging.getLogger(__name__)

ADX_N = 14
BOX_N = 40
MIN_BARS = 60
TREND_ADX = 25.0
RANGE_ADX = 20.0
TOUCH_ATR = 0.35
BOX_MAX_ATR = 8.0
EDGE_TOUCHES = 2                 # each edge tested this many separate times: a box
BREAK_ATR = 0.5
CROSS_N = 5
FRAMES = ("60m", "1d")
DECIDES = ("1d", "60m")          # the frame whose regime is the ticker's, first readable
COUNTS = {"1d": 250, "60m": 400}
LABELS = {"trend_up": "상승 추세", "trend_down": "하락 추세", "range": "박스권", "turning": "전환",
          "unknown": "판단 보류"}


def _ema(xs: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for x in xs:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def _wilder(xs: list[float], n: int) -> list[float]:
    """Wilder's smoothing: the first value is the mean of the first ``n``, then (prev * (n-1) + x) / n."""
    if len(xs) < n:
        return []
    out = [sum(xs[:n]) / n]
    for x in xs[n:]:
        out.append((out[-1] * (n - 1) + x) / n)
    return out


def adx(bars: list[dict], n: int = ADX_N) -> dict | None:
    """{adx, plus_di, minus_di, atr} on the last bar and the +DI - -DI series (for crosses), or None."""
    if len(bars) < 2 * n + 1:
        return None
    tr, pdm, mdm = [], [], []
    for p, b in pairwise(bars):
        up, down = b["high"] - p["high"], p["low"] - b["low"]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"])))
    atr_s, pdm_s, mdm_s = _wilder(tr, n), _wilder(pdm, n), _wilder(mdm, n)
    pdi = [100 * p / a if a else 0.0 for p, a in zip(pdm_s, atr_s, strict=True)]
    mdi = [100 * m / a if a else 0.0 for m, a in zip(mdm_s, atr_s, strict=True)]
    dx = [100 * abs(p - m) / (p + m) if p + m else 0.0 for p, m in zip(pdi, mdi, strict=True)]
    adx_s = _wilder(dx, n)
    if not adx_s:
        return None
    return {"adx": adx_s[-1], "plus_di": pdi[-1], "minus_di": mdi[-1], "atr": atr_s[-1],
            "spread": [p - m for p, m in zip(pdi, mdi, strict=True)]}


def _episodes(hits: list[bool], away: list[bool]) -> list[int]:
    """The indexes where a touch of an edge starts: a new touch only after price went back to the box's
    middle (``away``) - hovering at the edge, in and out of the band, is one touch."""
    out, armed = [], True
    for i, (h, a) in enumerate(zip(hits, away, strict=True)):
        if h and armed:
            out.append(i)
            armed = False
        elif a:
            armed = True
    return out


def box(bars: list[dict], atr: float, n: int = BOX_N) -> dict | None:
    """The last ``n`` bars' box: {high, low, width_pct, width_atr, touches_high, touches_low, round_trips}."""
    span = bars[-n:]
    if len(span) < n or not atr:
        return None
    high, low = max(b["high"] for b in span), min(b["low"] for b in span)
    near = TOUCH_ATR * atr
    mid = (high + low) / 2
    tops = _episodes([b["high"] >= high - near for b in span], [b["low"] <= mid for b in span])
    bottoms = _episodes([b["low"] <= low + near for b in span], [b["high"] >= mid for b in span])
    order = sorted([(i, "h") for i in tops] + [(i, "l") for i in bottoms])
    trips = sum(1 for a, b in pairwise(order) if a[1] != b[1])
    return {"high": high, "low": low, "width_pct": round((high / low - 1) * 100, 2) if low else None,
            "width_atr": round((high - low) / atr, 2), "touches_high": len(tops), "touches_low": len(bottoms),
            "round_trips": trips}


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def classify(bars: list[dict], tf: str) -> dict:
    """{tf, regime, label, adx, plus_di, minus_di, atr, close, ema20, ema50, box, basis[], as_of, readable}
    for one frame's bars (oldest first). ``regime`` is unknown with fewer than MIN_BARS bars (``readable``
    false) or when no rule fits (``readable`` true)."""
    bars = [b for b in bars if b.get("close") and b.get("high") and b.get("low")]
    out = {"tf": tf, "regime": "unknown", "label": LABELS["unknown"], "adx": None, "plus_di": None,
           "minus_di": None, "atr": None, "close": bars[-1]["close"] if bars else None, "ema20": None,
           "ema50": None, "box": None, "basis": [], "as_of": bars[-1]["at"] if bars else None,
           "readable": False}           # enough bars to judge: unknown with readable is "no regime fits"
    if len(bars) < MIN_BARS:
        out["basis"] = [f"봉 {len(bars)}개 — {MIN_BARS}개가 안 돼 판단 보류"]
        return out
    d = adx(bars)
    closes = [b["close"] for b in bars]
    e20, e50 = _ema(closes, 20)[-1], _ema(closes, 50)[-1]
    close = closes[-1]
    if d is None:
        return out
    a, atr = d["adx"], d["atr"]
    bx = box(bars, atr)
    prev = box(bars[:-1], atr)
    out.update(adx=_r(a), plus_di=_r(d["plus_di"]), minus_di=_r(d["minus_di"]), atr=atr, ema20=e20, ema50=e50,
               box=bx)
    basis = [f"ADX {a:.0f}(+DI {d['plus_di']:.0f} · −DI {d['minus_di']:.0f})"]
    crossed = any((x > 0) != (y > 0) for x, y in pairwise(d["spread"][-CROSS_N - 1:]))
    regime = "unknown"
    up = a >= TREND_ADX and d["plus_di"] > d["minus_di"] and close > e20 > e50
    down = a >= TREND_ADX and d["minus_di"] > d["plus_di"] and close < e20 < e50
    broke_up = bool(prev) and close >= prev["high"] + BREAK_ATR * atr
    broke_down = bool(prev) and close <= prev["low"] - BREAK_ATR * atr
    # a break the trend already goes (a big down day in a downtrend) is the trend, not a turn
    if (broke_up and not up) or (broke_down and not down):
        regime = "turning"
        side = "위" if close > prev["high"] else "아래"
        basis.append(f"종가가 직전 {BOX_N}봉 박스({_fmt(prev['low'])}~{_fmt(prev['high'])}) {side}로 "
                     f"{abs(close - (prev['high'] if side == '위' else prev['low'])) / atr:.1f} ATR 벗어남")
    elif up:
        regime = "trend_up"
        basis.append(f"종가 > EMA20({_fmt(e20)}) > EMA50({_fmt(e50)})")
    elif down:
        regime = "trend_down"
        basis.append(f"종가 < EMA20({_fmt(e20)}) < EMA50({_fmt(e50)})")
    elif crossed and a < TREND_ADX:
        regime = "turning"
        basis.append(f"최근 {CROSS_N}봉 안에 +DI · −DI 교차")
    elif bx and a < RANGE_ADX and min(bx["touches_high"], bx["touches_low"]) >= EDGE_TOUCHES             and bx["width_atr"] <= BOX_MAX_ATR:
        regime = "range"
    if bx:
        basis.append(f"최근 {BOX_N}봉 박스 {_fmt(bx['low'])}~{_fmt(bx['high'])}(폭 {bx['width_pct']}% · "
                     f"{bx['width_atr']} ATR) · 위 끝 {bx['touches_high']}번 · 아래 끝 {bx['touches_low']}번 · "
                     f"왕복 {bx['round_trips']}번")
    out.update(regime=regime, label=LABELS[regime], basis=basis, readable=True)
    return out


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


async def view(prices, ticker: str, tfs: tuple[str, ...] = FRAMES) -> dict:
    """{ticker, regime, label, frames{tf: classify}, summary} - the daily frame decides, the hourly says
    what is happening inside it. A frame that cannot be read is left out (logged)."""
    async def one(tf: str) -> dict | None:
        try:
            got = await prices.candles(ticker, tf, COUNTS.get(tf, 250))
        except Exception as exc:  # noqa: BLE001 - that frame only
            log.info("regime %s %s: no bars: %s", ticker, tf, type(exc).__name__)
            return None
        return await asyncio.to_thread(classify, got.get("bars") or [], tf)

    got = dict(zip(tfs, await asyncio.gather(*(one(tf) for tf in tfs)), strict=True))
    frames_out = {tf: v for tf, v in got.items() if v is not None}
    order = [*(tf for tf in DECIDES if tf in frames_out), *(tf for tf in frames_out if tf not in DECIDES)]
    # the daily frame decides when it could be read, whatever it says; the hourly only stands in for it
    main = next((frames_out[tf] for tf in order if frames_out[tf].get("readable")), None) \
        or (frames_out[order[0]] if order else None)
    regime = main["regime"] if main else "unknown"
    parts = [f"{TF_KO.get(tf, tf)} {v['label']}" for tf, v in frames_out.items()]
    return {"ticker": ticker, "regime": regime, "label": LABELS[regime], "tf": main["tf"] if main else None,
            "frames": frames_out, "summary": " · ".join(parts) or "봉을 읽지 못해 판단 보류"}


TF_KO = {"1d": "일봉", "60m": "1시간봉", "1w": "주봉", "15m": "15분봉"}
