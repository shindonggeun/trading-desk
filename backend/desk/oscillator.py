"""Overbought / oversold, and what followed it before (#203).

The RSI(14) and the Stochastic (14, 1, 3) of a ticker's daily (or weekly) bars, every past episode
where one of them went to an extreme (RSI >= 70 or <= 30, %K >= 80 or <= 20; the episode's first
bar), and what the price did over the next 5 / 10 / 20 bars - the return and the worst drop / best
rise within 20. From those the current reading gets a zone ("hot": a pullback is likely, "cold":
a rebound is likely, "neutral") and a sentence with the record behind it and its sample size.

Whether the reading earns its place is measured, not assumed: for each kind of extreme the 20-bar
return after it is compared with the ticker's 20-bar return from any bar (``effect``: True when a
hot kind was followed by weaker, a cold kind by stronger). When it was not, the wording says the
reading is for reference only.

A leveraged or inverse product (SOXL, SOXS, TQQQ ...) is read on its underlying too (SOXX, QQQ):
the product's own oscillator swings with its leverage; the index's is the one traders quote.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass

from . import underlying as under
from .indicators import rsi, stochastic

INTERVALS = ("1d", "1w")
HISTORY_BARS = {"1d": 800, "1w": 260}   # about three years of daily bars, five of weekly
LOOK = (5, 10, 20)
SPAN = LOOK[-1]
RSI_HOT, RSI_COLD = 70.0, 30.0
STOCH_HOT, STOCH_COLD = 80.0, 20.0
MIN_N = 5                               # fewer past episodes: the record is shown but called thin
CACHE_TTL_SEC = 15 * 60
LABEL = {"1d": "일봉", "1w": "주봉"}

Bars = list[dict]
Fetch = Callable[[str, str], Bars]


def _rows(bars) -> Bars:
    return [asdict(b) if is_dataclass(b) else dict(b) for b in bars]


def _forward(bars: Bars, i: int) -> dict:
    close = bars[i]["close"]
    ahead = bars[i + 1:i + 1 + SPAN]
    return {f"ret{n}": round(bars[i + n]["close"] / close - 1, 4) for n in LOOK} | {
        "max_drop": round(min(b["low"] for b in ahead) / close - 1, 4),
        "max_rise": round(max(b["high"] for b in ahead) / close - 1, 4)}


def _series(bars: Bars) -> tuple[list, list, list]:
    closes = [b["close"] for b in bars]
    ks, ds = stochastic([b["high"] for b in bars], [b["low"] for b in bars], closes)
    return rsi(closes), ks, ds


KINDS = {
    "rsi_hot": (0, lambda v: v >= RSI_HOT), "rsi_cold": (0, lambda v: v <= RSI_COLD),
    "stoch_hot": (1, lambda v: v >= STOCH_HOT), "stoch_cold": (1, lambda v: v <= STOCH_COLD),
}


def episodes(bars: Bars) -> list[dict]:
    """Every extreme episode's first bar with its value and (when 20 bars followed) what came next."""
    series = _series(bars)
    out = []
    for kind, (which, test) in KINDS.items():
        inside = False
        for i, value in enumerate(series[which]):
            hit = value is not None and test(value)
            if hit and not inside:
                row = {"kind": kind, "at": bars[i]["at"], "value": value}
                if i + SPAN < len(bars):
                    row.update(_forward(bars, i))
                out.append(row)
            inside = hit
    return sorted(out, key=lambda r: (r["at"], r["kind"]))


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _share(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 3) if flags else None


def stats(rows: list[dict]) -> dict:
    done = [r for r in rows if "ret20" in r]
    return {"n": len(done), "thin": len(done) < MIN_N,
            "up20": _share([r["ret20"] > 0 for r in done]),
            **{f"ret{n}": _mean([r[f"ret{n}"] for r in done]) for n in LOOK},
            "max_drop": _mean([r["max_drop"] for r in done]), "max_rise": _mean([r["max_rise"] for r in done])}


def baseline(bars: Bars) -> dict:
    """The same numbers from every bar - what an extreme has to beat."""
    return stats([_forward(bars, i) for i in range(len(bars) - SPAN)])


def _divergence(bars: Bars, values: list, look: int = 20) -> str | None:
    """Bearish: a higher high in price over the last ``look`` bars on a lower RSI high than the
    ``look`` bars before; bullish the mirror on the lows."""
    if len(bars) < 2 * look or any(v is None for v in values[-2 * look:]):
        return None
    now, before = slice(-look, None), slice(-2 * look, -look)
    highs, lows = [b["high"] for b in bars], [b["low"] for b in bars]
    if max(highs[now]) > max(highs[before]) and max(values[now]) < max(values[before]):
        return "bearish"
    if min(lows[now]) < min(lows[before]) and min(values[now]) > min(values[before]):
        return "bullish"
    return None


