"""#695 how head and shoulders (desk.hs_pattern) actually resolved: walk past bars, take each pattern the
first time it is seen with the right shoulder done and the neckline not yet broken, and follow it until a
close through the neckline (the textbook way) or beyond the head (the other way). Then whether the break
failed (a close back over the neckline within FALSE_BARS) and whether the target (the head's height from the
neckline) was reached within REACH_BARS. The same walk on each ticker's shuffled returns is the baseline -
the same table and verdict as desk.patterns_bt.

    python -m desk.hs_bt [--out desk/data/hs_stats.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import zlib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import hs_pattern as hs
from . import patterns_bt
from .sr import _atr
from .trend import _swing_idx

FRAMES = {"60m": (3, 3400, 7), "1d": (3, 2600, 5), "1w": (2, 700, 2)}
FORWARD_MAX = 60
MOVE_BARS = 10
REACH_BARS = 30
FETCH_GAP_SEC = 1.0


def instances(bars: list[dict], timeframe: str, k: int, step: int, ticker: str = "") -> list[dict]:
    """Each pattern once, at its first sighting while forming, with what followed."""
    out, seen, busy = [], set(), {True: -1, False: -1}
    tried: dict[bool, tuple] = {True: (), False: ()}
    for t in range(8 * k + 20, len(bars) - 1, step):
        window = bars[max(0, t - hs.LOOKBACK + 1): t + 1]
        offset = t + 1 - len(window)
        atr = _atr(window)
        if not atr:
            continue
        for top in (True, False):
            if t <= busy[top]:
                continue
            # the same latest peaks as the last step: the same shapes were already tried (a new pattern needs a
            # new right shoulder) - skips most steps
            peaks = tuple(offset + i for i in _swing_idx(window, k, "high" if top else "low")[-(hs.RECENT_RS + hs.GAP_PEAKS + 1):])
            if peaks == tried[top]:
                continue
            tried[top] = peaks
            p = hs.best(window, k, atr, top)
            if not p:
                continue
            key = (top, offset + p["ls"], offset + p["head"], offset + p["rs"])
            if key in seen or hs.state_of(window, p)[0] != "forming":
                continue
            seen.add(key)
            out.append(_follow(bars, p, t, offset, atr, timeframe, ticker, top))
            busy[top] = t + FORWARD_MAX
    return out


def _follow(bars: list[dict], p: dict, t: int, offset: int, atr: float, timeframe: str, ticker: str,
            top: bool) -> dict:
    kind = "head_shoulders" if top else "inverse_head_shoulders"
    expected = hs.EXPECTED[kind]
    sign, tol = p["sign"], hs.BREAK_ATR * p["unit"]
    direction, at = None, None
    for x in range(t + 1, min(t + 1 + FORWARD_MAX, len(bars))):
        c = sign * math.log(bars[x]["close"])
        if c > p["head_y"]:
            direction, at = ("up" if expected == "down" else "down"), x
            break
        if c < hs._neck(p, x - offset) - tol:
            direction, at = expected, x
            break
    row = {"ticker": ticker, "at": bars[t]["at"][:10], "kind": kind, "timeframe": timeframe, "expected": expected,
           "direction": direction, "move_atr": None, "false_break": None, "prior": "none", "frac": None,
           "reach": None}
    if direction == expected:
        if at + MOVE_BARS < len(bars):
            row["move_atr"] = round(-sign * (bars[at + MOVE_BARS]["close"] - bars[at]["close"]) / atr, 2)
        back = bars[at + 1: at + 1 + hs.FALSE_BARS]          # as the live ``failed``: back over by BREAK_ATR
        row["false_break"] = any(sign * math.log(b["close"]) > hs._neck(p, x - offset) + tol
                                 for x, b in enumerate(back, start=at + 1))
        goal = hs._neck(p, at - offset) - p["height"]
        ahead = bars[at + 1: at + 1 + REACH_BARS]
        hit = any(sign * math.log(b[p["trough_f"]]) <= goal for b in ahead)
        row["reach"] = True if hit else (False if len(ahead) == REACH_BARS else None)
    return row


def table(rows: list[dict], null_rows: list[dict]) -> dict:
    """desk.patterns_bt's table (all, train / held-out, the shuffled baseline, the verdict) without the
    converging patterns' own rule columns."""
    out = patterns_bt.table(rows, null_rows)
    # the false-break share over the breaks the textbook way only (patterns_bt counts every break): a break
    # beyond the head has no neckline to fall back over
    for key, v in out.items():
        group = sorted((r for r in rows if f"{r['timeframe']}:{r['kind']}" == key), key=lambda r: r["at"])
        cut = int(len(group) * patterns_bt.TRAIN_SHARE)
        nulls = [r for r in null_rows if f"{r['timeframe']}:{r['kind']}" == key]
        v["false_break_pct"] = _false_share(group)
        for part, rs in (("train", group[:cut]), ("valid", group[cut:]), ("null", nulls)):
            if v.get(part):
                v[part]["false_break_pct"] = _false_share(rs)
    for v in out.values():
        for key in ("rules", "by_grade", "by_prior", "frac_q25", "frac_q50", "frac_q75", "late_pct", "pullback_pct",
                    "chase_n", "chase_win_pct", "chase_r_avg", "retest_n", "retest_win_pct", "retest_r_avg",
                    "reach1_pct", "extreme_reach_pct"):
            v.pop(key, None)
    return out


