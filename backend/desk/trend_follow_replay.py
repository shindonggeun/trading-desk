"""#977 replay of the daily trend-following read: each past day read on the bars up to it.

Only the swing lines are used (no chart frame - its trendlines are drawn with the whole history), so every
read sees nothing after its own day. A breakout counts as failed when a close in the next ``HOLD`` bars ends
under the broken line by ``trend_follow.BREAK_ATR`` ATRs (the read's own rule).

    python -m desk.trend_follow_replay SOXX,SOXL,NQ=F,^KS11,000660.KS,005930.KS --years 3 --out replay.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime, timedelta

from . import channels, trend_follow
from . import market_calendar as calendar

HOLD = 10
WINDOW = channels.BARS["1d"]          # the bars chart-read hands the read


def _day(ticker: str, at: str) -> str:
    return calendar.bar_day(at, trend_follow._market(ticker) == "kr").isoformat()


def replay(ticker: str, bars: list[dict], hold: int = HOLD) -> list[dict]:
    """The days a read turned ``breakout_confirmed`` or ``first_rebound`` on its own last bar, and what the
    next ``hold`` bars did."""
    out = []
    for m in range(trend_follow.MIN_BARS, len(bars) - hold + 1):
        window = bars[max(0, m - WINDOW):m]
        last = window[-1]
        now = datetime.fromisoformat(last["at"]).astimezone(UTC) + timedelta(days=2)     # the bar is final
        read = trend_follow.build(ticker, {"bars": window, "source": "replay"}, now)
        stage = read["stage"]
        if not stage or stage["confirmed_at"] != last["at"] or stage["state"] not in ("breakout_confirmed",
                                                                                         "first_rebound"):
            continue
        ahead = bars[m:m + hold]
        close = last["close"]
        row = {"day": _day(ticker, last["at"]), "state": stage["state"], "signal": stage["signal"],
               "close": channels._price(close), "return_pct": round((ahead[-1]["close"] / close - 1) * 100, 2),
               "worst_low_pct": round((min(b["low"] for b in ahead) / close - 1) * 100, 2)}
        if stage["state"] == "breakout_confirmed":
            line = read["key_prices"]["hold"]["price"]
            tol = trend_follow.BREAK_ATR * channels.atr(window)
            fail = next((b for b in ahead if b["close"] < line - tol), None)
            row.update(line=line, failed_on=_day(ticker, fail["at"]) if fail else None)
        out.append(row)
    return out


def summary(rows: list[dict]) -> dict:
    def part(xs: list[dict]) -> dict:
        rets = [r["return_pct"] for r in xs]
        return {"n": len(xs), "up": sum(r > 0 for r in rets),
                "median_return_pct": round(statistics.median(rets), 2) if rets else None}
    breaks = [r for r in rows if r["state"] == "breakout_confirmed"]
    return {"breakout": {**part(breaks), "failed": sum(1 for r in breaks if r["failed_on"])},
            "first_up_bar": part([r for r in rows if r["signal"] == "first_up_bar"]),
            "support_reclaim": part([r for r in rows if r["signal"] == "support_reclaim"])}


def main() -> None:  # pragma: no cover - reads Yahoo
    from . import prices

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tickers")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = {}
    for t in args.tickers.split(","):
        raw = prices.yahoo_candles(t, "1d", 252 * args.years + WINDOW)
        bars = [{"at": b.at, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
                for b in raw]
        rows = replay(t, bars)
        out[t] = {"first": _day(t, bars[0]["at"]), "last": _day(t, bars[-1]["at"]), "bars": len(bars),
                  "days_read": max(0, len(bars) - HOLD + 1 - trend_follow.MIN_BARS), "summary": summary(rows),
                  "events": rows}
        print(t, json.dumps(out[t]["summary"], ensure_ascii=False))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