def zone(bars: Bars) -> dict:
    """The current reading: hot (RSI >= 70, or %K and %D >= 80), cold (the mirror) or neutral."""
    r, k, d = _series(bars)
    now_r, now_k, now_d = r[-1], k[-1], d[-1]
    hot_r = now_r is not None and now_r >= RSI_HOT
    hot_s = now_k is not None and now_d is not None and now_k >= STOCH_HOT and now_d >= STOCH_HOT
    cold_r = now_r is not None and now_r <= RSI_COLD
    cold_s = now_k is not None and now_d is not None and now_k <= STOCH_COLD and now_d <= STOCH_COLD
    name = "hot" if hot_r or hot_s else "cold" if cold_r or cold_s else "neutral"
    by = ("rsi" if hot_r else "stoch") if name == "hot" else ("rsi" if cold_r else "stoch") if name == "cold" else None
    return {"zone": name, "by": by, "rsi": now_r, "stoch_k": now_k, "stoch_d": now_d,
            "divergence": _divergence(bars, r), "at": bars[-1]["at"] if bars else None}


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def report(bars, ticker: str, interval: str = "1d") -> dict:
    """The zone now, the record after each kind of extreme and against any bar, and a sentence."""
    bars = _rows(bars)
    if len(bars) < SPAN + 30:
        return {"ticker": ticker, "interval": interval, "zone": None, "error": "too few bars"}
    rows = episodes(bars)
    by_kind = {kind: stats([r for r in rows if r["kind"] == kind]) for kind in KINDS}
    base = baseline(bars)
    # per kind: did a hot reading come before a weaker 20 bars than any bar's (a cold one a stronger)?
    # The RSI and the Stochastic can disagree on the same ticker, so they are not pooled.
    effect = {"baseline_ret20": base["ret20"]}
    for kind, s in by_kind.items():
        if s["ret20"] is None or base["ret20"] is None:
            effect[kind] = None
        else:
            effect[kind] = s["ret20"] < base["ret20"] if kind.endswith("hot") else s["ret20"] > base["ret20"]
    now = zone(bars)
    years = round(len(bars) / (252 if interval == "1d" else 52), 1)
    return {"ticker": ticker, "interval": interval, "bars": len(bars), "years": years, **now,
            "by_kind": by_kind, "baseline": base, "effect": effect,
            "recent": [r for r in rows if r["kind"].endswith(now["zone"])][-5:] if now["zone"] != "neutral" else [],
            "reason": _reason(ticker, interval, now, by_kind, effect, base, years)}


def _reason(ticker: str, interval: str, now: dict, by_kind: dict, effect: dict, base: dict, years: float) -> str:
    label = LABEL.get(interval, interval)
    reading = f"RSI {now['rsi']:.0f}" if now["rsi"] is not None else "RSI —"
    if now["stoch_k"] is not None:
        reading += f" · 스토캐스틱 {now['stoch_k']:.0f}"
    head = f"{ticker} {label} {reading}"
    if now["zone"] == "neutral":
        return f"{head} — 과열 · 침체 구간 아님"
    kind = f"{now['by']}_{now['zone']}"
    s = by_kind[kind]
    weak = effect.get(kind)
    if not s["n"]:
        return f"{head} — {'과열' if now['zone'] == 'hot' else '침체'} 구간이지만 지난 {years}년 같은 자리가 없어 통계 없음"
    if now["zone"] == "hot":
        down = round(s["n"] * (1 - s["up20"]))
        text = (f"{head} — 과열(위험) 구간. 지난 {years}년 같은 자리 {s['n']}번 중 {down}번은 {label} 20봉 뒤 더 낮았고, "
                f"20봉 안 평균 최대 낙폭 {_pct(s['max_drop'])}(평소 {_pct(base['max_drop'])})")
    else:
        up = round(s["n"] * s["up20"])
        text = (f"{head} — 침체(바닥권) 구간. 지난 {years}년 같은 자리 {s['n']}번 중 {up}번은 {label} 20봉 뒤 더 높았고, "
                f"20봉 안 평균 최대 상승 {_pct(s['max_rise'])}(평소 {_pct(base['max_rise'])})")
    if now["divergence"]:
        text += f", {'하락' if now['divergence'] == 'bearish' else '상승'} 다이버전스"
    if s["thin"]:
        text += " — 표본 적음"
    if weak is False:
        text += " — 참고: 이 종목은 이 판정 뒤 20봉 수익률이 평소와 다르지 않았음"
    return text


# ------------------------------------------------------------------- fetching


_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}


def read(fetch: Fetch, ticker: str, interval: str = "1d", clock: Callable[[], float] = time.monotonic) -> dict:
    """``report`` on ``fetch(ticker, interval)``'s bars, cached a quarter of an hour."""
    key = (ticker.upper(), interval)
    hit = _CACHE.get(key)
    if hit and clock() - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    result = report(fetch(ticker, interval), ticker.upper(), interval)
    if len(_CACHE) > 200:
        _CACHE.clear()
    _CACHE[key] = (clock(), result)
    return result


def view(fetch: Fetch, ticker: str, interval: str = "1d") -> dict:
    """The ticker's reading and, for a leveraged / inverse product, its underlying's."""
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
    own = read(fetch, ticker, interval)
    lever = under.leverage_of(ticker)
    base = None
    if lever:
        base = {**read(fetch, lever[0], interval), "leverage": lever[1]}
    return {"ticker": ticker.upper(), "interval": interval, "own": own, "underlying": base}


def prompt_note(result: dict | None) -> str | None:
    """One line for the deciding agents: the zone and the record behind it (daily bars)."""
    if not result or not result.get("zone"):
        return None
    return f"Oscillator (desk, past episodes): {result['reason']}"


def yahoo_fetch(yahoo_candles: Callable) -> Fetch:
    """A Fetch over desk.prices' Yahoo candles: about three years of daily (five of weekly) bars."""
    return lambda ticker, interval: _rows(yahoo_candles(ticker, interval, HISTORY_BARS[interval]))


async def note_for(fetch: Fetch, ticker: str, timeout: float = 20.0) -> str | None:
    """The analysis note for ``ticker`` (the run's own instrument: the underlying of a product)."""
    return prompt_note(await asyncio.wait_for(asyncio.to_thread(read, fetch, ticker, "1d"), timeout))
