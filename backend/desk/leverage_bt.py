"""A leveraged product traded on its underlying's plan (#239 stage 3), on the same days.

For each leveraged product (desk.underlying.leverage_of: SOXL → SOXX, TQQQ → QQQ …) the basket
backtest's swing plans are built twice a day apart:

- ``own``: the product's own playbooks, judged on its bars (what the desk does now).
- ``under``: the underlying's playbooks - its zone, ladder, stop and targets read on the steadier
  index - converted into the product's prices with the realized beta (``BETA_BARS`` daily returns
  of the product on the underlying's) around that day's two closes, and judged on the product's bars.

Each arm is also given with the decay filter: a 3x product held N days loses about
(L² - L) / 2 · σ² · N to daily rebalancing (σ the underlying's daily volatility, ``VOL_BARS``),
whatever the index does; a plan whose decay over its time stop eats more than ``DECAY_MAX`` of the
way to T1 is skipped (``…+decay``). R is desk.outcome's, after costs, as everywhere in desk.backtest;
the held-out part is the last 30% of the plan dates (desk.backtest.split_cut).

Measured 2026-11-10 (swing, SOXL · TQQQ 2019-, 10bp a side, held-out from 2024-05-20):
own 3304 fills, -0.071R (held-out 1022, -0.046R), win 50%; under 3061 fills, -0.077R (held-out
917, -0.087R), win 48% - SOXL on SOXX's plan a little better (-0.053R vs -0.070R), TQQQ on QQQ's
worse (-0.103R vs -0.072R). The decay filter skips only 7 / 15 plans at DECAY_MAX 0.5 (a swing's
T1 is far beyond a few days' rebalancing loss) and changes nothing. Nothing adopted: the product's
own plan stays; the report is kept to re-check when the plans change.
"""

from __future__ import annotations

from dataclasses import asdict
from itertools import pairwise

from . import backtest, horizons
from .underlying import leverage_of

BETA_BARS = 60          # daily returns the beta is fitted on (about three months)
VOL_BARS = 20           # the underlying's daily volatility for the decay estimate
DECAY_MAX = 0.5         # skip a plan whose decay to its time stop eats more than this of the way to T1
ARMS = ("own", "under", "own+decay", "under+decay")


def _returns(closes: list[float]) -> list[float]:
    return [b / a - 1 for a, b in pairwise(closes) if a]


def realized_beta(product: list[float], underlying: list[float], n: int = BETA_BARS) -> float | None:
    """The product's daily return on the underlying's, least squares over the last ``n`` days."""
    rp, ru = _returns(product[-(n + 1):]), _returns(underlying[-(n + 1):])
    k = min(len(rp), len(ru))
    if k < n // 2:
        return None
    rp, ru = rp[-k:], ru[-k:]
    mp, mu = sum(rp) / k, sum(ru) / k
    var = sum((x - mu) ** 2 for x in ru)
    return sum((x - mu) * (y - mp) for x, y in zip(ru, rp, strict=True)) / var if var else None


def decay_pct(underlying: list[float], leverage: float, bars: int, n: int = VOL_BARS) -> float | None:
    """The product's expected loss to daily rebalancing over ``bars`` days, as a fraction."""
    r = _returns(underlying[-(n + 1):])
    if len(r) < n // 2:
        return None
    m = sum(r) / len(r)
    var = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    return (leverage * leverage - leverage) / 2 * var * bars


def to_product(pb: dict, anchor_p: float, anchor_u: float, beta: float) -> dict:
    """The underlying's playbooks in the product's prices: p = P0 · (1 + β · (u / U0 - 1))."""
    def conv(v):
        if v is None:
            return None
        out = anchor_p * (1 + beta * (v / anchor_u - 1))
        return round(out, 2) if out > 0 else None

    out = {**pb, "frame": {**pb["frame"], "reference_price": anchor_p,
                           "atr": (pb["frame"].get("atr") or 0) * beta * anchor_p / anchor_u}}
    for name in ("long", "short"):
        side = pb.get(name)
        if not side or side.get("setup") in (None, "none"):
            continue
        s = dict(side)
        if s.get("zone"):
            s["zone"] = {**s["zone"], **{k: conv(s["zone"].get(k)) for k in ("low", "high", "mid")}}
        s["entries"] = [{**e, "price": conv(e["price"])} for e in s.get("entries") or []]
        s["stop"] = conv(s.get("stop"))
        s["avg_entry"] = conv(s.get("avg_entry"))
        s["targets"] = [{**t, "price": conv(t["price"])} for t in s.get("targets") or []]
        if s["stop"] is None or s["avg_entry"] is None or any(e["price"] is None for e in s["entries"]):
            s = {**s, "setup": "none"}
        else:
            s["risk"] = round(abs(s["avg_entry"] - s["stop"]), 2)
        out[name] = s
    return out


def _decayed(pb: dict, side_name: str, decay: float | None, bars: int) -> bool:
    """True when the decay over the time stop eats more than DECAY_MAX of the way to T1."""
    side = pb.get(side_name) or {}
    if decay is None or not side.get("targets") or not side.get("avg_entry"):
        return False
    hold = (side.get("time_stop") or {}).get("bars") or bars
    gain = abs(side["targets"][0]["price"] / side["avg_entry"] - 1)
    return gain > 0 and decay * hold / bars > DECAY_MAX * gain


