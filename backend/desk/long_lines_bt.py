"""After a long line of months is reached, does the price bounce or break? (#679) A walk-forward backtest
of desk.long_lines on daily bars, read the way desk.lines_bt reads the short lines.

Every STEP-th bar the long lines are found on the WINDOW bars up to it (nothing after it); each unbroken
one the price is on the right side of is watched for HORIZON bars:

- a **touch**: the low reaches a long support (the high a long resistance) - the line read where it is
  on that bar (desk.lines_bt._touch, its TOUCH_ATR · BREAK_ATR · BOUNCE_ATR);
- after it, within HORIZON bars, a **bounce** is a close BOUNCE_ATR away on the line's side, a **break**
  a close BREAK_ATR beyond it - whichever first (neither: "none");
- a line counts once: its first touch (the same line found at later steps is not a new case);
- the **baseline** is a line of the same slope and distance (in ATR) set at another step of the same
  ticker drawn at random: how often any line so placed would have "held".

``python -m desk.long_lines_bt`` reads Yahoo daily bars and writes OUT, which the line alerts quote
(desk.line_signals). Past rates, not a forecast.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from . import long_lines
from .lines_bt import BOUNCE_ATR, _line, _tally, _touch

OUT = Path(__file__).parent / "data" / "backtest" / "long_lines.json"
TICKERS = ("AMD", "NVDA", "SOXX", "KORU", "SOXL", "TQQQ", "QQQ", "SPY", "AAPL", "MSFT")
FETCH = 5200               # daily bars (about 20 years)
WINDOW = long_lines.SPEC["1d"]["window"]
STEP = 5
HORIZON = 10
ROLES = ("long_support", "long_resistance")


def backtest(bars: list[dict], step: int = STEP, seed: int = 0) -> dict:
    """Per role the line's and the baseline's touches and what followed, over ``bars`` (oldest first)."""
    bars = [b for b in bars if b["low"] > 0 and b["close"] > 0 and b["high"] >= b["low"]]
    rng = random.Random(seed)
    n = WINDOW
    at: list[tuple[int, float, float]] = []                  # (t, unit, log close) of every step
    cases: list[tuple[str, int, int, float, float, str]] = []   # role, step, sign, dist ATR, slope ATR/bar, key
    for t in range(WINDOW, len(bars) - HORIZON, step):
        past = bars[t - WINDOW:t]
        a = long_lines.atr(past)
        if not a:
            continue
        unit = a / past[-1]["close"]
        close = math.log(past[-1]["close"])
        at.append((t, unit, close))
        for ln in long_lines.find(past, "1d", unit):
            if ln.broken_at is not None:
                continue
            sign = 1 if ln.kind == "support" else -1
            # the line by its first anchor's date and its first touch: the same line at a later step is one
            key = f"{ln.kind}-{past[ln.i]['at']}-{past[ln.touches[0]]['at']}"
            cases.append((f"long_{ln.kind}", len(at) - 1, sign, sign * (close - ln.at(n - 1)) / unit,
                          ln.slope / unit, key))

    def other(s):
        while True:
            o = rng.randrange(len(at))
            if o != s or len(at) == 1:
                return at[o]

    roles = {r: {"line": _tally(), "baseline": _tally()} for r in ROLES}
    done: set[str] = set()
    for role, s, sign, dist, slope, key in cases:
        if dist <= 0 or key in done:
            continue
        t, unit, close = at[s]
        got = _touch(bars[t:t + 2 * HORIZON], n, _line(close, unit, -sign, dist, slope, n), sign, unit, HORIZON)
        if got is None:
            continue                                # not reached yet: the line may be reached at a later step
        done.add(key)
        roles[role]["line"]["n"] += 1
        roles[role]["line"][got] += 1
        bt, bunit, bclose = other(s)
        base = _touch(bars[bt:bt + 2 * HORIZON], n, _line(bclose, bunit, -sign, dist, slope, n), sign, bunit, HORIZON)
        if base is not None:
            roles[role]["baseline"]["n"] += 1
            roles[role]["baseline"][base] += 1
    return {"bars": len(bars), "from": bars[0]["at"] if bars else None, "to": bars[-1]["at"] if bars else None,
            "steps": len(at), "roles": roles}


def _rate(x: int, of: int) -> float | None:
    return round(x / of, 3) if of else None


def report(per_ticker: dict[str, dict]) -> dict:
    """The pooled rates over the tickers, and each ticker's counts."""
    pooled = {r: {"line": _tally(), "baseline": _tally()} for r in ROLES}
    for got in per_ticker.values():
        for r in ROLES:
            for which in ("line", "baseline"):
                for k, v in got["roles"][r][which].items():
                    pooled[r][which][k] += v
    roles = {}
    for r, got in pooled.items():
        line, base = got["line"], got["baseline"]
        roles[r] = {**line, "bounce_rate": _rate(line["bounce"], line["n"]), "break_rate": _rate(line["break"], line["n"]),
                    "baseline": {**base, "bounce_rate": _rate(base["bounce"], base["n"]),
                                 "break_rate": _rate(base["break"], base["n"])}}
    return {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "tickers": list(per_ticker),
            "window": WINDOW, "step": STEP, "horizon_bars": HORIZON, "bounce_atr": BOUNCE_ATR, "roles": roles,
            "per_ticker": {t: {"from": g["from"], "to": g["to"], "steps": g["steps"],
                               **{r: g["roles"][r]["line"]["n"] for r in ROLES}} for t, g in per_ticker.items()}}


