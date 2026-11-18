"""The long-term bottom zone ("인생 타점", #240): where a stock or an index sits against the deep lows
of its own past, on weekly and monthly bars, and a plan to buy into it in steps over months.

Not the short swing of #167 (desk.value_swing: days in, weeks out) - this is the rare, deep low a
holder buys over months and keeps for months to years. So the plan has no tight stop: it is wrong
when the structure breaks (a monthly close under the deepest support), not on a few percent.

Evidence, each with its value and threshold so a page and the analysts' note can say why:

- ``drawdown``: the fall from the highest close so far, and its percentile among every week of the
  instrument's own past (a 30% fall is ordinary for one stock and rare for another);
- ``weekly_rsi`` / ``monthly_rsi``: 14-period RSI of weekly and monthly closes, oversold;
- ``divergence``: a weekly lower low in price with a higher low in RSI (selling losing force);
- ``long_ma``: the close at or under the 60-week or the 120-week average (the long trend lines);
- ``support``: a major weekly swing low (lower than 8 weeks either side) of the last five years
  within a few percent of the close;
- ``capitulation``: the market-wide selling score (desk.risk_regime) peaked recently and eased -
  passed in by the caller, unknown (None) in the backtest.

Score = the weighted share of the known evidence that is met, 0-100. Stage: ``none`` (the fall is
too small to be a long-term low), ``watch``, ``approach`` or ``bottom`` (the 인생 타점 itself).

Only bars up to the week asked for are read (no look-ahead): ``assess(bars, i)`` sees ``bars[:i+1]``.
Nothing here is a forecast; the backtest (``backtest``) shows how the same rules did at past lows.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable
from datetime import UTC, datetime

log = logging.getLogger(__name__)

VERSION = 1
HISTORY_WEEKS = 1100               # about 21 years: 2008 is in the backtest
MIN_WEEKS = 60                     # less history than this is not enough to call a long-term low
MIN_DRAWDOWN = 15.0                # under this fall (%) there is no long-term low to speak of
RSI_N = 14
WEEKLY_OVERSOLD, MONTHLY_OVERSOLD = 30.0, 35.0
DRAWDOWN_PCTL = 85.0               # the fall is deeper than this share of the instrument's own weeks
NEAR_PCT = 4.0                     # "near" a support line: within this many percent
SWING_K = 8                        # a major weekly swing low: lower than K weeks either side
SUPPORT_WEEKS = 260                # the swing lows of the last five years count
DIVERGENCE_WEEKS = 26
WEIGHTS = {"drawdown": 25, "weekly_rsi": 15, "monthly_rsi": 15, "divergence": 10, "long_ma": 15,
           "support": 10, "capitulation": 10}
STAGES = ((70, "bottom"), (45, "approach"), (0, "watch"))
STAGE_KO = {"none": "해당 없음", "watch": "관찰", "approach": "접근", "bottom": "인생 타점"}
LADDER_WEIGHTS = (20, 20, 30, 30)  # % of the planned amount per step, more as it gets cheaper
MIN_STEP_GAP = 5.0                 # % between two steps
FALLBACK_STEPS = (0.0, 8.0, 16.0, 25.0)     # % under the first step when supports run out
MAX_DEPTH = 30.0                   # % under the close: the ladder's deepest step (supports deeper wait)
INVALIDATION_BUFFER = 8.0          # % under the deepest step: a monthly close there breaks the plan


# --- arithmetic on the bars -----------------------------------------------------------------------

def _rsi(closes: list[float], n: int = RSI_N) -> list[float | None]:
    """Wilder's RSI for every close (None until there are n changes)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(closes)):
        if i > n:
            avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
            avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if avg_l == 0 else round(100 - 100 / (1 + avg_g / avg_l), 1)
    return out


def _sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def monthly(bars: list[dict]) -> list[dict]:
    """Weekly bars grouped by the month of their date: last close, lowest low, highest high."""
    out: list[dict] = []
    for b in bars:
        key = b["at"][:7]
        if out and out[-1]["at"][:7] == key:
            m = out[-1]
            m.update(close=b["close"], low=min(m["low"], b["low"]), high=max(m["high"], b["high"]))
        else:
            out.append({"at": b["at"], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]})
    return out


def _swing_lows(bars: list[dict], k: int = SWING_K) -> list[tuple[int, float]]:
    """(index, low) of weekly lows lower than the k weeks on either side (complete ones only)."""
    lows = [b["low"] for b in bars]
    return [(i, lows[i]) for i in range(k, len(lows) - k)
            if lows[i] == min(lows[i - k:i + k + 1])]


