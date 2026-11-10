"""Exit rules side by side on the same fills (#239): does riding the winners pay more than the fixed
T1 / T2 exit, and does adding to a winner (pyramiding) help?

desk.outcome judges a playbook with its fixed exit: half at T1, the stop to break-even, the rest at
T2. Here every filled trade of the backtest is replayed from its first fill with other exits - the
entry, the planned risk (average fill to the original stop) and the bars stay the same, so the
difference is the exit alone:

- ``fixed``: desk.outcome's own result (the baseline);
- ``trail_atr``: half at T1, then the stop to break-even and trailed ``TRAIL_ATR`` x ATR under the
  best price since (above it for a short) - the rest rides until the trail is hit;
- ``trail_swing``: half at T1, then the rest trailed under the lowest low of the last ``SWING_BARS``
  bars (the highest high for a short), never looser than break-even;
- ``runner``: no partial: the whole position's stop goes to break-even at +1R and is then trailed
  ``RUNNER_ATR`` x ATR;
- ``pyramid``: like ``runner``, and at +1R it adds ``ADD_SHARE`` of the position at that price; the
  whole position's stop then sits at the original average fill (the add risks 1R of its own), and
  is trailed as ``runner``. Its R counts the add's cost too.

A position still open when the look-ahead ends is marked at the last close, as the backtest does.
The ladder's later fills are folded into the average fill from the first fill's bar on (the same
average desk.outcome reports) - an approximation the fixed exit shares, so the comparison holds.
A bar touching both the stop and a target counts the stop first (as desk.outcome). Pure functions.
"""

from __future__ import annotations

VARIANTS = ("fixed", "trail_atr", "trail_swing", "runner", "pyramid")
TRAIL_ATR = 2.0
RUNNER_ATR = 3.0
SWING_BARS = 5
ADD_SHARE = 0.5
BIG_R = 3.0                       # a "big win" (#239: 한 번에 크게)


def _favourable(direction: int, bar: dict) -> float:
    return bar["high"] if direction > 0 else bar["low"]


def _adverse(direction: int, bar: dict) -> float:
    return bar["low"] if direction > 0 else bar["high"]


def _hit(direction: int, bar: dict, stop: float) -> bool:
    return bar["low"] <= stop if direction > 0 else bar["high"] >= stop


def _gap_fill(direction: int, bar: dict, stop: float) -> float:
    """A stop that the bar opens through fills at the open, not at the stop."""
    return min(bar["open"], stop) if direction > 0 else max(bar["open"], stop)


def _half_then_trail(direction: int, bars: list[dict], avg: float, stop0: float, t1: float, atr: float,
                     trail) -> float:
    """Half at T1, the rest trailed by ``trail(best, i)`` (never looser than break-even); R per share."""
    risk = abs(avg - stop0) or 1e-9
    sign = 1 if direction > 0 else -1
    stop, took, best, first_half = stop0, False, avg, 0.0
    for i, bar in enumerate(bars):
        if _hit(direction, bar, stop):
            out = _gap_fill(direction, bar, stop)
            return (first_half + (0.5 if took else 1.0) * sign * (out - avg)) / risk
        if not took and (bar["high"] >= t1 if direction > 0 else bar["low"] <= t1):
            took, first_half, stop = True, 0.5 * sign * (t1 - avg), avg
        if took:
            best = max(best, _favourable(direction, bar)) if direction > 0 else min(best, _favourable(direction, bar))
            level = trail(best, i)
            stop = max(stop, level) if direction > 0 else min(stop, level)
    last = bars[-1]["close"] if bars else avg
    return (first_half + (0.5 if took else 1.0) * sign * (last - avg)) / risk


def _runner(direction: int, bars: list[dict], avg: float, stop0: float, atr: float, add: float) -> tuple[float, float]:
    """The whole position rides; at +1R the stop goes to break-even (``add`` > 0: it adds that share
    at +1R and the stop sits at the original average); then trailed RUNNER_ATR x ATR.
    (R per initial share, the share added)."""
    risk = abs(avg - stop0) or 1e-9
    sign = 1 if direction > 0 else -1
    one_r = avg + sign * risk
    stop, armed, best, added = stop0, False, avg, 0.0
    for bar in bars:
        if _hit(direction, bar, stop):
            out = _gap_fill(direction, bar, stop)
            return (sign * (out - avg) + added * sign * (out - one_r)) / risk, added
        if not armed and (bar["high"] >= one_r if direction > 0 else bar["low"] <= one_r):
            armed, stop, added = True, avg, add
        if armed:
            best = max(best, _favourable(direction, bar)) if direction > 0 else min(best, _favourable(direction, bar))
            level = best - sign * RUNNER_ATR * atr
            stop = max(stop, level) if direction > 0 else min(stop, level)
    last = bars[-1]["close"] if bars else avg
    return (sign * (last - avg) + added * sign * (last - one_r)) / risk, added


def replay(direction: int, future: list[dict], first_fill_at: str | None, avg: float, stop0: float,
           t1: float, atr: float, fixed_r: float | None) -> dict[str, float | None] | None:
    """R per filled share for each exit rule on one filled trade (None when there is nothing to replay),
    plus ``added``: the extra share the pyramid bought (its cost is on top)."""
    if not first_fill_at or not atr or stop0 is None or t1 is None:
        return None
    at = [b["at"] for b in future]
    if first_fill_at not in at:
        return None
    # from the bar after the first fill: the fill bar's own range came before the fill (no hindsight)
    bars = future[at.index(first_fill_at) + 1:]
    if not bars:
        return {"fixed": fixed_r, "trail_atr": fixed_r, "trail_swing": fixed_r, "runner": fixed_r,
                "pyramid": fixed_r, "added": 0.0}
    sign = 1 if direction > 0 else -1

    def by_atr(best: float, i: int) -> float:
        return best - sign * TRAIL_ATR * atr

    def by_swing(best: float, i: int) -> float:
        window = bars[max(0, i - SWING_BARS + 1): i + 1]
        return min(b["low"] for b in window) if direction > 0 else max(b["high"] for b in window)

    pyramid, added = _runner(direction, bars, avg, stop0, atr, ADD_SHARE)
    return {
        "fixed": fixed_r,
        "trail_atr": round(_half_then_trail(direction, bars, avg, stop0, t1, atr, by_atr), 3),
        "trail_swing": round(_half_then_trail(direction, bars, avg, stop0, t1, atr, by_swing), 3),
        "runner": round(_runner(direction, bars, avg, stop0, atr, 0.0)[0], 3),
        "pyramid": round(pyramid, 3),
        "added": added,
    }
