"""Trade review: what I actually bought and sold, next to the plan I had at the time.

For each execution the broker reports (read-only, ``Broker.fills``):

- **plan**: the latest finished analysis of the same instrument dated on or before the
  fill, at most ``PLAN_LOOKBACK_DAYS`` earlier;
- **alignment**: did the fill go with the plan's direction (BUY on a long plan, SELL on
  a short one), against it, or happen while the plan said to wait;
- **entry gap**: fill price vs the plan's entry, in %;
- **outcome**: for a fill that followed the plan and had stop and target, which level
  daily bars touched first after the fill day — ``target`` / ``stop`` / ``both`` (same
  bar, order unknown) / ``open`` (neither yet);
- **realized P&L** on sells, FIFO against buys inside the window; shares bought before
  the window are counted as unmatched rather than guessed.

Nothing here places or changes an order.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

from .broker.base import Fill
from .universe import yahoo_symbol

PLAN_LOOKBACK_DAYS = 30
MAX_DAYS = 180
OUTCOME_BARS = 200          # one candle request covers ~9 months of daily bars

Candles = Callable[[str, int], Awaitable[list[dict]]]       # (ticker, count) -> daily bars, oldest first


def ticker_for(symbol: str, market: str | None) -> str:
    """Broker symbol → Yahoo ticker. Without a listing, a 6-digit code is taken as KOSPI."""
    if market:
        return yahoo_symbol(symbol, market)
    if symbol.isdigit() and len(symbol) == 6:
        return f"{symbol}.KS"
    return symbol.replace(".", "-")


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round((a - b) / b * 100, 2)


def _alignment(side: str, plan: dict | None) -> str:
    if not plan:
        return "no_plan"
    bias = plan.get("bias")
    if bias == "long":
        return "with_plan" if side == "BUY" else "against_plan"
    if bias == "short":
        return "with_plan" if side == "SELL" else "against_plan"
    return "plan_flat"


def first_touch(bars: list[dict], after: date, stop: float, target: float, long: bool) -> dict:
    """Which of stop / target daily bars reached first strictly after the fill day."""
    for bar in bars:
        day = date.fromisoformat(bar["at"][:10])
        if day <= after:
            continue
        hit_stop = bar["low"] <= stop if long else bar["high"] >= stop
        hit_target = bar["high"] >= target if long else bar["low"] <= target
        if hit_stop and hit_target:
            return {"result": "both", "on": day.isoformat()}
        if hit_target:
            return {"result": "target", "on": day.isoformat()}
        if hit_stop:
            return {"result": "stop", "on": day.isoformat()}
    return {"result": "open", "on": None}


def _realize(fills: list[Fill]) -> dict[str, dict]:
    """FIFO per symbol: realized P&L of each SELL against earlier BUYs in the list (fees included)."""
    lots: dict[str, deque] = defaultdict(deque)       # symbol -> [qty, price, fee per share]
    out: dict[str, dict] = {}
    for fill in sorted(fills, key=lambda f: f.filled_at):
        if fill.side == "BUY":
            lots[fill.symbol].append([fill.quantity, fill.price, fill.fee / fill.quantity if fill.quantity else 0.0])
            continue
        if fill.side != "SELL":
            continue
        want, matched, cost, pnl = fill.quantity, 0.0, 0.0, 0.0
        sell_fee_each = fill.fee / fill.quantity if fill.quantity else 0.0
        queue = lots[fill.symbol]
        while want > 1e-12 and queue:
            lot = queue[0]
            take = min(want, lot[0])
            cost += take * (lot[1] + lot[2])
            pnl += take * (fill.price - sell_fee_each - lot[1] - lot[2])
            matched += take
            want -= take
            lot[0] -= take
            if lot[0] <= 1e-12:
                queue.popleft()
        out[fill.order_id] = {
            "matched_quantity": matched,
            "unmatched_quantity": round(want, 8),          # bought before the window: unknown cost
            "realized_pnl": round(pnl, 2) if matched else None,
            "realized_rate": round(pnl / cost * 100, 2) if matched and cost else None,
        }
    return out


async def build_review(fills: list[Fill], plans_for: Callable[[list[str], str], list[dict]],
                       listing_for: Callable[[str], dict | None], candles: Candles, start: date,
                       today: date) -> dict:
    tickers: dict[str, tuple[str, str | None]] = {}
    for fill in fills:
        if fill.symbol not in tickers:
            listed = listing_for(fill.symbol)
            tickers[fill.symbol] = (ticker_for(fill.symbol, (listed or {}).get("market")),
                                    (listed or {}).get("name"))
    since = (start - timedelta(days=PLAN_LOOKBACK_DAYS)).isoformat()
    # A DB read: off the event loop.
    plans = await asyncio.to_thread(plans_for, sorted({t for t, _ in tickers.values()}), since)
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for run in plans:                                       # newest first
        by_ticker[run["ticker"]].append(run)

    realized = _realize(fills)
    bars_cache: dict[str, list[dict]] = {}
    rows, errors = [], []
    for fill in sorted(fills, key=lambda f: f.filled_at, reverse=True):
        ticker, name = tickers[fill.symbol]
        day = date.fromisoformat(fill.filled_at[:10])
        run = next((r for r in by_ticker.get(ticker, [])
                    if r["trade_date"] <= day.isoformat()
                    and day - date.fromisoformat(r["trade_date"]) <= timedelta(days=PLAN_LOOKBACK_DAYS)), None)
        plan = (run or {}).get("plan") or None
        alignment = _alignment(fill.side, plan if run else None)
        outcome = None
        if alignment == "with_plan" and plan.get("stop_loss") is not None and plan.get("price_target") is not None:
            if ticker not in bars_cache:
                try:
                    bars_cache[ticker] = await candles(ticker, OUTCOME_BARS)
                except Exception as exc:  # noqa: BLE001 - one instrument without bars should not sink the page
                    bars_cache[ticker] = []
                    errors.append(f"{ticker}: {type(exc).__name__}: {exc}")
            if bars_cache[ticker]:
                outcome = first_touch(bars_cache[ticker], day, plan["stop_loss"], plan["price_target"],
                                      long=plan.get("bias") == "long")
        rows.append({
            "order_id": fill.order_id,
            "symbol": fill.symbol,
            "ticker": ticker,
            "name": name or ((run or {}).get("options") or {}).get("name"),
            "side": fill.side,
            "quantity": fill.quantity,
            "price": fill.price,
            "fee": fill.fee,
            "currency": fill.currency,
            "filled_at": fill.filled_at,
            "plan": None if not run else {
                "run_id": run["id"],
                "trade_date": run["trade_date"],
                "rating": plan.get("rating") if plan else None,
                "bias": plan.get("bias") if plan else None,
                "entry_price": plan.get("entry_price") if plan else None,
                "stop_loss": plan.get("stop_loss") if plan else None,
                "price_target": plan.get("price_target") if plan else None,
            },
            "alignment": alignment,
            "entry_gap_pct": _pct(fill.price, (plan or {}).get("entry_price")),
            "outcome": outcome,
            "realized": realized.get(fill.order_id),
        })

    pnl: dict[str, float] = defaultdict(float)
    for row in rows:
        if row["realized"] and row["realized"]["realized_pnl"] is not None:
            pnl[row["currency"]] += row["realized"]["realized_pnl"]
    count = lambda key, value: sum(1 for r in rows if r[key] == value)
    outcomes = [r["outcome"]["result"] for r in rows if r["outcome"]]
    return {
        "from": start.isoformat(),
        "to": today.isoformat(),
        "fills": rows,
        "summary": {
            "fills": len(rows),
            "with_plan": count("alignment", "with_plan"),
            "against_plan": count("alignment", "against_plan"),
            "plan_flat": count("alignment", "plan_flat"),
            "no_plan": count("alignment", "no_plan"),
            "outcomes": {k: outcomes.count(k) for k in ("target", "stop", "both", "open")},
            "realized_pnl": {k: round(v, 2) for k, v in sorted(pnl.items())},
        },
        "errors": errors,
    }
