"""The weekly market map (#202): where the main indices stand on their weekly bars, how far they
could fall or rise, and how often each line held before - in plain Korean for a beginner.

Each index gets, from its weekly bars only up to the week asked for (no look-ahead):

- two long trend lines - the 20-week ("검정선") and the 60-week ("빨간선") moving averages - and
  where the close sits against them; a close under the black line opens the way to the red one;
- the 50-week Bollinger band (2 sigma), the 52-week high / low, the fall's Fibonacci retracements and
  the nearest weekly swing lows / highs as support / resistance;
- this week's candle (a long black / white body, a long lower / upper wick) and a break or reclaim
  of the black line;
- the record behind the lines: after a weekly close broke under the black line (above the red one),
  how often the low reached the red line within ``REACH_WEEKS`` and in how many weeks, and the
  breaks that turned straight back up instead (the V-shaped exceptions, by date); after the low
  first touched the 50-week lower band, how often the close was higher ``REBOUND_WEEKS`` later.

Nothing here is a forecast: every line carries its own record with its sample size. Pure arithmetic
on the bars apart from ``Maps`` (Yahoo weekly bars, cached; desk.api builds one).
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

log = logging.getLogger(__name__)

VERSION = 1
# (symbol, Korean name, fallback symbol when the first has no bars)
TARGETS: tuple[tuple[str, str, str | None], ...] = (
    ("NQ=F", "나스닥100 선물", "^NDX"),
    ("QQQ", "나스닥100 ETF(QQQ)", None),
    ("SOXX", "반도체 ETF(SOXX)", None),
    ("^GSPC", "S&P 500", None),
    ("^KS200", "코스피200", "069500.KS"),     # Yahoo has no KOSPI200 bars: the KODEX 200 ETF tracks it
)
NAMES = {s: name for s, name, _ in TARGETS}
HISTORY_WEEKS = 520            # about ten years of weekly bars for the record
BLACK, RED = 20, 60            # the trend lines (weeks)
BB_WEEKS, BB_SIGMA = 50, 2.0
REACH_WEEKS = 26               # a break under the black line "reached" the red one within this
V_WEEKS = 8                    # ... or turned back above the black line within this: a V exception
REBOUND_WEEKS = 13             # the close this many weeks after a lower-band touch
TOUCH_GAP = 4                  # a new touch needs this many weeks without one
LONG_BODY = 1.5                # a body this many times the 20-week average body is "long"
SWING_K = 3                    # a weekly swing low / high: lower / higher than K weeks either side

# Leveraged and sector tickers whose analysis reads an index map (#202 -> desk.runner note)
UNDERLYING = {
    "SOXL": "SOXX", "SOXS": "SOXX", "USD": "SOXX", "SMH": "SOXX", "SOXX": "SOXX",
    "NVDA": "SOXX", "AMD": "SOXX", "AVGO": "SOXX", "MU": "SOXX", "TSM": "SOXX", "INTC": "SOXX",
    "TQQQ": "QQQ", "SQQQ": "QQQ", "QLD": "QQQ", "QID": "QQQ", "QQQ": "QQQ",
    "SPXL": "^GSPC", "UPRO": "^GSPC", "SPXS": "^GSPC", "SSO": "^GSPC", "SPY": "^GSPC", "VOO": "^GSPC",
    "122630.KS": "^KS200", "252670.KS": "^KS200", "069500.KS": "^KS200", "114800.KS": "^KS200",
}

GLOSSARY = [
    {"term": "주봉", "text": "한 주(월~금) 가격을 막대 하나로 그린 것. 하루하루의 흔들림을 걸러 큰 흐름을 본다."},
    {"term": "장대음봉 · 장대양봉", "text": "그 주에 크게 빠진(음봉) · 크게 오른(양봉) 막대. 평소보다 몸통이 길면 그 방향 힘이 셌다는 뜻."},
    {"term": "검정선 · 빨간선", "text": "여기서는 20주 · 60주 평균 가격선. 검정선 아래로 주봉이 마감하면 다음 받침으로 빨간선을 본다."},
    {"term": "볼린저밴드(50주)", "text": "50주 평균에서 위아래로 표준편차 2배 떨어진 띠. 아래 띠 근처는 과거에 큰 바닥이 자주 나온 자리."},
    {"term": "지지 · 저항", "text": "가격이 내려오다 멈춘(지지) · 올라가다 막힌(저항) 자리. 여러 번 확인될수록 강하다."},
]
CHECKLIST = [
    "지금 사려는 가격이 미리 계획한 자리(지지 · 밴드 하단 등)인가?",
    "바닥 신호가 두 개 이상 겹치는가(밴드 하단 · 지지선 · 아래꼬리 · 과매도)?",
    "이 선이 100% 지켜진다고 믿고 있지 않은가? (아래 '예외' 날짜를 볼 것)",
    "더 빠지면(다음 선까지) 어떻게 할지 — 추가 매수 · 손절 · 기다림이 정해져 있는가?",
]


def _sma(xs: list[float], n: int, i: int) -> float | None:
    return sum(xs[i - n + 1:i + 1]) / n if i + 1 >= n else None


def _pct(a: float | None, b: float | None) -> float | None:
    return round((a / b - 1) * 100, 1) if a is not None and b else None


def week_complete(last_at: str, today: date | None = None) -> bool:
    """A weekly bar is stamped on its Monday: the week is over once its Friday has passed."""
    monday = datetime.fromisoformat(last_at).date()
    return (today or datetime.now(UTC).date()) > monday + timedelta(days=4)


def candle(bars: list[dict], i: int) -> dict:
    """This week's bar in words: a long body, a long wick, or an ordinary week."""
    b = bars[i]
    body = abs(b["close"] - b["open"])
    rng = max(b["high"] - b["low"], 1e-9)
    past = [abs(x["close"] - x["open"]) for x in bars[max(0, i - 20):i]]
    avg = statistics.mean(past) if past else body
    lower = min(b["open"], b["close"]) - b["low"]
    upper = b["high"] - max(b["open"], b["close"])
    change = _pct(b["close"], bars[i - 1]["close"]) if i else None
    if avg and body >= LONG_BODY * avg and body >= 0.5 * rng:
        kind = "long_black" if b["close"] < b["open"] else "long_white"
    elif lower >= 2 * body and lower >= 0.5 * rng:
        kind = "lower_wick"
    elif upper >= 2 * body and upper >= 0.5 * rng:
        kind = "upper_wick"
    else:
        kind = "plain"
    label = {"long_black": "장대음봉", "long_white": "장대양봉", "lower_wick": "아래꼬리(밑에서 되받음)",
             "upper_wick": "윗꼬리(위에서 밀림)", "plain": "보통 주봉"}[kind]
    return {"kind": kind, "label": label, "change_pct": change}


