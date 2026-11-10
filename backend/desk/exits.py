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
- ``pyramid``: like ``runner``, and at +1R it adds ``ADD_SHARE`` of the position (at the open when
  the bar gaps past +1R); the whole position's stop then sits at the original average fill, and is
  trailed as ``runner``. Its R counts the add's cost too.

Like desk.outcome: the walk starts on the first fill's bar (its stop counts, at the stop price), a
bar touching both the stop and a target counts the stop first, a later bar that opens through the
stop fills at the open, and before T1 (+1R for runner / pyramid) the side's time stop closes it at
the bar's close. A bar that reaches +1R and falls back to break-even is taken as the worse order.
A position still open when the look-ahead ends is marked at the last close. The ladder's later
fills are folded into the average fill from the first fill's bar on (the average desk.outcome
reports). Known optimism left: a trail raised on a bar's best price is first checked on the next bar.
Pure functions.
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


def _hit(direction: int, bar: dict, stop: float) -> bool:
    return bar["low"] <= stop if direction > 0 else bar["high"] >= stop


def _stop_fill(direction: int, bar: dict, stop: float, fill_bar: bool) -> float:
    """A stop the bar opens through fills at the open - not on the fill bar (the fill came first)."""
    if fill_bar:
        return stop
    return min(bar["open"], stop) if direction > 0 else max(bar["open"], stop)


def _reached(direction: int, bar: dict, level: float) -> bool:
    return bar["high"] >= level if direction > 0 else bar["low"] <= level


def _tighter(direction: int, stop: float, level: float) -> float:
    return max(stop, level) if direction > 0 else min(stop, level)


def _best(direction: int, best: float, bar: dict) -> float:
    return max(best, bar["high"]) if direction > 0 else min(best, bar["low"])


def _half_then_trail(direction: int, bars: list[dict], avg: float, stop0: float, t1: float, trail,
                     time_stop: int | None) -> float:
    """desk.outcome's walk with the T2 exit swapped for a trail ``trail(best, i)``. R per share."""
    risk = abs(avg - stop0) or 1e-9
    sign = 1 if direction > 0 else -1
    stop, took, best, first_half = stop0, False, avg, 0.0
    for i, bar in enumerate(bars):
        if _hit(direction, bar, stop):
            out = _stop_fill(direction, bar, stop, i == 0)
            return (first_half + (0.5 if took else 1.0) * sign * (out - avg)) / risk
        if not took and _reached(direction, bar, t1):
            took, first_half, stop = True, 0.5 * sign * (t1 - avg), avg
        if took:
            best = _best(direction, best, bar)
            stop = _tighter(direction, stop, trail(best, i))
        elif time_stop and i >= time_stop:
            return sign * (bar["close"] - avg) / risk
    return (first_half + (0.5 if took else 1.0) * sign * (bars[-1]["close"] - avg)) / risk


def _runner(direction: int, bars: list[dict], avg: float, stop0: float, atr: float, add: float,
            time_stop: int | None) -> tuple[float, float]:
    """(R per initial share, the share added)."""
    risk = abs(avg - stop0) or 1e-9
    sign = 1 if direction > 0 else -1
    one_r = avg + sign * risk
    stop, armed, best, added, add_at = stop0, False, avg, 0.0, one_r

    def pnl(out: float) -> float:
        return (sign * (out - avg) + added * sign * (out - add_at)) / risk

    for i, bar in enumerate(bars):
        if _hit(direction, bar, stop):
            return pnl(_stop_fill(direction, bar, stop, i == 0)), added
        if not armed and _reached(direction, bar, one_r):
            armed, stop = True, avg
            if add:
                added = add
                add_at = max(one_r, bar["open"]) if direction > 0 else min(one_r, bar["open"])
            if _hit(direction, bar, stop):             # back to break-even in the same bar: the worse order
                return pnl(stop), added
        if armed:
            best = _best(direction, best, bar)
            stop = _tighter(direction, stop, best - sign * RUNNER_ATR * atr)
        elif time_stop and i >= time_stop:
            return pnl(bar["close"]), added
    return pnl(bars[-1]["close"]), added


def replay(direction: int, future: list[dict], first_fill_at: str | None, avg: float, stop0: float,
           t1: float, atr: float, fixed_r: float | None,
           time_stop: int | None = None) -> dict[str, float | None] | None:
    """R per filled share for each exit rule on one filled trade (None when there is nothing to
    replay), plus ``added``: the extra share the pyramid bought (its cost is on top). ``time_stop``:
    the side's bars before T1 (desk.outcome's rule)."""
    if not first_fill_at or not atr or stop0 is None or t1 is None:
        return None
    at = [b["at"] for b in future]
    if first_fill_at not in at:
        return None
    bars = future[at.index(first_fill_at):]
    sign = 1 if direction > 0 else -1

    def by_atr(best: float, i: int) -> float:
        return best - sign * TRAIL_ATR * atr

    def by_swing(best: float, i: int) -> float:
        window = bars[max(0, i - SWING_BARS + 1): i + 1]
        return min(b["low"] for b in window) if direction > 0 else max(b["high"] for b in window)

    pyramid, added = _runner(direction, bars, avg, stop0, atr, ADD_SHARE, time_stop)
    return {
        "fixed": fixed_r,
        "trail_atr": round(_half_then_trail(direction, bars, avg, stop0, t1, by_atr, time_stop), 3),
        "trail_swing": round(_half_then_trail(direction, bars, avg, stop0, t1, by_swing, time_stop), 3),
        "runner": round(_runner(direction, bars, avg, stop0, atr, 0.0, time_stop)[0], 3),
        "pyramid": round(pyramid, 3),
        "added": added,
    }
