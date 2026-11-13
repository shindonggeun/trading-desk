"""Event study for major levels (#328): does the price bounce more at a major support than at the
desk's usual supports, and than at a random price the same distance away?

For each ticker, every STEP_WEEKS weeks, with only the bars finished by then:
- major supports (desk.sr_major.find on up to ten years of weekly bars), the desk's current
  supports (desk.sr.compute on 200 daily + 156 weekly bars) and, for every major one, a control
  zone of the same width about as far under the price (0.8 to 1.2 x its distance, not overlapping it);
- each is followed for FORWARD_DAYS daily bars: the first touch, then whether the price went
  REACT_ATR x ATR above the zone within REACT_BARS bars before closing THROUGH_ATR under it.

A zone counts once per first-touch day (the same level seen at several dates is one event).
Dates are split at the 70% point: train / held-out. Run: ``python -m desk.sr_major_bt``.
"""

from __future__ import annotations

import json
import math
import random
import sys
import zlib
from dataclasses import asdict
from pathlib import Path

from . import sr, sr_major
from .patterns_bt import TICKERS

STEP_WEEKS = 4
MIN_WEEKS = 156
FORWARD_DAYS = 120
REACT_BARS = 10
REACT_ATR = 1.0
THROUGH_ATR = 0.25
NEAR_ATR, FAR_ATR = 0.5, 6.0
OUT = Path(__file__).parent / "data" / "backtest" / "sr_major.json"


def _datr(days: list[dict], n: int = 14) -> float | None:
    return sr_major._atr(days, n)


def outcome(days: list[dict], d0: int, lo: float, hi: float, atr: float) -> tuple[str, bool] | None:
    """(touch date, bounced) for the first visit to [lo, hi] after day d0, None if none."""
    for j in range(d0 + 1, min(len(days), d0 + 1 + FORWARD_DAYS)):
        if days[j]["low"] > hi:
            continue
        for k in range(j, min(len(days), j + REACT_BARS)):
            if days[k]["close"] < lo - THROUGH_ATR * atr:
                return days[j]["at"][:10], False
            if k > j and days[k]["high"] >= hi + REACT_ATR * atr:
                return days[j]["at"][:10], True
        return (days[j]["at"][:10], False) if j + REACT_BARS <= len(days) else None
    return None


def events(ticker: str, weekly: list[dict], daily: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed)
    out, seen = [], set()
    di = 0
    for t in range(MIN_WEEKS, len(weekly), STEP_WEEKS):
        cutoff = weekly[t]["at"]
        while di + 1 < len(daily) and daily[di + 1]["at"] < cutoff:
            di += 1
        if di < 200 or daily[di]["at"] >= cutoff:
            continue
        days = daily[:di + 1]
        atr = _datr(days[-200:])
        if not atr:
            continue
        price = days[-1]["close"]
        weeks = weekly[max(0, t - 520):t]

        def near(lo: float, hi: float, price: float = price, atr: float = atr) -> bool:
            return price - NEAR_ATR * atr > hi and price - FAR_ATR * atr < lo

        groups: list[tuple[str, float, float]] = []
        for m in sr_major.find(weeks, price):
            lo, hi = m["zone"]
            if m["kind"] != "support" or not near(lo, hi):
                continue
            groups.append(("major", lo, hi))
            # a control zone of the same width about as far away (0.8 to 1.2 x the major's distance),
            # moved under the major when the two would overlap: distance decides the touch, not the zone
            half = (hi - lo) / 2
            dist = (price - (lo + hi) / 2) * rng.uniform(0.8, 1.2)
            c_lo, c_hi = price - dist - half, price - dist + half
            if c_lo <= hi and c_hi >= lo:
                c_lo, c_hi = lo - 2 * half - 0.1 * atr, lo - 0.1 * atr
            groups.append(("control", c_lo, c_hi))
        try:
            now = sr.compute(days[-200:], weeks[-156:], None, weekly_complete=True, horizon="swing")
            for lv in now["levels"]:
                lo, hi = lv["zone"]
                if lv["kind"] == "support" and near(lo, hi):
                    groups.append(("current", lo, hi))
        except ValueError:
            pass
        for group, lo, hi in groups:
            got = outcome(daily, di, lo, hi, atr)
            if got is None:
                continue
            key = (group, got[0], round(lo / atr))
            if key in seen:
                continue
            seen.add(key)
            out.append({"ticker": ticker, "group": group, "at": days[-1]["at"][:10], "touch": got[0],
                        "bounced": got[1], "dist_atr": round((price - hi) / atr, 2)})
    return out


def table(rows: list[dict], cut: str) -> dict:
    out = {}
    for group in ("major", "current", "control"):
        out[group] = {}
        for half, pick in (("train", lambda r: r["touch"] < cut), ("valid", lambda r: r["touch"] >= cut)):
            xs = [r["bounced"] for r in rows if r["group"] == group and pick(r)]
            n = len(xs)
            rate = sum(xs) / n if n else None
            se = math.sqrt(rate * (1 - rate) / n) if n else None
            out[group][half] = {"n": n, "bounce_pct": round(100 * rate, 1) if rate is not None else None,
                                "se_pct": round(100 * se, 1) if se is not None else None,
                                "mean_dist_atr": round(sum(r["dist_atr"] for r in rows if r["group"] == group and pick(r)) / n, 2) if n else None}
    return out


def verdict(t: dict) -> dict:
    """Major beats the random control and the current supports on the held-out half by 2 SE."""
    m, c, cur = t["major"]["valid"], t["control"]["valid"], t["current"]["valid"]
    def beats(a, b):
        if not a["n"] or not b["n"]:
            return None
        se = math.hypot(a["se_pct"], b["se_pct"])
        return a["bounce_pct"] - b["bounce_pct"] > 2 * se
    return {"beats_control": beats(m, c), "beats_current": beats(m, cur),
            "train_beats_control": (t["major"]["train"]["bounce_pct"] or 0) > (t["control"]["train"]["bounce_pct"] or 0)}


def run(tickers=TICKERS, fetch=None, progress=None) -> dict:
    from .prices import yahoo_candles

    fetch = fetch or (lambda t, iv, n: [asdict(b) for b in yahoo_candles(t, iv, n)])
    rows = []
    for t in tickers:
        try:
            weekly, daily = fetch(t, "1w", 1000), fetch(t, "1d", 5000)
        except Exception as exc:  # noqa: BLE001 - one ticker's data does not stop the study
            if progress:
                progress(f"{t}: {type(exc).__name__}")
            continue
        got = events(t, weekly[:-1], daily, zlib.crc32(t.encode()))
        rows += got
        if progress:
            progress(f"{t}: {len(got)} events")
    touches = sorted(r["touch"] for r in rows)
    cut = touches[int(len(touches) * 0.7)] if touches else ""
    t = table(rows, cut)
    return {"tickers": list(tickers), "cut": cut, "step_weeks": STEP_WEEKS, "forward_days": FORWARD_DAYS,
            "react": {"bars": REACT_BARS, "atr": REACT_ATR, "through_atr": THROUGH_ATR},
            "distance_atr": [NEAR_ATR, FAR_ATR], "table": t, "verdict": verdict(t)}


def main() -> int:
    rep = run(progress=lambda m: print(m, file=sys.stderr))
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps({"cut": rep["cut"], "table": rep["table"], "verdict": rep["verdict"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
