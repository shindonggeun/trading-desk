"""The chart at a plan's moment, as numbers a backtest can bucket (#284).

The desk's playbooks score 0-100 from their own rules, and the basket backtest shows the score does
not tell a good plan from a bad one. Before changing the rules this records what the chart looked
like when each plan was made - trend stack, distance from the 20-day line, oscillators, volatility,
volume, recent returns, the gap, where the close sits in its range, the weekly map, the market's
selling score and the strength against the index - so desk.backtest.feature_report can show which
of them separate winners from losers on the training dates *and* the held-out ones.

``compute`` reads only the bars it is given (the plan's own window: nothing after the plan) and
never changes a plan. On a swing plan the daily bars are the 200-bar window (so "range" is about
40 weeks, not 52); on an intraday plan they are the sessions before the plan's day, so the gap,
volume and returns are the previous session's. Pure functions, no I/O.
"""

from __future__ import annotations

from . import indicators, weekly_map

ATR_N = 14
RANGE_BARS = 252        # where the close sits within up to a year (a swing window has 200 bars)
# Numeric features, bucketed by the training dates' quartiles; the rest are categories.
NUMERIC = ("dist20_atr", "slope20", "rsi14", "stoch_k", "bb_pctb", "atr_pctile", "vol_ratio", "ret5",
           "ret20", "gap_atr", "range_pos", "rel20", "risk_score")
CATEGORICAL = ("ma_stack", "weekly_pos")
NAMES = {
    "ma_stack": "이평 배열(종가>20·50·200일선, 20>50일선 개수)",
    "dist20_atr": "20일선 거리(ATR 배수)",
    "slope20": "20일선 5일 기울기",
    "rsi14": "RSI(14)",
    "stoch_k": "스토캐스틱 %K",
    "bb_pctb": "볼린저(20) %B",
    "atr_pctile": "ATR 백분위(창 안, 스윙 약 200일)",
    "vol_ratio": "거래량 배수(20일 평균 대비)",
    "ret5": "5일 수익률",
    "ret20": "20일 수익률",
    "gap_atr": "계획 봉의 갭(ATR 배수, 장중 보기는 전일)",
    "range_pos": "창 범위 안 위치(0 저점 · 1 고점, 스윙 약 200일)",
    "weekly_pos": "주간 지도 위치",
    "rel20": "지수 대비 20일 상대 강도",
    "risk_score": "시장 투매 점수",
}


def _sma(xs: list[float], n: int, end: int | None = None) -> float | None:
    end = len(xs) if end is None else end
    if end < n:
        return None
    return sum(xs[end - n:end]) / n


def _atrs(bars: list[dict], n: int = ATR_N) -> list[float]:
    """The n-bar mean true range at each bar from the n-th on (oldest first)."""
    trs = []
    for i, b in enumerate(bars):
        prev = bars[i - 1]["close"] if i else b["close"]
        trs.append(max(b["high"], prev) - min(b["low"], prev))
    return [sum(trs[i - n + 1:i + 1]) / n for i in range(n - 1, len(trs))]


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


def compute(daily: list[dict], weekly: list[dict] | None = None, risk: dict | None = None) -> dict:
    """The features of the last bar of ``daily`` (oldest first). A feature the bars cannot give
    (too short a window, no volume, no index) is None or left out."""
    if len(daily) < 21:
        return {}
    closes = [b["close"] for b in daily]
    close, last = closes[-1], daily[-1]
    out: dict = {}

    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    votes = [close > sma20] + ([close > sma50, sma20 > sma50] if sma50 else []) + ([close > sma200] if sma200 else [])
    out["ma_stack"] = f"{sum(votes)}/{len(votes)}"

    atrs = _atrs(daily)
    atr = atrs[-1] if atrs else None
    out["dist20_atr"] = _r((close - sma20) / atr, 2) if atr else None
    before = _sma(closes, 20, len(closes) - 5)
    out["slope20"] = _r(sma20 / before - 1, 4) if before else None

    out["rsi14"] = indicators.rsi(closes)[-1]
    out["stoch_k"] = indicators.stochastic([b["high"] for b in daily], [b["low"] for b in daily], closes)[0][-1]
    band = indicators.bollinger(closes, 20)[-1]
    width = band["upper"] - band["lower"] if band else 0
    out["bb_pctb"] = _r((close - band["lower"]) / width, 3) if band and width else None

    if atr and close:
        ratios = [a / b["close"] for a, b in zip(atrs[-RANGE_BARS:], daily[-len(atrs[-RANGE_BARS:]):], strict=True)
                  if b["close"]]
        now = atr / close
        out["atr_pctile"] = _r(sum(1 for x in ratios if x <= now) / len(ratios), 3) if len(ratios) >= 20 else None
    else:
        out["atr_pctile"] = None

    vols = [b.get("volume") or 0 for b in daily]
    avg_vol = sum(vols[-21:-1]) / 20
    out["vol_ratio"] = _r(vols[-1] / avg_vol, 2) if avg_vol > 0 and vols[-1] else None

    out["ret5"] = _r(close / closes[-6] - 1, 4) if closes[-6] else None
    out["ret20"] = _r(close / closes[-21] - 1, 4) if closes[-21] else None
    prev = closes[-2]
    out["gap_atr"] = _r((last["open"] - prev) / atr, 2) if atr and last.get("open") is not None else None

    year = daily[-RANGE_BARS:]
    hi, lo = max(b["high"] for b in year), min(b["low"] for b in year)
    out["range_pos"] = _r((close - lo) / (hi - lo), 3) if hi > lo else None

    wm = weekly_map.build("", weekly) if weekly else None
    out["weekly_pos"] = (f"{wm['position']}{':' + wm['cross'] if wm['cross'] else ''}") if wm else None

    index = (risk or {}).get("index") or {}
    out["rel20"] = _r(out["ret20"] - index["ret20"], 4) \
        if out["ret20"] is not None and index.get("ret20") is not None else None
    out["risk_score"] = (risk or {}).get("score")
    return out
