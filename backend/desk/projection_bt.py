"""Calibrating desk.projection on past bars (#299).

- Cone: at every few bars, how far the close moved after 1/4, 1/2 and all of the horizon, in units
  of the volatility times the square root of the bars. The training years' 68th and 95th
  percentiles become the band multipliers; the held-out years report how often the bands held.
- First touch (daily): at every few bars, the desk's levels (desk.sr.compute on the bars up to
  then), the walk's probabilities of meeting the level above first, the one below first, or neither
  within the horizon - against what the closes did. Brier score against the training base rates.

    python -m desk.projection_bt [--out desk/data/projection_calibration.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import horizons, projection, sr
from .patterns_bt import TICKERS

FRAMES = {"1d": (2600, 5), "1w": (700, 2), "60m": (3400, 7)}   # bars fetched, sample every N bars
TOUCH_STEP = 10            # daily bars between first-touch samples (each computes the levels)
TOUCH_SIMS = 400
TRAIN_SHARE = 0.7
# held-out samples this many calendar days after the cut are dropped: a training sample's look-ahead
# (up to the horizon) must not reach into the held-out years
GAP_DAYS = {"1d": 35, "1w": 100, "60m": 10}


def _q(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def cone_samples(bars: list[dict], timeframe: str, step: int) -> list[tuple[str, float]]:
    steps = projection.STEPS.get(timeframe, 20)
    out = []
    for t in range(projection.SIGMA_BARS + 1, len(bars) - steps, step):
        sig = projection.sigma(bars[: t + 1])
        if not sig:
            continue
        for j in sorted({max(1, steps // 4), max(1, steps // 2), steps}):
            z = abs(math.log(bars[t + j]["close"] / bars[t]["close"])) / (sig * math.sqrt(j))
            out.append((bars[t]["at"][:10], z))
    return out


def cone_table(samples: list[tuple[str, float]], gap_days: int = 0) -> dict:
    """Samples overlap (every few bars, each looking the horizon ahead): the effective sample is
    smaller than ``n``. Coverage pools 1/4, 1/2 and all of the horizon."""
    samples.sort()
    cut = int(len(samples) * TRAIN_SHARE)
    start = (datetime.fromisoformat(samples[cut][0]) + timedelta(days=gap_days)).date().isoformat() if cut < len(samples) else ""
    train, valid = [z for _, z in samples[:cut]], [z for d, z in samples[cut:] if d >= start]
    m68, m95 = _q(train, 0.68), (_q(train, 0.95) or 0) / projection.Z95
    share = lambda zs, lim: round(100 * sum(z <= lim for z in zs) / len(zs), 1) if zs else None
    return {"n": len(samples), "cut": samples[cut][0] if cut < len(samples) else None,
            "mult68": round(m68, 3) if m68 else 1.0, "mult95": round(m95, 3) if m95 else 1.0,
            "raw_coverage68_valid": share(valid, 1.0), "raw_coverage95_valid": share(valid, projection.Z95),
            "coverage68_valid": share(valid, m68 or 1.0), "coverage95_valid": share(valid, projection.Z95 * (m95 or 1.0))}


def touch_samples(days: list[dict], weeks: list[dict], mult68: float) -> list[dict]:
    """The predicted first-touch split against the closes that followed."""
    steps = projection.STEPS["1d"]
    out = []
    swing = horizons.get(None)
    for t in range(200, len(days) - steps, TOUCH_STEP):
        window = days[t - 199: t + 1]                 # 200 daily bars, as the live swing frame
        cut = datetime.fromisoformat(days[t]["at"]) - timedelta(days=7)
        sec = [w for w in weeks if datetime.fromisoformat(w["at"]) <= cut][-60:]
        try:
            levels = sr.compute(window, sec, None, weekly_complete=True, horizon="swing")
        except ValueError:
            continue
        sr.add_patterns(levels, window, sec, swing)   # the live levels carry the pattern lines too
        price, sig = days[t]["close"], projection.sigma(days[: t + 1])
        if not sig:
            continue
        above, below = projection._levels(levels, price, projection.MIN_GAP * mult68 * sig * math.sqrt(steps))
        if not above or not below:
            continue
        up, down = above[0]["price"], below[0]["price"]
        p = projection.first_touch(price, up, down, sig, steps, sims=TOUCH_SIMS, seed=t, scale=mult68)
        real = "none"
        for x in range(t + 1, t + 1 + steps):
            c = days[x]["close"]
            if c >= up:
                real = "up"
                break
            if c <= down:
                real = "down"
                break
        out.append({"at": days[t]["at"][:10], "p": p, "real": real})
    return out


def touch_table(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    rows.sort(key=lambda r: r["at"])
    cut = int(len(rows) * TRAIN_SHARE)
    train, valid = rows[:cut], rows[cut:]
    keys = ("up", "down", "none")
    base = {k: sum(r["real"] == k for r in train) / len(train) for k in keys}
    brier = lambda rs, pred: sum(sum((pred(r)[k] - (r["real"] == k)) ** 2 for k in keys) for r in rs) / len(rs)
    buckets = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        hit = [r for r in valid if lo <= r["p"]["up"] < lo + 0.2 or (lo == 0.8 and r["p"]["up"] == 1.0)]
        if hit:
            buckets.append({"p_up": f"{lo:.1f}-{lo + 0.2:.1f}", "n": len(hit),
                            "said": round(100 * sum(r["p"]["up"] for r in hit) / len(hit), 1),
                            "was": round(100 * sum(r["real"] == "up" for r in hit) / len(hit), 1)})
    return {"n": len(rows), "n_valid": len(valid), "cut": valid[0]["at"] if valid else None,
            "brier_valid": round(brier(valid, lambda r: r["p"]), 4) if valid else None,
            "brier_base_rates_valid": round(brier(valid, lambda r: base), 4) if valid else None,
            "base_rates_train": {k: round(100 * v, 1) for k, v in base.items()},
            "reliability_valid": buckets}


def run(progress=print) -> dict:
    from .prices import yahoo_candles

    frames, touch_rows = {}, []
    weekly: dict[str, list[dict]] = {}
    for timeframe, (count, step) in FRAMES.items():
        samples = []
        for ticker in TICKERS:
            try:
                bars = [asdict(b) for b in yahoo_candles(ticker, timeframe, count)]
            except Exception as exc:  # noqa: BLE001
                progress(f"{ticker} {timeframe}: skipped - {type(exc).__name__}")
                continue
            samples += cone_samples(bars, timeframe, step)
            if timeframe == "1w":
                weekly[ticker] = bars
            progress(f"{ticker} {timeframe}: {len(bars)} bars")
        frames[timeframe] = cone_table(samples, GAP_DAYS.get(timeframe, 0))
    mult68 = frames["1d"]["mult68"]
    for ticker in TICKERS:
        try:
            days = [asdict(b) for b in yahoo_candles(ticker, "1d", FRAMES["1d"][0])]
        except Exception as exc:  # noqa: BLE001
            progress(f"{ticker} first touch: skipped - {type(exc).__name__}")
            continue
        touch_rows += touch_samples(days, weekly.get(ticker) or [], mult68)
        progress(f"{ticker} first touch: {len(touch_rows)} samples so far")
    frames["1d"]["first_touch"] = touch_table(touch_rows)
    return {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "tickers": list(TICKERS),
            "method": "cone: |log move| / (sigma x sqrt bars) at 1/4, 1/2 and all of the horizon, multipliers from the "
                      "training 70% by date; first touch: desk levels then, a driftless walk's split vs the closes",
            "frames": frames}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m desk.projection_bt")
    ap.add_argument("--out", default=str(projection.CAL_PATH))
    args = ap.parse_args(argv)
    result = run(progress=lambda m: print(m, file=sys.stderr, flush=True))
    with Path(args.out).open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(result["frames"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
