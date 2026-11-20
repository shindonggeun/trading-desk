"""#694 "had one followed the holding alerts": a walk over past daily bars (docs/holding-watch.md).

Every ``EVERY`` trading days of each ticker one hypothetical buy at the close (the average). From the next
day on, with the regime and lines worked out on the bars finished before that day (desk.regime_live,
desk.holding_watch.lines), one of these ends the holding, first that happens:

- ``stop``: the day's low reaches the stop - out at the stop, or at the open when it gaps under;
- ``take``: a range's top reached in profit - out at the take-profit line;
- ``reduce`` (variant ``follow_reduce``): the regime turned to a downtrend - out at that day's close;
- ``hold``: ``HOLD_DAYS`` days on - out at the close.

Compared with holding ``HOLD_DAYS`` days with no alert and with only the max-loss stop. R = the return
over the risk taken at the buy (buy - the first stop). Reads Yahoo (``python -m desk.holding_watch_bt``).
"""

from __future__ import annotations

import argparse
import statistics
import sys

from . import holding_watch, regime_live
from .prices import yahoo_candles

TICKERS = ("NVDA", "AMD", "QQQ", "SOXL", "TLT", "KO", "005930.KS", "000660.KS", "035420.KS", "069500.KS")
YEARS_BARS = 1300                 # about five years of daily bars
WINDOW = 250                      # the bars the live watch reads (holding_watch.BARS)
EVERY = 10
HOLD_DAYS = 60
MAX_LOSS = holding_watch.MAX_LOSS_PCT


def _plans(bars: list[dict]) -> list[dict | None]:
    """For each day i, the regime and the structure lines from the bars finished before it."""
    out: list[dict | None] = [None] * len(bars)
    for i in range(regime_live.MIN_BARS + 1, len(bars)):
        done = bars[max(0, i - WINDOW):i]
        cls = regime_live.classify(done, "1d")
        out[i] = {"cls": cls, "bars": done}
    return out


def _walk(bars, plans, start: int, mode: str) -> dict | None:
    """One holding bought at the close of ``start``; mode: alerts | follow_reduce | max_loss | hold."""
    entry = bars[start]["close"]
    first = plans[start + 1] if start + 1 < len(bars) else None
    if first is None:
        return None
    ln0 = holding_watch.lines(first["cls"], first["bars"], entry, MAX_LOSS)
    first_stop = {"hold": entry * (1 - MAX_LOSS / 100), "max_loss": ln0["max_loss_stop"]}.get(mode, ln0["stop"])
    risk = entry - first_stop
    if risk <= 0:
        return None
    worst = 0.0
    for i in range(start + 1, min(start + 1 + HOLD_DAYS, len(bars))):
        bar, plan = bars[i], plans[i]
        ln = holding_watch.lines(plan["cls"], plan["bars"], entry, MAX_LOSS)
        stop = ln["max_loss_stop"] if mode == "max_loss" else ln["stop"]
        worst = min(worst, bar["low"] / entry - 1)
        if mode != "hold" and bar["low"] <= stop:
            out = min(bar["open"], stop)
            return {"ret": out / entry - 1, "r": (out - entry) / risk, "why": "stop", "days": i - start, "worst": worst}
        if mode in ("alerts", "follow_reduce") and ln["take_profit"] and bar["high"] >= ln["take_profit"] > entry:
            out = max(bar["open"], ln["take_profit"])
            return {"ret": out / entry - 1, "r": (out - entry) / risk, "why": "take", "days": i - start, "worst": worst}
        if mode == "follow_reduce" and ln["regime"] == "trend_down":
            out = bar["close"]
            return {"ret": out / entry - 1, "r": (out - entry) / risk, "why": "reduce", "days": i - start, "worst": worst}
    last = min(start + HOLD_DAYS, len(bars) - 1)
    if last <= start:
        return None
    out = bars[last]["close"]
    return {"ret": out / entry - 1, "r": (out - entry) / risk, "why": "hold", "days": last - start, "worst": worst}


def summary(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    rets = [t["ret"] for t in trades]
    return {"n": len(trades), "win": sum(r > 0 for r in rets) / len(rets), "avg_ret": statistics.mean(rets),
            "avg_r": statistics.mean(t["r"] for t in trades), "worst_ret": min(rets),
            "worst_dip": min(t["worst"] for t in trades), "avg_days": statistics.mean(t["days"] for t in trades),
            "why": {w: sum(t["why"] == w for t in trades) for w in ("stop", "take", "reduce", "hold")}}


def run(tickers=TICKERS) -> dict[str, dict]:
    trades: dict[str, list[dict]] = {m: [] for m in ("hold", "max_loss", "alerts", "follow_reduce")}
    for t in tickers:
        bars = [{"at": b.at, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
                for b in yahoo_candles(t, "1d", YEARS_BARS)]
        plans = _plans(bars)
        for start in range(WINDOW, len(bars) - 1, EVERY):
            for mode, got in trades.items():
                one = _walk(bars, plans, start, mode)
                if one is not None:
                    got.append(one)
        print(f"{t}: {len(bars)} bars", file=sys.stderr)
    return {m: summary(v) for m, v in trades.items()}


def table(result: dict[str, dict]) -> str:
    names = {"hold": f"{HOLD_DAYS}일 보유(알림 없음)", "max_loss": f"평단 −{MAX_LOSS:g}% 손절만",
             "alerts": "알림 따름(손절 · 익절)", "follow_reduce": "알림 따름 + 하락 추세에 정리"}
    rows = ["| 따른 규칙 | 거래 | 승률 | 평균 수익 | 평균 R | 최악 거래 | 보유 중 최대 낙폭 | 평균 보유일 | 끝난 이유(손절/익절/정리/만기) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for m, s in result.items():
        if not s.get("n"):
            continue
        w = s["why"]
        rows.append(f"| {names[m]} | {s['n']} | {s['win']:.0%} | {s['avg_ret']:+.2%} | {s['avg_r']:+.2f} | "
                    f"{s['worst_ret']:+.1%} | {s['worst_dip']:+.1%} | {s['avg_days']:.0f} | "
                    f"{w['stop']}/{w['take']}/{w['reduce']}/{w['hold']} |")
    return "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", nargs="*", default=list(TICKERS))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(table(run(args.tickers)))


if __name__ == "__main__":
    main()
