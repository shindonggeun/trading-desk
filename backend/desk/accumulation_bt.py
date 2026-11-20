"""Did the price bounce when it came back to an accumulation band? (#751) A walk-forward backtest of
desk.accumulation by view, the way desk.patterns_bt measures the patterns.

Every STEP-th bar of a view the bands are found on the WINDOW bars up to it (nothing after it); each
accumulation band below the price is watched for WATCH bars:

- a **touch**: the first bar whose low reaches the band's top;
- after it, within REACT bars, a **bounce** is a close BOUNCE_ATR above the band's top, a **break** a
  close BREAK_ATR below its bottom - whichever first (neither: "none");
- a band counts once: one overlapping a band already counted by SAME_OVERLAP of the narrower one is that
  band again (a band of levels moves its edges a little with the ATR from step to step), and one not
  reached in WATCH bars may be reached from a later step;
- the **baseline** is the same walk on the same ticker's returns shuffled (SHUFFLES times; each bar
  keeps its own shape around its close): how often a band so found would have "held" on a price path
  with no structure. Only the difference is evidence: held-out (the last 30% of the touches by date)
  bounce share minus the baseline's, at least EDGE_PCT and two standard errors (desk.patterns_bt), and
  train and held-out within UNSTABLE_PCT of each other - else ``no_edge`` / ``unstable``.

``python -m desk.accumulation_bt`` reads Yahoo bars and writes desk.accumulation.STATS_FILE, which the
chart read quotes. Past rates, not a forecast.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import sys
from datetime import UTC, date, datetime

from . import accumulation

TICKERS = ("GC=F", "NVDA", "KORU", "005930.KS", "SPY", "QQQ", "AMD", "SOXX", "AAPL", "MSFT")
# view -> how its bars are read and walked
SPEC = {
    "short": {"interval": "240m", "fetch": 2000, "window": 300, "step": 3, "watch": 60, "react": 20},
    "mid": {"interval": "1d", "fetch": 5200, "window": 500, "step": 5, "watch": 60, "react": 20},
    "long": {"interval": "1w", "fetch": 5200, "window": 260, "step": 2, "watch": 26, "react": 10},
}
BOUNCE_ATR = 2.0
BREAK_ATR = 1.0
SHUFFLES = 3
TRAIN_SHARE = 0.7
MIN_N = 20
EDGE_PCT = 5.0
UNSTABLE_PCT = 10.0
SAME_OVERLAP = 0.5         # bands overlapping this share of the narrower one are the same band


def same(a: tuple[float, float], b: tuple[float, float]) -> bool:
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    narrow = min(a[1] - a[0], b[1] - b[0])
    return hi > lo and (narrow <= 0 or (hi - lo) / narrow >= SAME_OVERLAP)


def shuffled(bars: list[dict], seed: int) -> list[dict]:
    """The same bars on a path of their close-to-close returns shuffled: each keeps its shape (open,
    high, low over its close) and volume, so only the order - the structure - is gone."""
    rng = random.Random(seed)
    rets = [math.log(b["close"] / a["close"]) for a, b in itertools.pairwise(bars)]
    rng.shuffle(rets)
    out = [dict(bars[0])]
    close = bars[0]["close"]
    for b, r in zip(bars[1:], rets, strict=False):
        close *= math.exp(r)
        f = close / b["close"]
        out.append({**b, "open": b.get("open", b["close"]) * f, "high": b["high"] * f, "low": b["low"] * f,
                    "close": close})
    return out


def walk(bars: list[dict], view: str, spec: dict | None = None) -> list[dict]:
    """Every touch of an accumulation band and what followed: {at, outcome, distance_atr, score}."""
    spec = spec or SPEC[view]
    bars = accumulation.clean(bars)
    a = accumulation.atrs(bars)
    seen: list[tuple[float, float]] = []
    out = []
    w, watch, react = spec["window"], spec["watch"], spec["react"]
    for t in range(w, len(bars) - 1, spec["step"]):
        past = bars[t - w:t]
        z = accumulation.zones(past, view, past[-1]["close"], spec["interval"])
        for row in z["below"]:
            if row["role"] != "accumulation" or row.get("inside"):
                continue
            key = (row["low"], row["high"])
            if any(same(key, k) for k in seen):
                continue
            k = next((i for i in range(t, min(len(bars), t + watch)) if bars[i]["low"] <= row["high"]), None)
            if k is None:
                continue                            # not reached yet: a later step may see it reached
            seen.append(key)
            unit = a[t - 1]
            outcome = "none"
            for i in range(k, min(len(bars), k + react)):
                if bars[i]["close"] < row["low"] - BREAK_ATR * unit:
                    outcome = "break"
                    break
                if i > k and bars[i]["close"] > row["high"] + BOUNCE_ATR * unit:
                    outcome = "bounce"
                    break
            if k + react > len(bars) and outcome == "none":
                continue                            # still playing out
            out.append({"at": bars[k].get("at"), "outcome": outcome, "distance_atr": row["distance_atr"],
                        "score": row["score"]})
    return out


def _share(rows: list[dict], what: str = "bounce") -> float | None:
    return round(100 * sum(r["outcome"] == what for r in rows) / len(rows), 1) if rows else None


def summarize(rows: list[dict]) -> dict:
    return {"n": len(rows), "bounce_pct": _share(rows), "break_pct": _share(rows, "break")}


def verdict(train: dict, valid: dict, base: dict) -> dict:
    """The held-out bounce share against the shuffled baseline's (desk.patterns_bt._verdict's rule)."""
    v, b = valid.get("bounce_pct"), base.get("bounce_pct")
    if valid.get("n", 0) < MIN_N or v is None or b is None:
        return {"edge_pct": None, "edge_needed_pct": None, "verdict": "unmeasured"}
    edge = round(v - b, 1)
    p1, p0 = v / 100, b / 100
    se = math.sqrt(p1 * (1 - p1) / valid["n"] + p0 * (1 - p0) / max(1, base["n"]))
    need = max(EDGE_PCT, round(200 * se, 1))
    unstable = train.get("bounce_pct") is not None and abs(v - train["bounce_pct"]) > UNSTABLE_PCT
    return {"edge_pct": edge, "edge_needed_pct": need,
            "verdict": "unstable" if unstable else "edge" if edge >= need else "no_edge"}


def report(per_ticker: dict[str, dict[str, dict]]) -> dict:
    """Per view: pooled touches over the tickers, split by date, against the pooled shuffled walks."""
    views = {}
    for view, spec in SPEC.items():
        rows = sorted((r for g in per_ticker.values() for r in (g.get(view) or {}).get("real", [])),
                      key=lambda r: r["at"] or "")
        base = [r for g in per_ticker.values() for r in (g.get(view) or {}).get("shuffled", [])]
        cut = int(len(rows) * TRAIN_SHARE)
        train, valid, b = summarize(rows[:cut]), summarize(rows[cut:]), summarize(base)
        views[view] = {**summarize(rows), "baseline_pct": b["bounce_pct"], "baseline_n": b["n"],
                       "train": train, "valid": valid, **verdict(train, valid, b),
                       "horizon_bars": spec["react"], "watch_bars": spec["watch"], "timeframe": spec["interval"]}
    return {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "tickers": list(per_ticker),
            "bounce_atr": BOUNCE_ATR, "break_atr": BREAK_ATR, "shuffles": SHUFFLES, "views": views,
            "per_ticker": {t: {v: len((g.get(v) or {}).get("real", [])) for v in SPEC} for t, g in per_ticker.items()}}


def table(rep: dict) -> list[str]:
    lines = ["| 관점 | 봉 | 닿은 횟수 | 반등 | 이탈 | 셔플 기준 반등 | 학습 · 검증 반등 | 우위(필요) | 판정 |",
             "|---|---|---|---|---|---|---|---|---|"]

    def pct(x):
        return "-" if x is None else f"{x:.0f}%"

    for view, g in rep["views"].items():
        edge = "-" if g["edge_pct"] is None else f"{g['edge_pct']:+.1f}%p"
        need = "-" if g["edge_needed_pct"] is None else f"{g['edge_needed_pct']}"
        tf = accumulation.VIEW_TF_KO.get(g["timeframe"], g["timeframe"])
        lines.append(f"| {accumulation.VIEWS[view]['label']} | {tf} | {g['n']} | {pct(g['bounce_pct'])} | "
                     f"{pct(g['break_pct'])} | {pct(g['baseline_pct'])}({g['baseline_n']}) | "
                     f"{pct(g['train']['bounce_pct'])} · {pct(g['valid']['bounce_pct'])} | {edge}({need}) | {g['verdict']} |")
    return lines


def weekly(bars: list[dict]) -> list[dict]:
    """Daily bars folded into ISO weeks (stamped with the week's first bar)."""
    out: list[dict] = []
    key = None
    for b in bars:
        d = date.fromisoformat(b["at"][:10])
        k = d.isocalendar()[:2]
        if k != key:
            out.append(dict(b))
            key = k
            continue
        w = out[-1]
        w["high"], w["low"], w["close"] = max(w["high"], b["high"]), min(w["low"], b["low"]), b["close"]
        w["volume"] = (w.get("volume") or 0) + (b.get("volume") or 0)
    return out


def run(bars_by_view: dict[str, list[dict]]) -> dict[str, dict]:
    out = {}
    for view, bars in bars_by_view.items():
        if not bars:
            continue
        real = walk(bars, view)
        shuf = [r for s in range(SHUFFLES) for r in walk(shuffled(accumulation.clean(bars), s), view)]
        out[view] = {"real": real, "shuffled": shuf}
    return out


def main(argv: list[str] | None = None) -> int:
    from .prices import yahoo_candles

    ap = argparse.ArgumentParser(prog="python -m desk.accumulation_bt", description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=",".join(TICKERS))
    ap.add_argument("--out", default=str(accumulation.STATS_FILE))
    args = ap.parse_args(argv)

    def rows(t: str, interval: str, count: int) -> list[dict]:
        return [{"at": b.at if isinstance(b.at, str) else b.at.isoformat(), "open": b.open, "high": b.high,
                 "low": b.low, "close": b.close, "volume": getattr(b, "volume", None)}
                for b in yahoo_candles(t, interval, count)]

    per = {}
    for t in args.tickers.split(","):
        got: dict[str, list[dict]] = {}
        try:
            daily = rows(t, "1d", SPEC["mid"]["fetch"])
            got["mid"], got["long"] = daily, weekly(daily)
        except Exception as exc:  # noqa: BLE001 - one ticker missing leaves the rest
            print(f"{t}: no daily bars ({type(exc).__name__})", file=sys.stderr)
        try:
            got["short"] = rows(t, "240m", SPEC["short"]["fetch"])
        except Exception as exc:  # noqa: BLE001
            print(f"{t}: no 4h bars ({type(exc).__name__})", file=sys.stderr)
        if not got:
            continue
        per[t] = run(got)
        print(f"{t}: " + ", ".join(f"{v} {len(g['real'])}/{len(g['shuffled'])}" for v, g in per[t].items()), file=sys.stderr)
    rep = report(per)
    from pathlib import Path

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(table(rep)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