def _swings(bars: list[dict], i: int, k: int = SWING_K) -> tuple[list[float], list[float]]:
    """Weekly swing lows and highs that had both sides finished by week ``i``."""
    lows, highs = [], []
    for j in range(k, i - k + 1):
        window = bars[j - k:j + k + 1]
        if bars[j]["low"] == min(x["low"] for x in window):
            lows.append(bars[j]["low"])
        if bars[j]["high"] == max(x["high"] for x in window):
            highs.append(bars[j]["high"])
    return lows, highs


def record(bars: list[dict], i: int) -> dict:
    """The lines' record up to week ``i`` - only events whose outcome was known by then."""
    closes = [b["close"] for b in bars]
    breaks, reached, weeks_to, v_dates, open_breaks = 0, 0, [], [], 0
    touches, rebounds, deeper, open_touches = 0, 0, [], 0
    last_touch = -10 ** 9
    for j in range(max(BLACK, RED), i + 1):
        black, red = _sma(closes, BLACK, j), _sma(closes, RED, j)
        prev_black = _sma(closes, BLACK, j - 1)
        if prev_black is not None and closes[j - 1] >= prev_black and closes[j] < black and closes[j] > red:
            if j + REACH_WEEKS > i:                     # its outcome was not known yet at week i
                open_breaks += 1
            else:
                breaks += 1
                hit = next((w for w in range(1, REACH_WEEKS + 1)
                            if bars[j + w]["low"] <= (_sma(closes, RED, j + w) or red)), None)
                if hit:
                    reached += 1
                    weeks_to.append(hit)
                elif any(closes[j + w] >= (_sma(closes, BLACK, j + w) or black) for w in range(1, V_WEEKS + 1)):
                    v_dates.append(bars[j]["at"][:10])
        mid = _sma(closes, BB_WEEKS, j)
        if mid is not None:
            sd = statistics.pstdev(closes[j - BB_WEEKS + 1:j + 1])
            touching = bars[j]["low"] <= mid - BB_SIGMA * sd
            fresh = touching and j - last_touch >= TOUCH_GAP      # weeks hugging the band are one touch
            if touching:
                last_touch = j
            if fresh:
                if j + REBOUND_WEEKS > i:
                    open_touches += 1
                else:
                    touches += 1
                    if closes[j + REBOUND_WEEKS] > closes[j]:
                        rebounds += 1
                    else:
                        deeper.append(bars[j]["at"][:10])
    return {
        "black_break": {"n": breaks, "reached_red": reached,
                        "rate": round(reached / breaks, 2) if breaks else None,
                        "median_weeks": statistics.median(weeks_to) if weeks_to else None,
                        "v_exceptions": v_dates[-5:], "pending": open_breaks, "within_weeks": REACH_WEEKS},
        "bb_lower_touch": {"n": touches, "rebounded": rebounds,
                           "rate": round(rebounds / touches, 2) if touches else None,
                           "lower_after": deeper[-5:], "pending": open_touches, "after_weeks": REBOUND_WEEKS},
    }


