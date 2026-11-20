"""#695 what followed each Minsky-style stage (desk.bubble_stage) on past weekly bars: for every week the
stage is read on the bars up to it; the week a stage begins (a run's first week) is a case. For the cases
of each stage: the returns 13, 26 and 52 weeks on, and how often the low fell 30% or more under that week's
close within 52 weeks (``crash30_pct``) - against every week of the same tickers (``base_*``), the share a
random week would show. A shuffled-price baseline would not do here: shuffled returns rarely draw a
parabola at all. Train (the first 70% of cases by date) and held-out must agree for an edge.

The tickers are today's (they survived): a stage's record is biased toward bubbles that recovered.

    python -m desk.bubble_bt [--out desk/data/bubble_stats.json]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import bubble_stage as bs
from . import patterns_bt

WEEKS = 1300               # about 25 years where Yahoo has them
HORIZONS = (13, 26, 52)
CRASH = -0.30
TRAIN_SHARE = 0.7
EDGE_PCT = 5.0
UNSTABLE_PCT = 10.0
FETCH_GAP_SEC = 1.0
REPEAT_WEEKS = 13          # a stage begun again within this many weeks of its last start is the same case
                           # (a reading flickering on its threshold)


def walk(weeks: list[dict], ticker: str = "") -> list[dict]:
    """Every week's stage and what followed it."""
    out, prev, began = [], None, {}
    lows = [b["low"] for b in weeks]
    closes = [b["close"] for b in weeks]
    for t in range(bs.MIN_WEEKS - 1, len(weeks)):
        n = bs.numbers(weeks[:t + 1])
        if n is None:
            continue
        st = bs.classify(n)
        start = st != prev and t - began.get(st, -REPEAT_WEEKS - 1) > REPEAT_WEEKS
        if st != prev:
            began[st] = t
        row = {"ticker": ticker, "at": weeks[t]["at"][:10], "stage": st, "start": start}
        for h in HORIZONS:
            row[f"r{h}"] = round(closes[t + h] / closes[t] - 1, 4) if t + h < len(weeks) else None
        row["crash30"] = (min(lows[t + 1: t + 53]) / closes[t] - 1 <= CRASH) if t + 52 < len(weeks) else None
        out.append(row)
        prev = st
    return out


def _pct(xs: list[bool]) -> float | None:
    return round(100 * sum(xs) / len(xs), 1) if xs else None


def _summary(rows: list[dict]) -> dict:
    out: dict = {"n": len(rows)}
    for h in HORIZONS:
        xs = sorted(r[f"r{h}"] for r in rows if r[f"r{h}"] is not None)
        out[f"r{h}_median_pct"] = round(100 * statistics.median(xs), 1) if xs else None
    r52 = sorted(r["r52"] for r in rows if r["r52"] is not None)
    if len(r52) >= 4:
        q = statistics.quantiles(r52, n=4)
        out["r52_q25_pct"], out["r52_q75_pct"] = round(100 * q[0], 1), round(100 * q[2], 1)
    crashes = [r["crash30"] for r in rows if r["crash30"] is not None]
    out["crash30_pct"], out["crash30_n"] = _pct(crashes), len(crashes)
    return out


def table(rows: list[dict]) -> dict:
    """Per stage: its cases' record, every week's (the base), the difference and the verdict."""
    base = _summary(rows)
    out = {}
    for st in bs.STAGE_KO:
        cases = sorted((r for r in rows if r["start"] and r["stage"] == st), key=lambda r: r["at"])
        if not cases:
            continue
        s = _summary(cases)
        cut = int(len(cases) * TRAIN_SHARE)
        train, valid = _summary(cases[:cut]), _summary(cases[cut:])
        s.update(base_crash30_pct=base["crash30_pct"], base_r52_median_pct=base["r52_median_pct"],
                 train_crash30_pct=train["crash30_pct"], valid_crash30_pct=valid["crash30_pct"])
        edge, need, verdict = None, None, "unmeasured"
        if s["crash30_pct"] is not None and base["crash30_pct"] is not None and s["crash30_n"]:
            edge = round(s["crash30_pct"] - base["crash30_pct"], 1)
            p1, p0 = s["crash30_pct"] / 100, base["crash30_pct"] / 100
            se = math.sqrt(p1 * (1 - p1) / s["crash30_n"] + p0 * (1 - p0) / max(1, base["crash30_n"]))
            need = max(EDGE_PCT, round(200 * se, 1))
            unstable = (train["crash30_pct"] is not None and valid["crash30_pct"] is not None
                        and abs(train["crash30_pct"] - valid["crash30_pct"]) > UNSTABLE_PCT)
            verdict = "unstable" if unstable else "edge" if abs(edge) >= need else "no_edge"
        s.update(edge_pct=edge, edge_needed_pct=need, verdict=verdict)
        out[st] = s
    return out


def run(tickers=patterns_bt.PATTERN_TICKERS, progress=print) -> tuple[dict, dict]:
    """The table, and every ticker's stage now (for the PR / the docs)."""
    from .prices import yahoo_candles

    rows, now = [], {}
    for ticker in tickers:
        time.sleep(FETCH_GAP_SEC)
        try:
            weeks = [asdict(b) for b in yahoo_candles(ticker, "1w", WEEKS)]
        except Exception as exc:  # noqa: BLE001
            if "429" in str(exc) or "Too Many" in str(exc):
                raise RuntimeError(f"Yahoo rate limit at {ticker}: stopped") from None
            progress(f"{ticker}: skipped - {type(exc).__name__}: {exc}")
            continue
        got = walk(weeks[:-1], ticker)             # the last weekly bar is the week still trading
        rows += got
        n = bs.numbers(weeks[:-1])
        if n:
            now[ticker] = {"stage": bs.classify(n), **{k: n[k] for k in (
                "dist_40w_pct", "accel", "drawdown_pct", "peak_dist_40w_pct", "drop_pct", "retrace_pct")}}
        progress(f"{ticker}: {len(weeks)} weeks, {len(got)} readings")
    result = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "tickers": list(tickers),
              "method": f"each finished week's stage on the weeks up to it; a case = a stage's first week; returns "
                        f"{'/'.join(map(str, HORIZONS))} weeks on; crash30 = a low {int(-CRASH * 100)}% or more under "
                        f"the close within 52 weeks; base = every week of the same tickers; edge = the stage's "
                        f"crash30 share minus the base's, two standard errors and {EDGE_PCT} points to count; "
                        f"train/held-out {int(TRAIN_SHARE * 100)}/{100 - int(TRAIN_SHARE * 100)} by date must agree "
                        f"within {UNSTABLE_PCT} points; survivors only (today's tickers)",
              "stages": table(rows)}
    return result, now


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m desk.bubble_bt")
    ap.add_argument("--out", default=str(bs.STATS_PATH))
    ap.add_argument("--now", default=None, help="also write every ticker's stage now to this JSON file")
    args = ap.parse_args(argv)
    result, now = run(progress=lambda m: print(m, file=sys.stderr, flush=True))
    with Path(args.out).open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    if args.now:
        Path(args.now).write_text(json.dumps(now, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for st, s in result["stages"].items():
        print(f"{st:12s} n={s['n']:4d} r13={s['r13_median_pct']} r26={s['r26_median_pct']} r52={s['r52_median_pct']} "
              f"crash30={s['crash30_pct']}% (base {s['base_crash30_pct']}%) | {s['verdict']} {s['edge_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
