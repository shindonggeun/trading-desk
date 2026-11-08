"""Chart indicators computed from bars (#60, #203). Pure functions, no I/O."""

from __future__ import annotations

import math

RSI_PERIOD = 14
# Extra bars fetched ahead of the shown ones so the RSI is already settled on the first bar
# drawn: Wilder smoothing keeps (13/14)^n of its starting error, about 1% after 60 bars.
RSI_WARMUP = 60
# #203: TradingView's Stoch (14, 1, 3) and Bollinger Bands (20 and 50, 2 standard deviations)
STOCH_K, STOCH_SMOOTH, STOCH_D = 14, 1, 3
BB_MULT = 2.0
INDICATORS = ("rsi14", "stoch", "bb20", "bb50")
# Bars needed ahead of the first one drawn for each indicator to be settled (or defined at all).
WARMUP = {"rsi14": RSI_WARMUP, "stoch": STOCH_K + STOCH_SMOOTH + STOCH_D, "bb20": 20, "bb50": 50}
# Short names the API also takes: rsi = rsi14, bb = both bands.
ALIASES = {"rsi": ("rsi14",), "bb": ("bb20", "bb50")}


def normalize(names) -> tuple[str, ...]:
    """The indicator ids asked for, aliases expanded, sorted; ValueError for an unknown one."""
    out: set[str] = set()
    for name in names:
        name = name.strip().lower()
        if not name:
            continue
        expanded = ALIASES.get(name, (name,))
        unknown = [n for n in expanded if n not in INDICATORS]
        if unknown:
            raise ValueError(f"indicators must be among {', '.join((*INDICATORS, *ALIASES))}")
        out.update(expanded)
    return tuple(sorted(out))


def _sma(values: list[float | None], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1:i + 1]
        if all(v is not None for v in window):
            out[i] = sum(window) / n
    return out


def stochastic(highs: list[float], lows: list[float], closes: list[float], k: int = STOCH_K,
               smooth: int = STOCH_SMOOTH, d: int = STOCH_D) -> tuple[list[float | None], list[float | None]]:
    """(%K, %D) as TradingView's ``ta.stoch``: 100 x (close - lowest low) / (highest high - lowest
    low) over ``k`` bars, %K its ``smooth``-bar mean, %D the ``d``-bar mean of %K. A flat window (no
    range) is 50."""
    raw: list[float | None] = [None] * len(closes)
    for i in range(k - 1, len(closes)):
        hh, ll = max(highs[i - k + 1:i + 1]), min(lows[i - k + 1:i + 1])
        raw[i] = 50.0 if hh == ll else 100 * (closes[i] - ll) / (hh - ll)
    pct_k = _sma(raw, smooth)
    pct_d = _sma(pct_k, d)
    return ([None if v is None else round(v, 2) for v in pct_k],
            [None if v is None else round(v, 2) for v in pct_d])


def bollinger(closes: list[float], n: int, mult: float = BB_MULT) -> list[dict | None]:
    """{"mid", "upper", "lower"} per close: the n-bar mean +- mult population standard deviations
    (TradingView's ``ta.stdev`` divides by n), None until n closes are known."""
    out: list[dict | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        window = closes[i - n + 1:i + 1]
        mid = sum(window) / n
        sd = math.sqrt(sum((c - mid) ** 2 for c in window) / n)
        out[i] = {"mid": round(mid, 4), "upper": round(mid + mult * sd, 4), "lower": round(mid - mult * sd, 4)}
    return out


def annotate(rows: list[dict], indicators: tuple[str, ...]) -> None:
    """Add the asked indicators to bar dicts in place: ``rsi``, ``stoch_k`` / ``stoch_d``, ``bb20`` /
    ``bb50`` (``{"mid", "upper", "lower"}``)."""
    closes = [r["close"] for r in rows]
    if "rsi14" in indicators:
        for row, value in zip(rows, rsi(closes), strict=True):
            row["rsi"] = value
    if "stoch" in indicators:
        ks, ds = stochastic([r["high"] for r in rows], [r["low"] for r in rows], closes)
        for row, k, d in zip(rows, ks, ds, strict=True):
            row["stoch_k"], row["stoch_d"] = k, d
    for name, n in (("bb20", 20), ("bb50", 50)):
        if name in indicators:
            for row, band in zip(rows, bollinger(closes, n), strict=True):
                row[name] = band


def rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder's RSI, one value per close (None until ``period`` changes are known).

    The first average is the simple mean of the first ``period`` gains/losses; after that
    each average is ``(prev * (period - 1) + current) / period``. A flat stretch with no
    losses is 100, with neither gains nor losses 50.
    """
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = _value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _value(avg_gain, avg_loss)
    return out


def _value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 50.0 if avg_gain == 0 else 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)