def build(symbol: str, bars: list[dict], i: int | None = None, today: date | None = None) -> dict | None:
    """The map of ``symbol`` as of week ``i`` (the last bar when None), from ``bars[:i+1]`` only.
    None with fewer than the red line's weeks."""
    i = len(bars) - 1 if i is None else i
    if i + 1 < RED:
        return None
    closes = [b["close"] for b in bars]
    close = closes[i]
    black, red = _sma(closes, BLACK, i), _sma(closes, RED, i)
    mid = _sma(closes, BB_WEEKS, i)
    sd = statistics.pstdev(closes[i - BB_WEEKS + 1:i + 1])
    upper, lower = mid + BB_SIGMA * sd, mid - BB_SIGMA * sd
    year = bars[max(0, i - 51):i + 1]
    hi52, lo52 = max(b["high"] for b in year), min(b["low"] for b in year)
    span = hi52 - lo52
    fib = [{"ratio": r, "price": round(lo52 + r * span, 2)} for r in (0.382, 0.5, 0.618)] if span > 0 else []
    lows, highs = _swings(bars, i)
    supports = sorted({round(x, 2) for x in lows if x < close}, reverse=True)[:3]
    resistances = sorted({round(x, 2) for x in highs if x > close})[:3]
    prev_black = _sma(closes, BLACK, i - 1)
    if prev_black is not None and closes[i - 1] >= prev_black and close < black:
        cross = "broke_black"
    elif prev_black is not None and closes[i - 1] < prev_black and close >= black:
        cross = "reclaimed_black"
    else:
        cross = None
    position = "above_black" if close >= black else "between" if close >= red else "below_red"
    # the next line each way: under the black line the red one is the floor; under the red one the
    # nearest of the 52-week low and the lower band. Up: the line just above, or the nearest of the
    # 52-week high and the upper band above the black line.
    if position == "below_red":
        down = max((x for x in (lower, lo52) if x < close), default=min(lower, lo52))
        up = red
    else:
        down = red
        up = black if position == "between" else min((x for x in (hi52, upper) if x > close), default=max(hi52, upper))
    rec = record(bars, i)
    out = {
        "version": VERSION, "symbol": symbol, "name": NAMES.get(symbol, symbol),
        "week": bars[i]["at"][:10], "week_complete": week_complete(bars[i]["at"], today) if i == len(bars) - 1 else True,
        "close": round(close, 2),
        "lines": {"black": {"weeks": BLACK, "price": round(black, 2), "pct": _pct(black, close)},
                  "red": {"weeks": RED, "price": round(red, 2), "pct": _pct(red, close)}},
        "position": position, "cross": cross,
        "bollinger": {"weeks": BB_WEEKS, "upper": round(upper, 2), "middle": round(mid, 2), "lower": round(lower, 2),
                      "lower_pct": _pct(lower, close), "upper_pct": _pct(upper, close)},
        "range_52w": {"high": round(hi52, 2), "low": round(lo52, 2), "from_high_pct": _pct(close, hi52)},
        "fibonacci": fib, "supports": supports, "resistances": resistances,
        "candle": candle(bars, i),
        "scenario": {"down": {"price": round(down, 2), "pct": _pct(down, close)},
                     "up": {"price": round(up, 2), "pct": _pct(up, close)}},
        "record": rec,
    }
    out["text"] = explain(out)
    return out