def _pctl(values: list[float], x: float) -> float:
    return round(100.0 * sum(1 for v in values if v <= x) / len(values), 1) if values else 0.0


def _check(cid: str, met: bool | None, value, threshold: str) -> dict:
    return {"id": cid, "met": met, "value": value, "threshold": threshold, "weight": WEIGHTS[cid]}


# --- the zone -------------------------------------------------------------------------------------

def assess(bars: list[dict], i: int | None = None, capitulation: dict | None = None,
           leveraged: bool = False) -> dict | None:
    """The zone at week ``i`` (default: the newest) from ``bars[:i+1]``; None with too few weeks.

    ``capitulation``: the market-wide selling score (desk.risk_regime.score), or None when unknown.
    ``leveraged``: the ticker is a leveraged / inverse product (the plan then says so)."""
    i = len(bars) - 1 if i is None else i
    if i + 1 < MIN_WEEKS:
        return None
    seen = bars[:i + 1]
    closes = [b["close"] for b in seen]
    close = closes[-1]

    # the fall from the highest close so far, against every week's fall so far
    peak, falls = 0.0, []
    for c in closes:
        peak = max(peak, c)
        falls.append((1 - c / peak) * 100 if peak else 0.0)
    high = max(closes)
    dd = round(falls[-1], 1)
    dd_pctl = _pctl(falls, falls[-1])

    w_rsi = _rsi(closes)
    m_bars = monthly(seen)
    m_rsi = _rsi([m["close"] for m in m_bars])
    rsi_w, rsi_m = w_rsi[-1], m_rsi[-1]

    # divergence: the newest swing low under the one before, with a higher RSI there
    div = None
    window = range(max(1, len(seen) - DIVERGENCE_WEEKS), len(seen))
    pivots = [j for j in window if j + 1 < len(seen) and seen[j]["low"] <= min(seen[j - 1]["low"], seen[j + 1]["low"])]
    pivots.append(len(seen) - 1)
    if len(pivots) >= 2 and all(w_rsi[j] is not None for j in pivots[-2:]):
        a, b = pivots[-2], pivots[-1]
        div = seen[b]["low"] < seen[a]["low"] and (w_rsi[b] or 0) > (w_rsi[a] or 0)

    ma60, ma120 = _sma(closes, 60), _sma(closes, 120)
    lines = {"ma60": round(ma60, 4) if ma60 else None, "ma120": round(ma120, 4) if ma120 else None}
    under_or_near = [m for m in (ma60, ma120) if m and close <= m * (1 + NEAR_PCT / 100)]
    long_ma = bool(under_or_near) if (ma60 or ma120) else None

    swings = [(j, low) for j, low in _swing_lows(seen) if j >= len(seen) - SUPPORT_WEEKS]
    supports = sorted({round(low, 4) for _j, low in swings if low <= close * (1 + NEAR_PCT / 100)}, reverse=True)
    near = next((s for s in supports if abs(close / s - 1) * 100 <= NEAR_PCT), None)
    support_met = (near is not None) if swings else None

    cap_met, cap_value = None, None
    if capitulation is not None:
        cap_value = capitulation.get("score")
        peak_score = capitulation.get("peak", cap_value)
        cap_met = bool(peak_score is not None and peak_score >= 60 and cap_value is not None and cap_value < peak_score)

    evidence = [
        _check("drawdown", dd >= 20 and dd_pctl >= DRAWDOWN_PCTL, {"pct": dd, "pctl": dd_pctl},
               f"고점 대비 −20% 이상이면서 과거 낙폭의 상위 {100 - DRAWDOWN_PCTL:.0f}%"),
        _check("weekly_rsi", None if rsi_w is None else rsi_w <= WEEKLY_OVERSOLD, rsi_w, f"주봉 RSI ≤ {WEEKLY_OVERSOLD:.0f}"),
        _check("monthly_rsi", None if rsi_m is None else rsi_m <= MONTHLY_OVERSOLD, rsi_m,
               f"월봉 RSI ≤ {MONTHLY_OVERSOLD:.0f}"),
        _check("divergence", div, None, "주봉 저점은 낮아졌는데 RSI 저점은 높아짐"),
        _check("long_ma", long_ma, lines, f"60주 · 120주 이평 아래거나 {NEAR_PCT:.0f}% 안"),
        _check("support", support_met, near, f"최근 5년 주봉 큰 저점 {NEAR_PCT:.0f}% 안"),
        _check("capitulation", cap_met, cap_value, "시장 투매 점수가 60 이상 찍고 내려옴"),
    ]
    known = [e for e in evidence if e["met"] is not None]
    weight = sum(e["weight"] for e in known)
    score = round(100 * sum(e["weight"] for e in known if e["met"]) / weight) if weight else 0
    if dd < MIN_DRAWDOWN:
        stage = "none"
    else:
        stage = next(name for floor, name in STAGES if score >= floor)

    ladder = _ladder(close, supports, [m for m in (ma60, ma120) if m and m < close])
    deepest = ladder[-1]["price"]
    return {
        # #310: the lines the ladder stands on, so live_ladder can cut it again at the price now
        "ladder_basis": {"supports": supports, "lines": [round(m, 4) for m in (ma60, ma120) if m]},
        "version": VERSION, "as_of": seen[-1]["at"], "close": round(close, 4), "score": score,
        "stage": stage, "stage_ko": STAGE_KO[stage], "drawdown_pct": dd, "high": round(high, 4),
        "evidence": evidence, "ladder": ladder if stage != "none" else [],
        "invalidation": {"monthly_close_below": _round(deepest * (1 - INVALIDATION_BUFFER / 100)),
                         "text": f"월봉 종가가 마지막 단계보다 {INVALIDATION_BUFFER:.0f}% 아래에서 끝나면 바닥 판단을 접는다"},
        "targets": [t for t in (
            {"id": "ma60", "price": _round(ma60), "text": "60주 이평 복귀"} if ma60 and ma60 > close else None,
            {"id": "high_80", "price": _round(high * 0.8), "text": "직전 고점의 80%"} if high * 0.8 > close else None,
            {"id": "high", "price": _round(high), "text": "직전 고점"},
        ) if t],
        "leveraged_note": ("레버리지 · 인버스 상품은 오래 들고 가면 일일 재조정 감쇠로 기초자산보다 덜 회복한다 — "
                           "본주 · 지수 ETF 로 모으거나 비중을 절반 이하로") if leveraged else None,
    }


