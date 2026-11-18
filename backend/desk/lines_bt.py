"""How often the channels' and short trendlines' lines held (#611) - a walk-forward backtest.

At every STEP-th bar the lines are found on the WINDOW bars up to it (desk.channels, nothing after it),
then the next bars are watched:

- a **touch**: within HORIZON bars the price reaches a line it started on the right side of (a low at
  the lower channel line or a rising support, a high at the upper line or a falling resistance), the
  line read where it is on that bar;
- after the touch, within HORIZON bars, a **bounce** is a close BOUNCE_ATR (the window's ATR) away from
  the line on the side it started, a **break** a close BREAK_ATR beyond it - whichever comes first
  (neither: "none");
- a short trendline that broke on the window's last bar: does the price go on BOUNCE_ATR the way it broke
  before closing back over the line ("follow")?
- the **baseline** of each is a line of the same slope and the same distance from the price (both in the
  step's ATR) set at another step drawn at random, read the same way: how often any line so placed would
  have "held" (or been followed). A line is worth drawing by how far it beats that; on a random walk the
  two agree (tests/test_channels.py).

``python -m desk.lines_bt`` reads Yahoo (NQ=F 5m ~60 days, 60m ~2 years, 1d since 2000) and writes
OUT, which /api/lines reads for each line's past rate. Past rates, not a forecast.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import channels

OUT = Path(__file__).parent / "data" / "backtest" / "lines.json"
HORIZON = channels.HORIZON
BOUNCE_ATR = 1.0
FETCH = {"5m": 16000, "15m": 6000, "60m": 12000, "240m": 3000, "1d": 6500}
DEFAULT_TFS = ("5m", "60m", "1d")
ROLES = ("channel_lower", "channel_upper", "trend_support", "trend_resistance")


def _touch(future: list[dict], n: int, f, sign: int, unit: float, horizon: int) -> str | None:
    """None without a touch; else bounce · break · none. ``f(x)`` is the line's log price at bar x
    (the window's last bar is n - 1, ``future[h]`` is bar n + h); sign +1 for a line below the price."""
    touch, brk = channels.TOUCH_ATR * unit, channels.BREAK_ATR * unit
    for h in range(min(horizon, len(future))):
        b = future[h]
        y = f(n + h)
        hit = math.log(b["low"]) <= y + touch if sign > 0 else math.log(b["high"]) >= y - touch
        if not hit:
            continue
        for g in range(h, min(h + horizon, len(future))):
            gap = sign * (math.log(future[g]["close"]) - f(n + g))
            if gap < -brk:
                return "break"
            if gap >= BOUNCE_ATR * unit:
                return "bounce"
        return "none"
    return None


def _run_away(future: list[dict], start: float, sign: int, unit: float, horizon: int,
              back=None, n: int = 0) -> bool | None:
    """Did the close go BOUNCE_ATR the ``sign`` way from ``start`` first? ``back(x)``: a line whose
    close back over it ends the run (else BOUNCE_ATR the other way does). None: neither in time."""
    for h in range(min(horizon, len(future))):
        move = sign * (math.log(future[h]["close"]) - start)
        if move >= BOUNCE_ATR * unit:
            return True
        if back is not None:
            if sign * (math.log(future[h]["close"]) - back(n + h)) < 0:     # closed back over the line
                return False
        elif move <= -BOUNCE_ATR * unit:
            return False
    return None


def _tally() -> dict:
    return {"n": 0, "bounce": 0, "break": 0, "none": 0}


def _line(close: float, unit: float, side: int, dist: float, slope: float, n: int):
    """A line ``dist`` ATR on the ``side`` (-1 below, +1 above) of ``close`` at bar n - 1, rising
    ``slope`` ATR a bar (all in log price, ``unit`` = one ATR)."""
    y0 = close + side * dist * unit
    return lambda x: y0 + slope * unit * (x - (n - 1))


def backtest(bars: list[dict], tf: str, step: int | None = None, seed: int = 0) -> dict:
    """The rates over ``bars`` (oldest first) of timeframe ``tf``. Each line's baseline is a line of the
    same slope and distance (in ATR) from the price, set at another step drawn at random (``seed``)."""
    bars = [b for b in bars if b["low"] > 0 and b["close"] > 0 and b["high"] >= b["low"]]
    window, horizon = channels.BARS[tf], HORIZON[tf]
    step = step or horizon
    n = window
    rng = random.Random(seed)
    at: list[tuple[int, int, float, float]] = []       # (index, t, unit, log close) of every step
    cases: list[tuple[str, int, int, float, float]] = []   # role, step, sign, distance ATR, slope ATR/bar
    breaks: list[tuple[int, int, float, float]] = []       # step, way, distance ATR past the line, slope
    for t in range(window, len(bars) - horizon, step):
        past = bars[t - window:t]
        a = channels.atr(past)
        if not a:
            continue
        unit = a / past[-1]["close"]
        close = math.log(past[-1]["close"])
        at.append((len(at), t, unit, close))
        found_channels, found_lines = channels.pick(past, tf, unit, a)
        for c in found_channels:
            cases += [("channel_lower", len(at) - 1, 1, (close - c.lower(n - 1)) / unit, c.slope / unit),
                      ("channel_upper", len(at) - 1, -1, (c.upper(n - 1) - close) / unit, c.slope / unit)]
        for ln in found_lines:
            sign = 1 if ln.kind == "support" else -1
            if ln.broken_at is None:
                cases.append(("trend_support" if sign > 0 else "trend_resistance", len(at) - 1, sign,
                              sign * (close - ln.at(n - 1)) / unit, ln.slope / unit))
            elif ln.broken_at == n - 1:                # broke on the last bar: does it go on?
                breaks.append((len(at) - 1, -sign, -sign * (close - ln.at(n - 1)) / unit, ln.slope / unit))

    def other(s):
        """A random step other than s (the baseline's place)."""
        while True:
            o = rng.randrange(len(at))
            if o != s or len(at) == 1:
                return at[o]

    roles = {r: {"line": _tally(), "baseline": _tally()} for r in ROLES}
    for role, s, sign, dist, slope in cases:
        if dist <= 0:                                  # not on the line's side now
            continue
        for which, (_i, t, unit, close) in (("line", at[s]), ("baseline", other(s))):
            got = _touch(bars[t:t + 2 * horizon], n, _line(close, unit, -sign, dist, slope, n), sign, unit, horizon)
            if got is not None:
                roles[role][which]["n"] += 1
                roles[role][which][got] += 1
    follow = {"n": 0, "follow": 0, "baseline_n": 0, "baseline_follow": 0}
    for s, way, dist, slope in breaks:
        for prefix, (_i, t, unit, close) in (("", at[s]), ("baseline_", other(s))):
            line = _line(close, unit, -way, dist, slope, n)            # the line behind the price
            got = _run_away(bars[t:t + 2 * horizon], close, way, unit, horizon, back=line, n=n)
            if got is not None:
                follow[f"{prefix}n"] += 1
                follow[f"{prefix}follow"] += got

    def rate(x, of):
        return round(x / of, 3) if of else None

    out = {}
    for role, got in roles.items():
        line, base = got["line"], got["baseline"]
        out[role] = {**line, "bounce_rate": rate(line["bounce"], line["n"]),
                     "break_rate": rate(line["break"], line["n"]),
                     "baseline": {**base, "bounce_rate": rate(base["bounce"], base["n"]),
                                  "break_rate": rate(base["break"], base["n"])}}
    out["trend_break_follow"] = {**follow, "follow_rate": rate(follow["follow"], follow["n"]),
                                 "baseline_rate": rate(follow["baseline_follow"], follow["baseline_n"])}
    return {"timeframe": tf, "bars": len(bars), "from": bars[0]["at"] if bars else None,
            "to": bars[-1]["at"] if bars else None, "steps": len(at), "window": window, "horizon_bars": horizon,
            "bounce_atr": BOUNCE_ATR, "seed": seed, "roles": out}


_cache: dict | None = None


def load(path: Path = OUT) -> dict:
    global _cache
    if _cache is None or path != OUT:
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            got = {}
        if path != OUT:
            return got
        _cache = got
    return _cache


def rates(ticker: str, tf: str, table: dict | None = None) -> dict:
    """Each line role's past rate for desk.channels.lines: {"channel": {"lower", "upper"}, "trend_support",
    "trend_resistance", "trend_break"}; empty when this ticker and timeframe were not backtested."""
    got = ((table if table is not None else load()).get("tickers") or {}).get(ticker, {}).get(tf)
    if not got:
        return {}
    roles, h = got["roles"], got["horizon_bars"]

    def one(role):
        r = roles[role]
        return {"n": r["n"], "bounce_rate": r["bounce_rate"], "break_rate": r["break_rate"],
                "baseline_bounce_rate": r["baseline"]["bounce_rate"], "horizon_bars": h}
    f = roles["trend_break_follow"]
    return {"channel": {"lower": one("channel_lower"), "upper": one("channel_upper")},
            "trend_support": one("trend_support"), "trend_resistance": one("trend_resistance"),
            "trend_break": {"n": f["n"], "follow_rate": f["follow_rate"], "baseline_rate": f["baseline_rate"],
                            "horizon_bars": h}}


def summary(ticker: str, table: dict | None = None) -> dict | None:
    got = (table if table is not None else load())
    per = (got.get("tickers") or {}).get(ticker)
    if not per:
        return None
    return {"generated_at": got.get("generated_at"),
            "timeframes": {tf: {"from": r["from"], "to": r["to"], "steps": r["steps"]} for tf, r in per.items()}}


def main(argv: list[str] | None = None) -> int:
    from .prices import yahoo_candles

    ap = argparse.ArgumentParser(prog="python -m desk.lines_bt", description=__doc__.splitlines()[0])
    ap.add_argument("--ticker", default="NQ=F")
    ap.add_argument("--tf", default=",".join(DEFAULT_TFS))
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    table = load(args.out) if args.out.exists() else {}
    table.setdefault("tickers", {})
    for tf in args.tf.split(","):
        bars = [asdict(b) for b in yahoo_candles(args.ticker, tf, FETCH[tf])]
        result = backtest(bars, tf)
        table["tickers"].setdefault(args.ticker, {})[tf] = result
        print(json.dumps({"ticker": args.ticker, "tf": tf, "bars": result["bars"], "steps": result["steps"],
                          **{r: (v["n"], v.get("bounce_rate"), v["baseline"]["bounce_rate"]) if r in ROLES else
                             (v["n"], v["follow_rate"], v["baseline_rate"]) for r, v in result["roles"].items()}},
                         ensure_ascii=False))
    table["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    args.out.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