def pair_trades(product: str, h: horizons.Horizon, start: str, costs: backtest.Costs, vix=None, market=None,
                fetch=None, progress=None) -> dict[str, list[backtest.Trade]]:
    """{arm: trades} for one leveraged product."""
    lev = leverage_of(product)
    if not lev:
        raise ValueError(f"{product} is not a leveraged product")
    under, leverage, _ = lev
    fetch = fetch or backtest.history
    p_primary, p_secondary, _ = fetch(product, h, start)
    u_primary, u_secondary, _ = fetch(under, h, start)
    p_index = {b["at"][:10]: i for i, b in enumerate(p_primary)}
    u_index = {b["at"][:10]: i for i, b in enumerate(u_primary)}
    look = backtest.LOOKAHEAD[h.id]
    own_snaps = {s.day: s for s in backtest.snapshots(product, h, p_primary, p_secondary, start, None, None, progress,
                                                       vix=vix, market=market)}
    out: dict[str, list[backtest.Trade]] = {a: [] for a in ARMS}
    for snap in backtest.snapshots(under, h, u_primary, u_secondary, start, None, None, progress, vix=vix,
                                   market=market):
        tp, tu = p_index.get(snap.day), u_index.get(snap.day)
        own = own_snaps.get(snap.day)
        if tp is None or tu is None or own is None:
            continue
        p_closes = [b["close"] for b in p_primary[: tp + 1]]
        u_closes = [b["close"] for b in u_primary[: tu + 1]]
        beta = realized_beta(p_closes, u_closes)
        future = p_primary[tp + 1: tp + 1 + look]
        if beta is None or beta <= 0 or len(future) < look:
            continue
        decay = decay_pct(u_closes, leverage, 1)
        own_pb = backtest.playbooks_at(own, h)
        u_pb = backtest.playbooks_at(snap, h)
        conv = to_product(u_pb, p_closes[-1], u_closes[-1], beta) if u_pb else None
        for arm, pb in (("own", own_pb), ("under", conv)):
            if not pb:
                continue
            trades = backtest.judge(pb, own, future, h, costs, ticker=product)
            out[arm] += trades
            out[f"{arm}+decay"] += [t for t in trades if not _decayed(pb, t.side, decay, 1)]
    return out


def run(products: list[str] | None = None, horizon: str = "swing", start: str | None = "2019-01-01",
        costs: backtest.Costs | None = None, fetch=None, progress=None, market=True, vix=True) -> dict:
    """{"horizon", "products", "cut", "rows": {f"{arm}" / f"{arm}:valid" / f"{arm}:{product}": aggregate}, "notes"}."""
    h = horizons.get(horizon)
    costs = costs or backtest.Costs()
    products = products or [t for t in backtest.BASKET if leverage_of(t)]
    notes: list[str] = []
    if vix is True:
        try:
            vix = backtest.vix_closes(start)
        except Exception as exc:  # noqa: BLE001 - the regime reads without VIX
            notes.append(f"^VIX 를 받지 못함 — {type(exc).__name__}: {exc}")
            vix = None
    if market is True:
        try:
            market = backtest.market_risk(start, products)
            market = market if market.series else None
        except Exception as exc:  # noqa: BLE001 - the market gate off
            notes.append(f"시장 투매 지표를 받지 못함 — {type(exc).__name__}: {exc}")
            market = None
    by: dict[str, dict[str, list[backtest.Trade]]] = {}
    for product in products:
        try:
            by[product] = pair_trades(product, h, start, costs, vix, market, fetch, progress)
        except Exception as exc:  # noqa: BLE001 - one pair failing leaves the others
            notes.append(f"{product}: 건너뜀 — {type(exc).__name__}: {exc}")
    every = [t for arms in by.values() for t in arms["own"]]
    cut = backtest.split_cut(t.at for t in every)
    keys = ("plans", "filled", "settled", "fill_rate", "t1_first_rate", "expectancy_r", "expectancy_per_plan_r",
            "mae_atr_median")
    rows: dict[str, dict] = {}
    for arm in ARMS:
        trades = [t for arms in by.values() for t in arms[arm]]
        rows[arm] = _row(trades, keys)
        if cut:
            _, valid = backtest.split(trades, cut, h)
            rows[f"{arm}:valid"] = _row(valid, keys)
        for product, arms in by.items():
            rows[f"{arm}:{product}"] = _row(arms[arm], keys)
    return {"horizon": h.id, "products": list(by), "start": start, "cut": cut, "costs": asdict(costs),
            "decay_max": DECAY_MAX, "beta_bars": BETA_BARS, "rows": rows, "notes": notes}


def _row(trades: list[backtest.Trade], keys: tuple[str, ...]) -> dict:
    agg = backtest.aggregate(trades)
    settled = [t for t in trades if t.filled_shares and t.r_net is not None]
    wins = sum(1 for t in settled if t.r_net > 0)
    return {**{k: agg[k] for k in keys}, "win_rate": round(wins / len(settled), 3) if settled else None,
            "big_win_rate": round(sum(1 for t in settled if t.r_net >= 3) / len(settled), 3) if settled else None}


def print_report(rep: dict, out=None) -> None:
    import sys
    out = out or sys.stdout
    print(f"{rep['horizon']} plans from {rep['start']}: {', '.join(rep['products'])} · held-out from {rep['cut']}",
          file=out)
    cols = ("plans", "filled", "fill_rate", "win_rate", "t1_first_rate", "expectancy_r", "expectancy_per_plan_r",
            "big_win_rate")
    print("row".ljust(22) + "".join(c.rjust(14) for c in cols), file=out)
    for name, row in rep["rows"].items():
        cells = "".join(("-" if row.get(c) is None else str(row[c])).rjust(14) for c in cols)
        print(name.ljust(22) + cells, file=out)
    for n in rep["notes"]:
        print(f"- {n}", file=out)