def _round(x: float | None) -> float | None:
    if x is None:
        return None
    return round(x, 2) if x < 1000 else round(x)


def _ladder(close: float, supports: list[float], lines_below: list[float]) -> list[dict]:
    """Four buy steps: now, then the supports and long lines below (down to MAX_DEPTH), at least
    MIN_STEP_GAP apart; fixed falls fill in when there are not enough of them."""
    prices = [close]
    floor = close * (1 - MAX_DEPTH / 100)
    for p in sorted({x for x in supports + lines_below if x >= floor}, reverse=True):
        if p < prices[-1] * (1 - MIN_STEP_GAP / 100):
            prices.append(p)
        if len(prices) == len(LADDER_WEIGHTS):
            break
    for fall in FALLBACK_STEPS:
        if len(prices) == len(LADDER_WEIGHTS):
            break
        p = close * (1 - fall / 100)
        if p < prices[-1] * (1 - MIN_STEP_GAP / 100):
            prices.append(p)
    while len(prices) < len(LADDER_WEIGHTS):          # supports packed close together
        prices.append(prices[-1] * (1 - MIN_STEP_GAP * 1.6 / 100))
    prices = sorted(prices, reverse=True)
    return [{"step": n + 1, "price": _round(p), "weight_pct": w,
             "from_close_pct": round((p / close - 1) * 100, 1)}
            for n, (p, w) in enumerate(zip(prices, LADDER_WEIGHTS, strict=True))]


NOW_BAND_PCT = 3.0     # the first step is "now" while the price is at most this far above it


def _invalidation(deepest: float) -> dict:
    return {"monthly_close_below": _round(deepest * (1 - INVALIDATION_BUFFER / 100)),
            "text": f"월봉 종가가 마지막 단계보다 {INVALIDATION_BUFFER:.0f}% 아래에서 끝나면 바닥 판단을 접는다"}