@lru_cache(maxsize=1)
def load(path: str = str(OUT)) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rate_text(role: str, table: dict | None = None) -> str:
    """The alert's past-rate line for a long line's signal (desk.line_signals)."""
    if role == "long_break":
        return "과거 맞은 비율: 장기 선 이탈 뒤는 따로 재지 않았어요"
    table = load() if table is None else table
    got = (table.get("roles") or {}).get(role)
    if not got or not got.get("n"):
        return "과거 맞은 비율: 장기 선은 아직 재지 않았어요"

    def pct(x):
        return "-" if x is None else f"{x * 100:.0f}%"

    base = got["baseline"]
    return (f"과거 장기 선에 닿은 뒤 {table['horizon_bars']}봉 안 반등 {pct(got['bounce_rate'])} · 이탈 "
            f"{pct(got['break_rate'])} (같은 자리 아무 선 반등 {pct(base['bounce_rate'])}, {got['n']}번)")


def table(rep: dict) -> list[str]:
    lines = ["| 선 | 닿은 선 n | 반등 | 이탈 | 둘 다 아님 | 기준선 n | 기준선 반등 | 기준선 이탈 |", "|---|---|---|---|---|---|---|---|"]
    ko = {"long_support": "장기 상승 지지선", "long_resistance": "장기 하락 저항선"}

    def pct(x):
        return "-" if x is None else f"{x * 100:.0f}%"

    for r in ROLES:
        g = rep["roles"][r]
        b = g["baseline"]
        lines.append(f"| {ko[r]} | {g['n']} | {pct(g['bounce_rate'])} | {pct(g['break_rate'])} | "
                     f"{pct(_rate(g['none'], g['n']))} | {b['n']} | {pct(b['bounce_rate'])} | {pct(b['break_rate'])} |")
    return lines


def main(argv: list[str] | None = None) -> int:
    from .prices import yahoo_candles

    ap = argparse.ArgumentParser(prog="python -m desk.long_lines_bt", description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=",".join(TICKERS))
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    per = {}
    for t in args.tickers.split(","):
        try:
            got = yahoo_candles(t, "1d", FETCH)
        except Exception as exc:  # noqa: BLE001 - one ticker missing leaves the rest
            print(f"{t}: no bars ({type(exc).__name__})", file=sys.stderr)
            continue
        bars = [{"at": b.at if isinstance(b.at, str) else b.at.isoformat(), "open": b.open, "high": b.high,
                 "low": b.low, "close": b.close} for b in got]
        per[t] = backtest(bars)
        print(f"{t}: {per[t]['steps']} steps, " + ", ".join(f"{r} {per[t]['roles'][r]['line']['n']}" for r in ROLES),
              file=sys.stderr)
    rep = report(per)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(table(rep)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
