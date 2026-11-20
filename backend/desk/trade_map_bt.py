"""Breakout or support entry - which did better, by regime? (#652) A walk-forward backtest of the trade
map's two scenarios (desk.trade_map.scenarios).

On every STEP-th daily bar of each ticker the levels an analysis at that close could have computed
(desk.backtest.snapshot: only bars up to it) give both scenarios per side; the regime is the playbook's
(desk.playbook.detect_regime: uptrend · range · downtrend · crash). Then the next bars play them out:

- fill within ENTRY_BARS bars: ``now`` at the next open, ``limit`` when the low (high, short) reaches the
  entry (at the open when it gaps through), ``stop`` (breakout) when the high (low) reaches it (at the open
  when it gaps through). Not filled: no trade. A ``limit`` whose open gapped past its own stop as well is
  filled there and stopped at once (a scratch less costs) - it stays in the sample, not dropped (#671); a
  ``now`` that opens past its stop is a plan already broken and is left out.
- after the fill, within HOLD_BARS bars: the stop first (a bar that reaches both counts as the stop - the
  cautious reading), else the first target, else the close of the last bar. The fill bar itself can only
  stop out.
- R = the move from the fill over the plan's risk (its entry to its stop), less COST_BPS each side.

Per side, entry kind and regime: filled count, fill rate, win rate, mean and median R, and the worst run
of the R sum in date order (a run over overlapping trades, not an account's equity curve). Plans
come every STEP bars and are held up to ENTRY_BARS + HOLD_BARS, so trades of one ticker overlap: ``n_indep``
counts the ones that do not (per ticker, side and kind, greedily in time) - the sample the comparison
leans on. ``python -m desk.trade_map_bt`` writes desk/data/backtest/trade_map_scenarios.json,
which the trade map reads (desk.trade_map_record). Past rates, not a forecast.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import backtest, horizons, playbook, trade_map
from .trade_map_record import PATH

TICKERS = ("KORU", "SOXL", "TQQQ", "UPRO", "TNA", "122630.KS", "NQ=F", "ES=F")
START = "2016-01-01"
STEP = 2
ENTRY_BARS = 5
HOLD_BARS = 20
COST_BPS = 10.0
REGIMES = ("uptrend", "range", "downtrend", "crash")


def play(sc: dict, future: list[dict], cost_bps: float = COST_BPS) -> dict | None:
    """One scenario on the bars after its plan; None when it never filled."""
    sign = 1 if sc["side"] == "long" else -1
    entry, stop, t1 = sc["entry"], sc["stop"], sc["targets"][0]["price"] if sc["targets"] else None
    fill, at = None, None
    for i, b in enumerate(future[:ENTRY_BARS]):
        if sc["entry_type"] == "now":
            fill, at = b["open"], i
        elif sc["entry_type"] == "limit":
            if sign * (b["low" if sign > 0 else "high"] - entry) <= 0:
                fill = min(entry, b["open"]) if sign > 0 else max(entry, b["open"])
                at = i
        elif sign * ((b["high"] if sign > 0 else b["low"]) - entry) >= 0:
            fill = max(entry, b["open"]) if sign > 0 else min(entry, b["open"])
            at = i
        if fill is not None:
            break
    if fill is None:
        return None
    if sign * (fill - stop) <= 0:
        if sc["entry_type"] != "limit":                     # a "now" whose open is past its stop: plan broken
            return None
        # #671 a resting limit that the open gapped through, stop and all: filled there, stopped right there
        risk = abs(entry - stop)
        return {"r": -2 * cost_bps / 1e4 * fill / risk, "exit": "stop", "fill": fill, "bars": 1,
                "start": at, "end": at}
    # R against the risk the plan took (entry to stop): a fill at an open next to the stop must not
    # turn a small move into tens of R
    risk = abs(entry - stop)
    exit_, how, end = None, "time", None
    bars = future[at: at + HOLD_BARS]
    for j, b in enumerate(bars):
        worst, best = (b["low"], b["high"]) if sign > 0 else (b["high"], b["low"])
        if sign * (worst - stop) <= 0:
            exit_ = (min(stop, b["open"]) if sign > 0 else max(stop, b["open"])) if j else stop
            how, end = "stop", at + j
            break
        if j and t1 is not None and sign * (best - t1) >= 0:
            exit_ = max(t1, b["open"]) if sign > 0 else min(t1, b["open"])
            how, end = "target", at + j
            break
    if exit_ is None:
        exit_, end = bars[-1]["close"], at + len(bars) - 1
    r = sign * (exit_ - fill) / risk - 2 * cost_bps / 1e4 * fill / risk
    return {"r": r, "exit": how, "fill": fill, "bars": len(bars), "start": at, "end": end}


def _independent(ts: list[dict]) -> int | None:
    """How many of ``ts`` do not overlap an earlier kept one of the same ticker, side and kind; None when a
    trade has no bar indexes (a per-ticker file from before #671) - not silently all of them."""
    if any("t0" not in t or "t1" not in t for t in ts):
        return None
    last: dict[tuple, int] = {}
    n = 0
    for t in sorted(ts, key=lambda t: (t["ticker"], t["t0"])):
        k = (t["ticker"], t["side"], t["kind"])
        if t["t0"] > last.get(k, -1):
            n += 1
            last[k] = t["t1"]
    return n


def _worst_run(rs: list[float]) -> float:
    peak = total = worst = 0.0
    for r in rs:
        total += r
        peak = max(peak, total)
        worst = min(worst, total - peak)
    return round(worst, 2)


def summarize(trades: list[dict], tried: dict[str, int]) -> dict:
    """Rows per "side:kind:regime" (and "side:kind:all")."""
    groups: dict[str, list[dict]] = {}
    for t in sorted(trades, key=lambda t: (t["day"], t["ticker"])):
        for reg in (t["regime"], "all"):
            groups.setdefault(f"{t['side']}:{t['kind']}:{reg}", []).append(t)
    out = {}
    for key, ts in groups.items():
        rs = [t["r"] for t in ts]
        out[key] = {"n": len(ts), "n_indep": _independent(ts), "tried": tried.get(key, len(ts)), "fill_rate": round(len(ts) / max(tried.get(key, 1), 1), 3),
                    "win_rate": round(sum(r > 0 for r in rs) / len(rs), 3), "avg_r": round(statistics.fmean(rs), 3),
                    "median_r": round(statistics.median(rs), 3), "max_dd_r": _worst_run(rs),
                    "target_rate": round(sum(t["exit"] == "target" for t in ts) / len(ts), 3),
                    "stop_rate": round(sum(t["exit"] == "stop" for t in ts) / len(ts), 3)}
    return out


def run(tickers=TICKERS, start: str = START, step: int = STEP, fetch=None, progress=print,
        trades_dir: Path | None = None, vix: dict[str, float] | None = None) -> dict:
    """The report over ``tickers``; with ``trades_dir`` each ticker's trades and tries are also kept there
    (trades_<ticker>.json), so a long run can go one ticker at a time and be merged (``merge``)."""
    h = horizons.get("swing")
    trades: list[dict] = []
    tried: dict[str, int] = {}
    span = [None, None]
    tried_of: dict[str, dict[str, int]] = {}
    for ticker in tickers:
        try:
            primary, secondary, _notes = (fetch or backtest.history)(ticker, h, start)
        except Exception as exc:  # noqa: BLE001 - one ticker missing leaves the rest
            progress(f"{ticker}: no bars ({type(exc).__name__})")
            continue
        n = 0
        for snap in backtest.snapshots(ticker, h, primary, secondary, start=start, step=step, vix=vix):
            # ^VIX too: the playbook's crash needs its fear evidence
            regime = playbook.detect_regime(playbook.features(snap.daily, snap.window, vix=snap.vix))["id"]
            if regime == "unknown":
                continue
            sr, ref = snap.sr, snap.window[-1]["close"]
            atr = sr.get("atr")
            if not atr or not ref:
                continue
            future = primary[snap.t + 1: snap.t + 1 + ENTRY_BARS + HOLD_BARS]
            for side in ("long", "short"):
                for sc in trade_map.scenarios(sr, ref, atr, side):
                    for k in (f"{side}:{sc['kind']}:{regime}", f"{side}:{sc['kind']}:all"):
                        tried[k] = tried.get(k, 0) + 1
                        tried_of.setdefault(ticker, {})[k] = tried_of.setdefault(ticker, {}).get(k, 0) + 1
                    got = play(sc, future)
                    if got:
                        base = snap.t + 1                # bar indexes of the fill and the exit, for n_indep
                        start_, end_ = got.pop("start"), got.pop("end")
                        trades.append({"ticker": ticker, "day": snap.day, "side": side, "kind": sc["kind"],
                                       "regime": regime, "rr": sc["rr"], "t0": base + start_, "t1": base + end_,
                                       **got})
            n += 1
            span[0] = min(span[0] or snap.day, snap.day)
            span[1] = max(span[1] or snap.day, snap.day)
        progress(f"{ticker}: {n} plan day(s)")
        if trades_dir is not None:
            mine = [t for t in trades if t["ticker"] == ticker]
            Path(trades_dir).mkdir(parents=True, exist_ok=True)
            (Path(trades_dir) / f"trades_{ticker}.json").write_text(json.dumps(
                {"ticker": ticker, "trades": mine, "tried": tried_of.get(ticker, {})}), encoding="utf-8")
    return report(trades, tried, list(tickers), span, step)


def report(trades: list[dict], tried: dict[str, int], tickers: list[str], span, step: int) -> dict:
    return {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "tickers": list(tickers),
            "from": span[0], "to": span[1], "step": step, "entry_bars": ENTRY_BARS, "hold_bars": HOLD_BARS,
            "cost_bps": COST_BPS, "trades": len(trades), "rows": summarize(trades, tried)}


def merge(trades_dir: Path, step: int = STEP) -> dict:
    """The report from the per-ticker files a ``--trades-dir`` run left."""
    trades: list[dict] = []
    tried: dict[str, int] = {}
    tickers = []
    for f in sorted(Path(trades_dir).glob("trades_*.json")):
        got = json.loads(f.read_text(encoding="utf-8"))
        tickers.append(got["ticker"])
        trades += got["trades"]
        for k, v in got["tried"].items():
            tried[k] = tried.get(k, 0) + v
    days = sorted(t["day"] for t in trades)
    return report(trades, tried, tickers, [days[0] if days else None, days[-1] if days else None], step)


def table(report: dict) -> list[str]:
    lines = ["| 방향 | 진입 | 레짐 | 체결 n (겹치지 않는 n · 체결률) | 승률 | 평균 R | 중앙 R | 1차 도달 | 손절 | 최악 누적 R |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for side in ("long", "short"):
        for reg in (*REGIMES, "all"):
            for kind in ("breakout", "support"):
                r = report["rows"].get(f"{side}:{kind}:{reg}")
                if not r:
                    continue
                lines.append(f"| {side} | {kind} | {reg} | {r['n']} ({'—' if r.get('n_indep') is None else r['n_indep']} · {round(r['fill_rate'] * 100)}%) | "
                             f"{round(r['win_rate'] * 100)}% | {r['avg_r']:+.2f} | {r['median_r']:+.2f} | "
                             f"{round(r['target_rate'] * 100)}% | {round(r['stop_rate'] * 100)}% | {r['max_dd_r']:+.1f} |")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m desk.trade_map_bt", description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", default=",".join(TICKERS))
    ap.add_argument("--from", dest="start", default=START)
    ap.add_argument("--step", type=int, default=STEP)
    ap.add_argument("--out", type=Path, default=PATH)
    ap.add_argument("--trades-dir", type=Path, default=None,
                    help="keep each ticker's trades here (one ticker per run is fine), then --merge")
    ap.add_argument("--merge", action="store_true", help="build --out from --trades-dir's files only")
    ap.add_argument("--no-out", action="store_true", help="do not write --out (a per-ticker run)")
    args = ap.parse_args(argv)
    if args.merge:
        report = merge(args.trades_dir, args.step)
    else:
        try:
            vix = backtest.vix_closes(args.start)
        except Exception as exc:  # noqa: BLE001 - the regime without its fear evidence, said so
            print(f"^VIX: none ({type(exc).__name__}) - crash reads on price alone", file=sys.stderr)
            vix = None
        report = run(args.tickers.split(","), args.start, args.step, progress=lambda m: print(m, file=sys.stderr),
                     trades_dir=args.trades_dir, vix=vix)
    if args.no_out:
        print("\n".join(table(report)))
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(table(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