def _false_share(rows: list[dict]) -> float | None:
    got = [r["false_break"] for r in rows if r["direction"] == r["expected"] and r["false_break"] is not None]
    return round(100 * sum(got) / len(got), 1) if got else None


def run(tickers=patterns_bt.PATTERN_TICKERS, frames=FRAMES, progress=print) -> dict:
    from .prices import yahoo_candles

    rows, null_rows = [], []
    for timeframe, (k, count, step) in frames.items():
        for ticker in tickers:
            time.sleep(FETCH_GAP_SEC)
            try:
                bars = [asdict(b) for b in yahoo_candles(ticker, timeframe, count)]
            except Exception as exc:  # noqa: BLE001 - one ticker failing leaves the others
                if "429" in str(exc) or "Too Many" in str(exc):
                    raise RuntimeError(f"Yahoo rate limit at {ticker} {timeframe}: stopped") from None
                progress(f"{ticker} {timeframe}: skipped - {type(exc).__name__}: {exc}")
                continue
            got = instances(bars, timeframe, k, step, ticker)
            for s in range(patterns_bt.SHUFFLES):
                seed = zlib.crc32(f"hs:{ticker}:{timeframe}:{s}".encode())
                null_rows += instances(patterns_bt.shuffled(bars, seed), timeframe, k, step, ticker)
            progress(f"{ticker} {timeframe}: {len(bars)} bars, {len(got)} patterns")
            rows += got
    return {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "tickers": list(tickers),
            "frames": {tf: {"k": k, "bars": c, "step": s} for tf, (k, c, s) in frames.items()},
            "method": f"first sighting with the right shoulder done and the neckline whole; break = a close through "
                      f"the neckline by {hs.BREAK_ATR} ATR (expected) or beyond the head (the other way) within "
                      f"{FORWARD_MAX} bars; false break = a close back over the neckline by {hs.BREAK_ATR} ATR within "
                      f"{hs.FALSE_BARS} bars, over the textbook-way breaks only; "
                      f"reach = the head's height from the neckline at the break within {REACH_BARS} bars; "
                      f"null = the same walk on shuffled returns ({patterns_bt.SHUFFLES}x)",
            "kinds": table(rows, null_rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m desk.hs_bt")
    ap.add_argument("--out", default=str(hs.STATS_PATH))
    args = ap.parse_args(argv)
    result = run(progress=lambda m: print(m, file=sys.stderr, flush=True))
    with Path(args.out).open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    for key, s in result["kinds"].items():
        v, b = s.get("valid") or {}, s.get("null") or {}
        print(f"{key:30s} n={s['n']:4d} expected={s['expected_pct']}% false={s['false_break_pct']}% "
              f"reach={s.get('reach_pct')}% | held-out {v.get('expected_pct')}% null {b.get('expected_pct')}% | "
              f"{s['verdict']} {s['edge_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
