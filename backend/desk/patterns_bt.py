"""How converging patterns (desk.patterns) actually broke (#297): walk past bars, take each pattern
the first time it is seen still converging, follow its two lines forward and record which side
the close broke first, how far the move went in the next bars, and whether it fell back inside
(a false break). Split by date into train (first 70%) and held-out, per timeframe and kind.

#629 also: where between the pattern's start and its apex the break came (``frac``: its quartiles
make the breakout window desk.patterns shows), whether the measured move - the starting height added
at the break - was reached within REACH_BARS (``reach``), and the shares by the trend before.

    python -m desk.patterns_bt [--out desk/data/pattern_stats.json]

Writes the table desk.patterns reads (``stats``). Yahoo bars, no key.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import zlib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import patterns
from .sr import _atr

TICKERS = ("SOXL", "TQQQ", "NVDA", "QQQ", "SPY", "IWM", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
           "AMD", "AVGO", "MU", "005930.KS", "000660.KS", "035420.KS", "005380.KS", "035720.KS")
# #629 the patterns are also measured on the index futures (projection_bt / sr_major_bt keep TICKERS)
PATTERN_TICKERS = (*TICKERS, "NQ=F", "ES=F")
# timeframe -> (swing k as the horizons use it, bars to fetch, plan every N bars). #629 the intraday
# frames: Yahoo keeps 5m / 15m about 60 days, 60m (and the 240m folded from it) about two years
FRAMES = {"1d": (3, 2600, 5), "1w": (2, 700, 2), "60m": (3, 3400, 7), "5m": (3, 16000, 12),
          "15m": (3, 5400, 8), "240m": (2, 1500, 3)}
FORWARD_MAX = 60           # bars a pattern is followed for a break
MOVE_BARS = 10             # bars after the break the move is measured over
FALSE_BARS = 5             # a close back inside within this many bars = a false break
FETCH_GAP_SEC = 1.0        # between two Yahoo reads
REACH_BARS = 30            # the measured move counts as reached within this many bars of the break
TRAIN_SHARE = 0.7
MIN_N = 20                 # fewer instances than this: no stats (weak evidence)
# The break side is mostly geometry (the line closing in faster is met first): the same walk on each
# ticker's own returns shuffled is the baseline, and only the difference from it is evidence.
SHUFFLES = 3
EDGE_PCT = 5.0             # held-out expected-way share at least this far from the shuffled one
UNSTABLE_PCT = 10.0        # train and held-out further apart than this: unstable


def shuffled(bars: list[dict], seed: int) -> list[dict]:
    """The same bars with their close-to-close returns in random order (each bar keeps its own
    open / high / low around its close) - the volatility stays, the order of moves does not."""
    rng = random.Random(seed)
    rets = [bars[i]["close"] / bars[i - 1]["close"] for i in range(1, len(bars))]
    rng.shuffle(rets)
    out, close = [dict(bars[0])], bars[0]["close"]
    for b, r in zip(bars[1:], rets, strict=True):
        close *= r
        f = close / b["close"]
        out.append({**b, "open": b["open"] * f, "high": b["high"] * f, "low": b["low"] * f, "close": close})
    return out


def instances(bars: list[dict], timeframe: str, k: int, step: int, ticker: str = "") -> list[dict]:
    """Each pattern once, at its first sighting while converging, with what followed."""
    out, seen, busy_until = [], set(), -1
    for t in range(6 * k + 20, len(bars) - 1, step):
        if t <= busy_until:                            # the last pattern is still open: its variants are not new
            continue
        window = bars[max(0, t - patterns.LOOKBACK + 1): t + 1]
        offset = t + 1 - len(window)
        atr = _atr(window)
        got = patterns.best(window, k, atr or 0.0)
        if not got:
            continue
        up, low, start, apex, kind, unit = got
        key = (kind, offset + min(up.touches), offset + min(low.touches))
        if key in seen:
            continue
        last = len(window) - 1
        close = math.log(window[-1]["close"])
        tol = patterns.BREAK_ATR * unit
        if not low.at(last) - tol <= close <= up.at(last) + tol:
            continue                                   # already broken when first seen: not a fresh setup
        seen.add(key)
        expected = patterns.EXPECTED.get(kind) or patterns._prior_trend(window, start)
        horizon = min(int(apex - last) + 5, FORWARD_MAX)
        direction, at = None, None
        prior = patterns._prior_trend(window, start)
        height = up.at(start) - low.at(start)
        for x in range(t + 1, min(t + 1 + horizon, len(bars))):
            local = x - offset
            if local >= apex:                          # past the apex the lines have crossed: no break
                break
            c = math.log(bars[x]["close"])
            if c > up.at(local) + tol:
                direction, at = "up", x
                break
            if c < low.at(local) - tol:
                direction, at = "down", x
                break
        busy_until = at if at is not None else t + horizon
        row = {"ticker": ticker, "at": window[-1]["at"][:10], "kind": kind, "timeframe": timeframe,
               "expected": expected, "direction": direction, "move_atr": None, "false_break": None,
               "prior": prior, "frac": None, "reach": None}
        if direction and atr:
            sign = 1 if direction == "up" else -1
            if at + MOVE_BARS < len(bars):
                row["move_atr"] = round(sign * (bars[at + MOVE_BARS]["close"] - bars[at]["close"]) / atr, 2)
            back = [bars[x]["close"] for x in range(at + 1, min(at + 1 + FALSE_BARS, len(bars)))]
            line = up if direction == "up" else low
            row["false_break"] = any(sign * (math.log(c) - line.at(x - offset)) < 0
                                     for x, c in zip(range(at + 1, at + 1 + len(back)), back, strict=True))
            row["frac"] = round((at - offset - start) / (apex - start), 3)
            goal = line.at(at - offset) + sign * height             # the starting height added at the break
            ahead = bars[at + 1: at + 1 + REACH_BARS]
            hit = any(sign * (math.log(b["high"] if sign > 0 else b["low"]) - goal) >= 0 for b in ahead)
            row["reach"] = True if hit else (False if len(ahead) == REACH_BARS else None)   # None: too recent
        out.append(row)
    return out


def _quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    return round(xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (pos - lo), 3)


def summarize(rows: list[dict], detail: bool = True) -> dict | None:
    if len(rows) < MIN_N:
        return None
    broke = [r for r in rows if r["direction"]]
    moves = [r["move_atr"] for r in broke if r["move_atr"] is not None]
    pct = lambda part, whole: round(100 * part / whole, 1) if whole else None
    out = {"n": len(rows), "up_pct": pct(sum(r["direction"] == "up" for r in rows), len(rows)),
           "down_pct": pct(sum(r["direction"] == "down" for r in rows), len(rows)),
           "none_pct": pct(len(rows) - len(broke), len(rows)),
           "expected_pct": pct(sum(r["direction"] == r["expected"] for r in broke), len(broke)),
           "false_break_pct": pct(sum(bool(r["false_break"]) for r in broke), len(broke)),
           "move_atr_avg": round(sum(moves) / len(moves), 2) if moves else None}
    if detail:
        fracs = [r["frac"] for r in broke if r.get("frac") is not None]
        reached = [r["reach"] for r in broke if r.get("reach") is not None]
        out.update({"frac_q25": _quantile(fracs, 0.25), "frac_q50": _quantile(fracs, 0.5),
                    "frac_q75": _quantile(fracs, 0.75), "reach_pct": pct(sum(reached), len(reached)),
                    "reach_n": len(reached)})
        by = {}
        for prior in ("up", "down"):
            got = summarize([r for r in rows if r.get("prior") == prior], detail=False)
            if got:
                by[prior] = {k: got[k] for k in ("n", "up_pct", "down_pct", "none_pct")}
        out["by_prior"] = by
    return out


def _verdict(s: dict, null: dict | None) -> dict:
    """edge: held-out expected-way share minus the shuffled baseline's, and what to say about it."""
    valid, train = s.get("valid") or {}, s.get("train") or {}
    v, b = valid.get("expected_pct"), (null or {}).get("expected_pct")
    edge = round(v - b, 1) if v is not None and b is not None else None
    unstable = (v is not None and train.get("expected_pct") is not None
                and abs(v - train["expected_pct"]) > UNSTABLE_PCT)
    # two standard errors of the difference of two shares: a smaller gap is sampling noise
    need = EDGE_PCT
    if edge is not None and valid.get("n") and (null or {}).get("n"):
        p1, p0 = v / 100, b / 100
        se = math.sqrt(p1 * (1 - p1) / valid["n"] + p0 * (1 - p0) / null["n"])
        need = max(EDGE_PCT, round(200 * se, 1))
    verdict = ("unmeasured" if edge is None else "unstable" if unstable
               else "edge" if abs(edge) >= need else "no_edge")
    return {"edge_pct": edge, "edge_needed_pct": need if edge is not None else None, "verdict": verdict}