def _won(_m: dict, v: float) -> str:
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"


def explain(m: dict) -> dict:
    """Plain Korean from the numbers above: the bottom line, the lines with their record, the terms
    and the plan checklist. No forecast words - 'opens the way to', 'happened N of M times'."""
    name, close = m["name"], m["close"]
    black, red = m["lines"]["black"], m["lines"]["red"]
    bb, rec = m["bollinger"], m["record"]
    down, up = m["scenario"]["down"], m["scenario"]["up"]
    if m["position"] == "above_black":
        head = (f"{name}: 주봉이 검정선(20주선 {_won(m, black['price'])}) 위에 있다. 검정선을 깨고 마감하면 "
                f"빨간선(60주선 {_won(m, red['price'])}, 지금보다 {red['pct']:+.1f}%)까지 열어 둔다.")
    elif m["position"] == "between":
        head = (f"{name}: 주봉이 검정선 아래, 빨간선 위에 있다. 다음 받침 — 빨간선 {_won(m, red['price'])}"
                f"({red['pct']:+.1f}%), 그 아래 50주 볼린저 하단 {_won(m, bb['lower'])}({bb['lower_pct']:+.1f}%).")
    else:
        floors = [f"52주 저점 {_won(m, m['range_52w']['low'])}({_pct(m['range_52w']['low'], close):+.1f}%)"
                  if m["range_52w"]["low"] < close else None,
                  f"50주 볼린저 하단 {_won(m, bb['lower'])}({bb['lower_pct']:+.1f}%)" if bb["lower"] < close else None]
        head = f"{name}: 주봉이 빨간선(60주선) 아래로 내려와 있다."
        if bb["lower"] >= close:
            head += (f" 50주 볼린저 하단({_won(m, bb['lower'])})보다도 아래 — 과거 큰 바닥이 자주 나온 구간이지만"
                     f" 띠 안으로 돌아오는지 먼저 본다.")
        if any(floors):
            head += " 다음 바닥 후보 — " + ", ".join(f for f in floors if f) + "."
    if m["cross"] == "broke_black":
        head += " 이번 주 검정선을 깨고 마감했다."
    elif m["cross"] == "reclaimed_black":
        head += " 이번 주 검정선을 되찾고 마감했다."
    if not m["week_complete"]:
        head += " (이번 주봉은 아직 마감 전)"
    lines = [f"이번 주봉: {m['candle']['label']}"
             + (f" ({m['candle']['change_pct']:+.1f}%)" if m["candle"]["change_pct"] is not None else "") + "."]
    bk = rec["black_break"]
    if bk["n"]:
        txt = (f"과거 검정선 이탈 {bk['n']}번 중 {bk['reached_red']}번이 {bk['within_weeks']}주 안에 빨간선까지 내려갔다"
               + (f"(보통 {bk['median_weeks']:.0f}주)" if bk["median_weeks"] else "") + ".")
        if bk["v_exceptions"]:
            txt += f" 예외 — 바로 검정선을 되찾은 V자: {', '.join(bk['v_exceptions'])}."
        lines.append(txt)
    else:
        lines.append("검정선 이탈 사례가 아직 없어 도달률을 말할 수 없다.")
    bt = rec["bb_lower_touch"]
    if bt["n"]:
        txt = f"50주 볼린저 하단에 닿은 {bt['n']}번 중 {bt['rebounded']}번은 {bt['after_weeks']}주 뒤 더 높았다."
        if bt["lower_after"]:
            txt += f" 그러지 못한 때: {', '.join(bt['lower_after'])}."
        lines.append(txt)
    lines.append(f"다음 선 — 아래 {_won(m, down['price'])}({down['pct']:+.1f}%), 위 {_won(m, up['price'])}"
                 f"({up['pct']:+.1f}%). 예측이 아니라 가격이 과거에 멈춘 자리다.")
    return {"headline": head, "lines": lines, "glossary": GLOSSARY, "checklist": CHECKLIST}


