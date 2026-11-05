"""Chart indicators computed from bars (#60). Pure functions, no I/O."""

from __future__ import annotations

RSI_PERIOD = 14
# Extra bars fetched ahead of the shown ones so the RSI is already settled on the first bar
# drawn: Wilder smoothing keeps (13/14)^n of its starting error, about 1% after 60 bars.
RSI_WARMUP = 60
INDICATORS = ("rsi14",)


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