def live_ladder(z: dict, price: float | None, source: str | None = None, at: str | None = None) -> dict:
    """#310: the zone with its ladder cut again at the price now - the reading (score, stage,
    evidence) stays the weekly one, only the buy steps move.

    - Under the weekly close: the steps above the price have ``passed``; a new ladder starts at the
      price with only the supports and long lines below it, and the invalidation follows its
      deepest step.
    - Within NOW_BAND_PCT above the first step: the first step is ``now``.
    - Further above: the first step waits (``below``) and ``first_step_gap_pct`` says how far.

    Every step gets ``from_now_pct`` and ``status`` (now | below | passed); without a price the
    weekly close stands in (``price_source: weekly_close``)."""
    if not z or not z.get("ladder"):
        return z
    close = z["close"]
    if not price or price <= 0:
        price, source, at = close, "weekly_close", z.get("as_of")
    basis = z.get("ladder_basis") or {"supports": [], "lines": []}
    out = {**z, "price_now": _round(price), "price_source": source, "price_at": at}

    def mark(steps: list[dict], first_now: bool) -> list[dict]:
        rows = []
        for n, s in enumerate(steps):
            status = "now" if n == 0 and first_now else "below"
            rows.append({**s, "from_now_pct": round((s["price"] / price - 1) * 100, 1), "status": status})
        return rows

    if price < close * (1 - 1e-9):
        below = [s for s in basis["supports"] if s < price]
        lines = [m for m in basis["lines"] if m < price]
        fresh = _ladder(price, below, lines)
        passed = [{**s, "from_now_pct": round((s["price"] / price - 1) * 100, 1), "status": "passed"}
                  for s in z["ladder"] if s["price"] > price]
        out["ladder"] = passed + mark(fresh, True)
        out["invalidation"] = _invalidation(fresh[-1]["price"])
        out["first_step_gap_pct"] = 0.0
        return out
    first = z["ladder"][0]["price"]
    now = price <= first * (1 + NOW_BAND_PCT / 100)
    out["ladder"] = mark(z["ladder"], now)
    out["first_step_gap_pct"] = 0.0 if now else round((first / price - 1) * 100, 1)
    return out


def note(z: dict | None, ticker: str) -> str | None:
    """One line for the analysts: the stage, its reasons and the ladder (only when there is a zone)."""
    if not z or z["stage"] in ("none",):
        return None
    met = [e["id"] for e in z["evidence"] if e["met"]]
    steps = " / ".join(f"{s['price']}({s['weight_pct']}%)" for s in z["ladder"] if s.get("status") != "passed")
    basis = (f" (from the price now {z['price_now']}, {z['price_source']})" if z.get("price_now") is not None
             and z.get("price_source") != "weekly_close" else " (from the last weekly close)")
    wait = (f"; the first step is {abs(z['first_step_gap_pct'])}% under the price now - not a buy yet"
            if z.get("first_step_gap_pct") else "")
    return (f"Long-term bottom zone ({ticker}, weekly/monthly, desk rules #240): stage {z['stage']} "
            f"score {z['score']}/100, down {z['drawdown_pct']}% from the high; met: {', '.join(met) or 'none'}. "
            f"Staged buys over months{basis}: {steps}{wait}; the plan is void on a monthly close under "
            f"{z['invalidation']['monthly_close_below']}. A rule-based reading, not a forecast."
            + (" Leveraged/inverse product: daily rebalancing decay makes it recover less than its "
               "underlying over months - accumulate the underlying or an index ETF, or at most half the size."
               if z.get("leveraged_note") else ""))


# --- the backtest ---------------------------------------------------------------------------------

BOTTOMS = {   # the big lows to look for (month), per market
    "US": ("2008-11", "2009-03", "2020-03", "2022-10", "2025-04"),
    "KR": ("2008-10", "2020-03", "2022-09", "2024-08", "2025-04"),
}
HORIZONS_WEEKS = (26, 52, 104)
SIGNAL_GAP_WEEKS = 26      # a new signal needs this long without one


