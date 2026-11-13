"""The evidence behind each tip in desk.tips (#330).

For every session of a tip's proxy since 2000, the regime is judged on the bars up to that day
(desk.tips.regime over the trailing window) and the proxy is followed forward 20 and 60 sessions.
The regime days are compared with all days, separately on a train period (before SPLIT) and a
held-out one (SPLIT on). A tip is ``weak`` unless its regime days were worse than all days by at
least WORSE_PP (return) / DECAY_PP (leveraged decay) at 60 sessions in **both** periods, each with
at least MIN_DAYS regime days. Days overlap (a 60-session window per day), so ``n`` counts days,
not independent episodes; ``episodes`` counts separate runs of regime days.

    python -m desk.tips_bt [--out desk/data/backtest/tips.json]

Yahoo daily bars, no key.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime

from . import tips

START = "2000-01-01"
SPLIT = "2015-01-01"
HORIZONS = (20, 60)
WORSE_PP = 2.0
DECAY_PP = 1.0
MIN_DAYS = 60
LEVERAGE = 3
BARS = 7000                 # about 27 years of sessions
WINDOW = tips.TREND + 1     # bars desk.tips.regime needs


def _align(series: tips.Series, days: list[str]) -> tips.Series:
    """Each indicator's last close on or before each of ``days`` (forward filled)."""
    out: tips.Series = {}
    for symbol, rows in series.items():
        j, last, aligned = 0, None, []
        for d in days:
            while j < len(rows) and rows[j]["day"] <= d:
                last = rows[j]["close"]
                j += 1
            aligned.append({"day": d, "close": last})
        out[symbol] = aligned
    return out


def regimes(series: tips.Series, days: list[str]) -> list[dict | None]:
    """The regime on each of ``days`` from the bars up to it (None before the window fills)."""
    aligned = _align(series, days)
    out: list[dict | None] = []
    for i in range(len(days)):
        if i + 1 < WINDOW:
            out.append(None)
            continue
        window = {s: [r for r in rows[i + 1 - WINDOW:i + 1] if r["close"] is not None] for s, rows in aligned.items()}
        out.append(tips.regime(window))
    return out


def forward(closes: list[float], i: int, h: int, metric: str) -> float | None:
    if i + h >= len(closes) or not closes[i]:
        return None
    ret = closes[i + h] / closes[i] - 1
    if metric == "return":
        return ret * 100
    synth = 1.0
    for k in range(i + 1, i + h + 1):
        synth *= 1 + LEVERAGE * (closes[k] / closes[k - 1] - 1)
    return ((synth - 1) - LEVERAGE * ret) * 100


def drawdown(closes: list[float], i: int, h: int) -> float | None:
    if i + h >= len(closes) or not closes[i]:
        return None
    return (min(closes[i + 1:i + h + 1]) / closes[i] - 1) * 100


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _stats(on: list[float], every: list[float]) -> dict:
    return {"n": len(on),
            "median": _r(statistics.median(on)) if on else None,
            "win": _r(100 * sum(v > 0 for v in on) / len(on)) if on else None,
            "all_median": _r(statistics.median(every)) if every else None,
            "all_win": _r(100 * sum(v > 0 for v in every) / len(every)) if every else None,
            "vs_all": _r(statistics.median(on) - statistics.median(every)) if on and every else None}


def _episodes(flags: list[bool]) -> int:
    return sum(1 for k, f in enumerate(flags) if f and (k == 0 or not flags[k - 1]))


def evidence(tip: tips.Tip, days: list[str], closes: list[float], regs: list[dict | None]) -> dict:
    out: dict = {"proxy": tip.proxy, "metric": tip.metric, "split": SPLIT[:4]}
    since = None
    for part, keep in (("train", lambda d: d < SPLIT), ("held_out", lambda d: d >= SPLIT)):
        idx = [i for i, d in enumerate(days) if keep(d) and d >= START and regs[i] is not None]
        flags = [tip.holds(regs[i]) is True for i in idx]
        if since is None and idx:
            since = days[idx[0]]
        block: dict = {"episodes": _episodes(flags), "regime_share": _r(100 * sum(flags) / len(flags)) if flags else None}
        for h in tips_horizons():
            vals = [(forward(closes, i, h, tip.metric), f) for i, f in zip(idx, flags, strict=True)]
            every = [v for v, _ in vals if v is not None]
            on = [v for v, f in vals if v is not None and f]
            block[str(h)] = _stats(on, every)
            if tip.metric == "return" and h == max(HORIZONS):
                dds = [(drawdown(closes, i, h), f) for i, f in zip(idx, flags, strict=True)]
                block[str(h)]["drawdown_median"] = _r(statistics.median([v for v, f in dds if v is not None and f]) if any(
                    v is not None and f for v, f in dds) else None)
                block[str(h)]["all_drawdown_median"] = _r(statistics.median([v for v, _ in dds if v is not None]) if any(
                    v is not None for v, _ in dds) else None)
        out[part] = block
    out["since"] = since
    out["weak"] = not all(_worse(out[p], tip.metric) for p in ("train", "held_out"))
    return out


def tips_horizons() -> tuple[int, ...]:
    return HORIZONS


def _worse(block: dict, metric: str) -> bool:
    s = block.get(str(max(HORIZONS))) or {}
    gap = DECAY_PP if metric == "decay" else WORSE_PP
    return (s.get("n") or 0) >= MIN_DAYS and s.get("vs_all") is not None and s["vs_all"] <= -gap


def run(fetch) -> dict:
    proxies = sorted({t.proxy for t in tips.TIPS})
    series = fetch(tuple(dict.fromkeys((*tips.SYMBOLS, *proxies))))
    indicators = {s: series[s] for s in tips.SYMBOLS if s in series}
    out = {"as_of": datetime.now(UTC).date().isoformat(), "start": START, "split": SPLIT,
           "basis": {"horizons": list(HORIZONS), "worse_pp": WORSE_PP, "decay_pp": DECAY_PP, "min_days": MIN_DAYS,
                     "rule": "weak unless the 60-session median on regime days is worse than on all days by the "
                             "gap in both train and held-out"},
           "tips": {}}
    cache: dict[str, tuple[list[str], list[float], list]] = {}
    for tip in tips.TIPS:
        if tip.proxy not in series:
            print(f"{tip.id}: no {tip.proxy} bars", file=sys.stderr)
            continue
        if tip.proxy not in cache:
            rows = series[tip.proxy]
            days = [r["day"] for r in rows]
            cache[tip.proxy] = (days, [r["close"] for r in rows], regimes(indicators, days))
        out["tips"][tip.id] = evidence(tip, *cache[tip.proxy])
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(tips.EVIDENCE_PATH))
    args = ap.parse_args(argv)
    from .prices import yahoo_candles
    from .risk_regime import fetch

    out = run(lambda symbols: fetch(symbols, candles=yahoo_candles, count=BARS))
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    for tid, ev in out["tips"].items():
        tr, ho = ev["train"]["60"], ev["held_out"]["60"]
        print(f"{tid:28} weak={ev['weak']!s:5} train vs_all={tr['vs_all']} (n={tr['n']}) "
              f"held-out vs_all={ho['vs_all']} (n={ho['n']})")


if __name__ == "__main__":
    main()