_CANDLE_EN = {"long_black": "a long down candle", "long_white": "a long up candle", "lower_wick": "a long lower wick",
              "upper_wick": "a long upper wick", "plain": "an ordinary candle"}


def note(m: dict | None, for_ticker: str) -> str | None:
    """One English line for the agents of a leveraged or sector ticker (desk.runner)."""
    if not m:
        return None
    bk = m["record"]["black_break"]
    where = {"above_black": "above its 20-week line", "between": "under its 20-week but above its 60-week line",
             "below_red": "under its 60-week line"}[m["position"]]
    rate = f"; past 20-week breaks reached the 60-week line {bk['reached_red']}/{bk['n']}" if bk["n"] else ""
    return (f"Weekly map of {m['symbol']} (the index behind {for_ticker}, week of {m['week']}): close {m['close']}, "
            f"{where} (20w {m['lines']['black']['price']}, 60w {m['lines']['red']['price']}{rate}); "
            f"50-week band {m['bollinger']['lower']}-{m['bollinger']['upper']}; this week "
            f"{_CANDLE_EN[m['candle']['kind']]}. "
            f"Context for the bigger trend, not an entry signal.")


def history(symbol: str, bars: list[dict], weeks: int = 8) -> list[dict]:
    """The map as of each of the last ``weeks`` weeks, newest first - only the bars up to each week."""
    out = []
    for i in range(len(bars) - 1, max(len(bars) - 1 - weeks, RED - 2), -1):
        m = build(symbol, bars, i)
        if m:
            out.append({k: m[k] for k in ("week", "week_complete", "close", "lines", "position", "cross", "candle",
                                          "scenario")} | {"headline": m["text"]["headline"]})
    return out


# ---------------------------------------------------------------- live


class Maps:
    """Weekly bars per index from Yahoo, cached; thread-safe (read from worker threads)."""

    def __init__(self, candles: Callable | None = None, ttl_sec: float = 3600.0, clock=time.monotonic) -> None:
        if candles is None:
            from .prices import yahoo_candles as candles
        self._candles = candles
        self.ttl = ttl_sec
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: dict[str, tuple[float, str, list[dict]]] = {}

    def bars(self, symbol: str) -> tuple[str, list[dict]]:
        """(the symbol the bars came from, weekly bars oldest first); a fallback symbol stands in."""
        with self._lock:
            hit = self._rows.get(symbol)
        if hit and self._clock() - hit[0] < self.ttl:
            return hit[1], hit[2]
        fallback = next((f for s, _n, f in TARGETS if s == symbol), None)
        got, used = [], symbol
        for sym in (symbol, fallback):
            if not sym:
                continue
            try:
                got = [{"at": b.at, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
                       for b in self._candles(sym, "1w", HISTORY_WEEKS) if b.close]
            except Exception as exc:  # noqa: BLE001 - the fallback, or this index missing
                log.info("weekly map: %s failed: %s", sym, exc)
                got = []
            if len(got) >= RED:
                used = sym
                break
        with self._lock:
            self._rows[symbol] = (self._clock(), used, got)
        return used, got

    def one(self, symbol: str) -> dict:
        used, bars = self.bars(symbol)
        m = build(symbol, bars)
        if m is None:
            return {"symbol": symbol, "name": NAMES.get(symbol, symbol), "error": "주봉이 모자라 지도를 만들 수 없습니다"}
        if used != symbol:
            m["source_symbol"] = used
        return m

    def all(self) -> dict:
        """Every target; one that fails answers its error and the others still come."""
        maps = []
        for symbol, _name, _f in TARGETS:
            try:
                maps.append(self.one(symbol))
            except Exception as exc:  # noqa: BLE001
                log.warning("weekly map %s failed: %s", symbol, exc)
                maps.append({"symbol": symbol, "name": NAMES.get(symbol, symbol), "error": type(exc).__name__})
        return {"maps": maps, "generated_at": datetime.now(UTC).isoformat(timespec="seconds")}

    def history(self, symbol: str, weeks: int = 8) -> list[dict]:
        _used, bars = self.bars(symbol)
        return history(symbol, bars, weeks)

    def note_for(self, ticker: str) -> str | None:
        index = UNDERLYING.get(ticker.upper())
        if not index:
            return None
        m = self.one(index)
        return None if "error" in m else note(m, ticker.upper())