def backtest(bars: list[dict], stage: str = "approach", horizons=HORIZONS_WEEKS) -> dict:
    """Walk the weeks; each time the stage first reaches ``stage`` (or deeper) after a quiet gap,
    follow the plan made that week: its steps fill when a later week's low reaches them (until the
    plan is void), and the average cost is marked at each horizon. A signal is false when the price
    ended up under its invalidation, or the year after it lost money."""
    rank = {"none": 0, "watch": 1, "approach": 2, "bottom": 3}
    # one signal per episode: a new one needs SIGNAL_GAP_WEEKS in a row under the stage, so a long
    # bear market that stays in the zone counts once
    signals, quiet, armed = [], SIGNAL_GAP_WEEKS, True
    for i in range(MIN_WEEKS - 1, len(bars)):
        z = assess(bars, i)
        if z is None or rank[z["stage"]] < rank[stage]:
            quiet += 1
            armed = armed or quiet >= SIGNAL_GAP_WEEKS
            continue
        quiet = 0
        if not armed:
            continue
        armed = False
        signals.append(_follow(bars, i, z, horizons))
    done = [s for s in signals if s["returns"].get(f"{max(horizons)}w") is not None or s["void"]]
    false = [s for s in done if s["false"]]
    out = {"signals": signals, "count": len(signals),
           "false_rate": round(100 * len(false) / len(done), 1) if done else None}
    for h in horizons:
        rs = [s["returns"][f"{h}w"] for s in signals if s["returns"].get(f"{h}w") is not None]
        out[f"avg_{h}w"] = round(statistics.fmean(rs), 1) if rs else None
    dds = [s["max_drawdown"] for s in signals if s["max_drawdown"] is not None]
    out["avg_max_drawdown"] = round(statistics.fmean(dds), 1) if dds else None
    return out


def _follow(bars: list[dict], i: int, z: dict, horizons) -> dict:
    void_at = z["invalidation"]["monthly_close_below"]
    filled: list[tuple[float, int]] = []          # (price, weight)
    pending = list(z["ladder"])
    worst, void, returns = None, False, {}
    last_month = bars[i]["at"][:7]
    end = min(len(bars), i + max(horizons) + 1)
    for j in range(i, end):
        b = bars[j]
        if j == i:
            # the signal is known at the week's close: only the first step (that close) fills then;
            # the week's own low came before the signal and fills nothing
            first = pending.pop(0)
            filled.append((first["price"], first["weight_pct"]))
        elif not void:
            for s in [s for s in pending if b["low"] <= s["price"]]:
                filled.append((s["price"], s["weight_pct"]))
                pending.remove(s)
        cost = (sum(p * w for p, w in filled) / sum(w for _p, w in filled)) if filled else None
        if cost and j > i:
            dd = (b["low"] / cost - 1) * 100
            worst = dd if worst is None else min(worst, dd)
        # a month ended (the next bar starts another): its close under the line voids the plan
        if j + 1 < len(bars) and bars[j + 1]["at"][:7] != b["at"][:7] and b["close"] < void_at and b["at"][:7] != last_month:
            void = True
        k = j - i
        if k in horizons and cost:
            returns[f"{k}w"] = round((b["close"] / cost - 1) * 100, 1)
    year = returns.get("52w")
    return {"at": bars[i]["at"][:10], "stage": z["stage"], "score": z["score"], "close": z["close"],
            "drawdown_pct": z["drawdown_pct"], "filled_steps": len(filled), "void": void,
            "returns": returns, "max_drawdown": round(worst, 1) if worst is not None else None,
            "false": void or (year is not None and year < 0)}


def lead(signals: list[dict], bottoms: tuple[str, ...], bars: list[dict]) -> list[dict]:
    """For each big low (month): the lowest week in it, and the first signal within a year before
    it - how many weeks early, and how far above the low that signal's close was."""
    out = []
    for month in bottoms:
        weeks = [b for b in bars if b["at"][:7] == month]
        if not weeks:
            continue
        low = min(weeks, key=lambda b: b["low"])
        low_day = low["at"][:10]
        before = [s for s in signals if s["at"] <= low_day and _weeks_between(s["at"], low_day) <= 52]
        first = before[0] if before else None
        out.append({"bottom": month, "low": _round(low["low"]),
                    "signal_at": first["at"] if first else None,
                    "weeks_early": _weeks_between(first["at"], low_day) if first else None,
                    "above_low_pct": round((first["close"] / low["low"] - 1) * 100, 1) if first else None})
    return out


def _weeks_between(a: str, b: str) -> int:
    da, db = datetime.fromisoformat(a[:10]), datetime.fromisoformat(b[:10])
    return round((db - da).days / 7)


def market_of(ticker: str) -> str:
    t = ticker.upper()
    return "KR" if t.endswith((".KS", ".KQ")) or t.startswith(("^KS", "^KQ")) else "US"