def table(rows: list[dict], null_rows: list[dict] | None = None) -> dict:
    """Per "timeframe:kind": all, train and held-out (split by date), the shuffled baseline and
    the verdict on the held-out difference from it."""
    out = {}
    for key in sorted({f"{r['timeframe']}:{r['kind']}" for r in rows}):
        group = sorted((r for r in rows if f"{r['timeframe']}:{r['kind']}" == key), key=lambda r: r["at"])
        cut = int(len(group) * TRAIN_SHARE)
        whole = summarize(group)
        if whole:
            null = summarize([r for r in null_rows or [] if f"{r['timeframe']}:{r['kind']}" == key])
            entry = {**whole, "train": summarize(group[:cut]), "valid": summarize(group[cut:]),
                     "cut": group[cut]["at"] if cut < len(group) else None, "null": null}
            out[key] = {**entry, **_verdict(entry, null)}
    return out


def run(tickers=PATTERN_TICKERS, frames=FRAMES, progress=print) -> dict:
    from .prices import yahoo_candles

    rows, null_rows = [], []
    for timeframe, (k, count, step) in frames.items():
        for ticker in tickers:
            time.sleep(FETCH_GAP_SEC)                  # a polite gap between Yahoo reads
            try:
                bars = [asdict(b) for b in yahoo_candles(ticker, timeframe, count)]
            except Exception as exc:  # noqa: BLE001 - one ticker failing leaves the others
                if "429" in str(exc) or "Too Many" in str(exc):
                    raise RuntimeError(f"Yahoo rate limit at {ticker} {timeframe}: stopped") from None
                progress(f"{ticker} {timeframe}: skipped - {type(exc).__name__}: {exc}")
                continue
            got = instances(bars, timeframe, k, step, ticker)
            for s in range(SHUFFLES):
                null_rows += instances(shuffled(bars, zlib.crc32(f"{ticker}:{timeframe}:{s}".encode())), timeframe, k, step, ticker)
            progress(f"{ticker} {timeframe}: {len(bars)} bars, {len(got)} patterns")
            rows += got
    return {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "tickers": list(tickers),
            "frames": {tf: {"k": k, "bars": c, "step": s} for tf, (k, c, s) in frames.items()},
            "method": f"first sighting while converging; break = close beyond a line by {patterns.BREAK_ATR} ATR "
                      f"within apex+5 bars (max {FORWARD_MAX}); move over {MOVE_BARS} bars in ATR; false break = "
                      f"a close back inside within {FALSE_BARS} bars; frac = the break's place between the start and "
                      f"the apex; reach = the starting height added at the break touched within {REACH_BARS} bars; "
                      f"train = first {int(TRAIN_SHARE * 100)}% by date; "
                      f"null = the same walk on each ticker's returns shuffled ({SHUFFLES}x); edge = held-out "
                      f"expected-way share minus the null's, {EDGE_PCT} points or more to count",
            "kinds": table(rows, null_rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m desk.patterns_bt")
    ap.add_argument("--out", default=str(patterns.STATS_PATH))
    args = ap.parse_args(argv)
    result = run(progress=lambda m: print(m, file=sys.stderr, flush=True))
    with Path(args.out).open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    for key, s in result["kinds"].items():
        v, b = s.get("valid") or {}, s.get("null") or {}
        print(f"{key:24s} n={s['n']:4d} up={s['up_pct']} down={s['down_pct']} expected={s['expected_pct']}% "
              f"move={s['move_atr_avg']} | held-out n={v.get('n')} expected={v.get('expected_pct')}% | "
              f"null expected={b.get('expected_pct')}% move={b.get('move_atr_avg')} | {s['verdict']} {s['edge_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
