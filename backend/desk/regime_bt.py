"""Walk-forward evaluation of daily range and trend rules using completed OHLC bars."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from statistics import fmean

from . import regime_live

REGIMES = ("range", "trend_up", "trend_down")
TARGETS = ("quick", "wide")
MIN_TRADES = 30
MAX_TAIL_RATE = 0.10
HOLD_BARS = 10
COST_BPS = 10


@dataclass(frozen=True)
class Trade:
    exit_index: int
    net_r: float


def metrics(results: list[float]) -> dict:
    wins = [r for r in results if r > 0]
    losses = [-r for r in results if r < 0]
    equity = peak = drawdown = 0.0
    for r in results:
        equity += r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    n = len(results)
    return {
        "n": n,
        "win_rate": len(wins) / n if n else None,
        "mean_r": fmean(results) if n else None,
        "payoff_ratio": fmean(wins) / fmean(losses) if wins and losses else None,
        "expectancy_r": (sum(wins) - sum(losses)) / n if n else None,
        "below_minus_1r_rate": sum(r < -1 for r in results) / n if n else None,
        "worst_r": min(results, default=None),
        "max_drawdown_r": drawdown if n else None,
    }


def _eligible(row: dict, min_trades: int) -> bool:
    return (row["n"] >= min_trades and row["expectancy_r"] > 0
            and row["below_minus_1r_rate"] <= MAX_TAIL_RATE)


def _levels(state: dict, variant: str) -> tuple[int, float, float] | None:
    regime, close, atr = state["regime"], state["close"], state["atr"]
    if not atr or regime not in REGIMES:
        return None
    if regime == "range":
        box = state["box"]
        low, high = box["low"], box["high"]
        if close > low + 0.5 * atr:
            return None
        fraction = 0.5 if variant == "quick" else 0.85
        return 1, low - 0.25 * atr, low + fraction * (high - low)
    side = 1 if regime == "trend_up" else -1
    if not 0 < side * (close - state["ema20"]) <= 0.5 * atr:
        return None
    reward = 1.0 if variant == "quick" else 2.5
    return side, state["ema20"] - side * atr, close + side * reward * atr


def _play(bars: list[dict], entry: int, state: dict, variant: str, cost_bps: float) -> Trade | None:
    levels = _levels(state, variant)
    if levels is None:
        return None
    side, stop, target = levels
    fill = bars[entry]["open"]
    risk = side * (fill - stop)
    if min(risk, side * (target - fill)) <= 0:
        return None

    trailing = state["regime"] != "range"
    for index in range(entry, entry + HOLD_BARS):
        bar = bars[index]
        opening = bar["open"]
        adverse = bar["low"] if side == 1 else bar["high"]
        favorable = bar["high"] if side == 1 else bar["low"]
        if side * (opening - stop) <= 0:
            price = opening
            break
        if side * (opening - target) >= 0:
            price = target
            break
        if side * (adverse - stop) <= 0:
            price = stop
            break
        if side * (favorable - target) >= 0:
            price = target
            break
        price = bar["close"]
        if trailing:
            # This close becomes available after all orders on this bar have been evaluated.
            next_stop = price - side * 2 * state["atr"]
            if side * (next_stop - stop) > 0:
                stop = next_stop
    cost = (fill + price) * cost_bps / 10_000
    return Trade(index, (side * (price - fill) - cost) / risk)


def _segment(bars: list[dict], states: list[dict], start: int, end: int,
             regime: str, variant: str, cost_bps: float) -> list[float]:
    results = []
    entry = max(1, start)
    while entry + HOLD_BARS <= end:
        state = states[entry - 1]
        if state["regime"] == regime:
            trade = _play(bars, entry, state, variant, cost_bps)
            if trade is not None:
                results.append(trade.net_r)
                entry = trade.exit_index + 1
                continue
        entry += 1
    return results


def _validate(bars: list[dict]) -> None:
    previous = None
    for bar in bars:
        at = bar["at"]
        if previous is not None and at <= previous:
            raise ValueError("daily bars must have unique timestamps in ascending order")
        previous = at
        opening, high, low, close = (bar[key] for key in ("open", "high", "low", "close"))
        if not all(isfinite(v) and v > 0 for v in (opening, high, low, close)):
            raise ValueError("OHLC prices must be finite and positive")
        if low > min(opening, close) or high < max(opening, close):
            raise ValueError("OHLC prices must lie within the candle range")


def evaluate(bars: list[dict], *, train_bars: int = 504, test_bars: int = 252,
             min_trades: int = MIN_TRADES, cost_bps: float = COST_BPS) -> dict:
    """Evaluate a single ticker. Each test fold starts flat after an expanding training window."""
    if min_trades < 1 or min(train_bars, test_bars) < HOLD_BARS or not isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("invalid fold size, trade minimum, or cost")
    _validate(bars)
    lookback = regime_live.COUNTS["1d"]
    states = [regime_live.classify(bars[max(0, end - lookback):end], "1d")
              for end in range(1, len(bars))]
    folds = []
    selected_results = {regime: [] for regime in REGIMES}
    selected_folds = dict.fromkeys(REGIMES, 0)
    comparisons = {regime: {variant: [] for variant in TARGETS} for regime in REGIMES}
    for start in range(train_bars, len(bars) - test_bars + 1, test_bars):
        end = start + test_bars
        rows = {}
        for regime in REGIMES:
            training = {v: metrics(_segment(bars, states, 0, start, regime, v, cost_bps)) for v in TARGETS}
            selected = max((v for v in TARGETS if _eligible(training[v], min_trades)),
                           key=lambda v: training[v]["expectancy_r"], default=None)
            testing = {v: _segment(bars, states, start, end, regime, v, cost_bps) for v in TARGETS}
            for variant, results in testing.items():
                comparisons[regime][variant].extend(results)
            if selected is not None and testing[selected]:
                selected_results[regime].extend(testing[selected])
                selected_folds[regime] += 1
            rows[regime] = {"selected": selected, "training": training,
                            "test": {v: metrics(rs) for v, rs in testing.items()}}
        folds.append({"test_from": bars[start]["at"], "test_through": bars[end - 1]["at"], "rows": rows})
    summary = {}
    for regime, results in selected_results.items():
        row = metrics(results)
        if row["n"] < min_trades or selected_folds[regime] < 2:
            status = "표본 부족"
        else:
            status = "기준 통과" if _eligible(row, min_trades) else "우위 없음"
        summary[regime] = {"status": status, "selected_folds": selected_folds[regime], "out_of_sample": row,
                           "variants": {v: metrics(rs) for v, rs in comparisons[regime].items()}}
    return {"train_bars": train_bars, "test_bars": test_bars, "min_trades": min_trades,
            "cost_bps_per_side": cost_bps, "folds": folds, "summary": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bars", type=Path, help="JSON array of completed daily OHLC bars for one ticker")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(json.loads(args.bars.read_text(encoding="utf-8")))
    report = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