def report(tickers: list[str], candles: Callable | None = None, out=None) -> dict:
    """``python -m desk.cli bottom-backtest``: per ticker the signals, the big lows' lead and the
    staged buys' results, for the ``approach`` and ``bottom`` stages."""
    import sys

    out = out or sys.stdout
    if candles is None:
        from .prices import yahoo_candles as candles
    result = {}
    for t in tickers:
        try:
            bars = [{"at": b.at, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
                    for b in candles(t, "1w", HISTORY_WEEKS) if b.close]
        except Exception as exc:  # noqa: BLE001 - one ticker failing leaves the rest
            print(f"{t}: no bars ({type(exc).__name__})", file=out)
            continue
        if len(bars) < MIN_WEEKS:
            print(f"{t}: only {len(bars)} weeks", file=out)
            continue
        per = {}
        for stage in ("approach", "bottom"):
            bt = backtest(bars, stage)
            bt["lead"] = lead(bt["signals"], BOTTOMS[market_of(t)], bars)
            per[stage] = bt
            print(f"{t} [{stage}] {bars[0]['at'][:10]}~{bars[-1]['at'][:10]} signals {bt['count']} "
                  f"false {bt['false_rate']}% | avg 26w {bt['avg_26w']}% 52w {bt['avg_52w']}% "
                  f"104w {bt['avg_104w']}% | avg max drawdown {bt['avg_max_drawdown']}%", file=out)
            for row in bt["lead"]:
                hit = (f"signal {row['signal_at']} ({row['weeks_early']}w early, {row['above_low_pct']}% above the low)"
                       if row["signal_at"] else "no signal in the year before")
                print(f"    low {row['bottom']} @ {row['low']}: {hit}", file=out)
        result[t] = per
    result["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return result


# --- the live zone --------------------------------------------------------------------------------

class Zones:
    """Each ticker's zone from its Yahoo weekly bars (HISTORY_WEEKS), cached; thread-safe."""

    def __init__(self, candles: Callable | None = None, ttl_sec: float = 3600.0, clock=None,
                 leveraged: Callable[[str], bool] | None = None, max_cached: int = 64) -> None:
        import threading
        import time

        if candles is None:
            from .prices import yahoo_candles as candles
        self._candles = candles
        self.ttl = ttl_sec
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._rows: dict[str, tuple[float, list[dict]]] = {}
        self._max = max_cached
        if leveraged is None:
            from .underlying import leverage_of

            def leveraged(t: str) -> bool:
                return leverage_of(t) is not None
        self._leveraged = leveraged

    def bars(self, ticker: str, ahead: float = 0.0) -> list[dict]:
        """The weekly bars, cached ``ttl``; ``ahead`` (the prewarm, #643) reads again one that expires that soon."""
        with self._lock:
            hit = self._rows.get(ticker)
        if hit and self._clock() - hit[0] < self.ttl - ahead:
            return hit[1]
        got = [{"at": b.at, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
               for b in self._candles(ticker, "1w", HISTORY_WEEKS) if b.close]
        with self._lock:
            self._rows.pop(ticker, None)
            self._rows[ticker] = (self._clock(), got)
            while len(self._rows) > self._max:
                self._rows.pop(next(iter(self._rows)))
        return got

    def warm(self, tickers: list[str], ahead: float = 0.0) -> int:
        """#643 the dashboard's zones (the main indices and the favorites) read before a page asks: the
        first ask after the hour no longer waits on Yahoo's weekly bars. How many were read; one failing
        does not stop the others."""
        done = 0
        for t in dict.fromkeys(tickers):
            try:
                self.bars(t, ahead)
                done += 1
            except Exception as exc:  # noqa: BLE001 - that ticker only
                log.info("prewarm bottom zone %s failed: %s", t, type(exc).__name__)
        return done

    def one(self, ticker: str, price: float | None = None, source: str | None = None,
            at: str | None = None) -> dict:
        """The zone, its ladder cut at ``price`` (#310; the weekly close without one)."""
        z = assess(self.bars(ticker), leveraged=self._leveraged(ticker))
        if z is None:
            return {"ticker": ticker, "supported": False,
                    "error": f"주봉이 {MIN_WEEKS}주보다 적어 장기 바닥을 판단할 수 없습니다"}
        return {"ticker": ticker, "supported": True, **live_ladder(z, price, source, at)}

    def note_for(self, ticker: str, price: float | None = None, source: str | None = None) -> str | None:
        try:
            z = self.one(ticker, price, source)
        except Exception as exc:  # noqa: BLE001 - Yahoo down: the run goes on without the line
            log.info("bottom zone note for %s failed: %s", ticker, type(exc).__name__)
            return None
        return note(z, ticker) if z.get("supported") else None
