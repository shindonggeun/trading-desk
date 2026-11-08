"""Touch entry vs lower-bar trigger entry (#149), on the same plans and the same lower bars.

For every plan the basket backtest (desk.backtest) builds, both arms walk the lower bars
(``trigger.LOWER``: 60-minute bars under a swing plan) from the plan's start:

- ``touch``: buy (sell) the zone's edge the first time the price reaches it; the plan's stop.
- ``trigger``: desk.trigger.scan from the same start; the fill at the trigger, its invalidation
  as the stop. A trigger at or past T1 is not taken.

Both then exit the same way on the lower bars: half at T1 with the rest's stop moved to the entry,
the rest at T2, a bar that reaches both the stop and a target counted as the stop, whatever is open
at the end of the look-ahead closed at the last close. R is each arm's own entry-to-stop, so the
report also gives the result in the plan's ATR (what a fixed position earns) and the MAE.
Yahoo keeps about 730 days of 60-minute bars: the sample is the last two years only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

from . import backtest, horizons, playbook, trigger

ENTRY_LOWER_BARS = {"60m": 70, "5m": 156, "1m": 120}     # the entry window: ~10 sessions / 2 sessions / 2 hours


@dataclass
class Arm:
    ticker: str
    at: str
    side: str
    arm: str
    filled: bool
    r: float | None = None            # over the arm's own risk, after costs
    atr_pnl: float | None = None      # the same move in the plan's ATR
    mae_atr: float | None = None
    took_t1: bool = False
    risk_atr: float | None = None


def _exit(bars: list[dict], up: bool, entry: float, stop: float, t1: float | None, t2: float | None) -> tuple[float, bool, float]:
    """(average exit, took T1, worst move against the entry) over ``bars`` starting at the fill bar.
    On the fill bar only the stop counts (its range before the fill is unknown: a target reached
    there may have come before the entry; a stop is read against the trade, as everywhere here)."""
    sgn = 1 if up else -1
    half, worst, cur_stop = None, 0.0, stop
    for n, b in enumerate(bars):
        worst = max(worst, sgn * (entry - (b["low"] if up else b["high"])))
        far, near = (b["low"], b["high"]) if up else (b["high"], b["low"])
        if sgn * (far - cur_stop) <= 0:
            out = cur_stop
            return ((half + out) / 2 if half is not None else out), half is not None, worst
        if n == 0:
            continue
        if half is None and t1 is not None and sgn * (near - t1) >= 0:
            half, cur_stop = t1, entry
        if half is not None and t2 is not None and sgn * (near - t2) >= 0:
            return (half + t2) / 2, True, worst
    last = bars[-1]["close"] if bars else entry
    return ((half + last) / 2 if half is not None else last), half is not None, worst


def _arm(name: str, ticker: str, day: str, side_name: str, up: bool, entry: float | None, stop: float | None,
         fill_at: str | None, side: dict, rest: list[dict], atr: float, bps: float) -> Arm:
    if entry is None or stop is None or not fill_at or (entry - stop) * (1 if up else -1) <= 0:
        return Arm(ticker, day, side_name, name, False)
    t = {x.get("role"): x.get("price") for x in side.get("targets") or []}
    span = [b for b in rest if b["at"] >= fill_at]
    out, took, worst = _exit(span, up, entry, stop, t.get("t1"), t.get("t2"))
    risk = abs(entry - stop)
    move = (out - entry) if up else (entry - out)
    cost = 2 * bps / 1e4 * entry
    return Arm(ticker, day, side_name, name, True, r=round((move - cost) / risk, 3),
               atr_pnl=round((move - cost) / atr, 3) if atr else None,
               mae_atr=round(worst / atr, 2) if atr else None, took_t1=took,
               risk_atr=round(risk / atr, 2) if atr else None)


def _touch(side: dict, bars: list[dict], up: bool) -> tuple[float | None, str | None]:
    edge = side["zone"]["high"] if up else side["zone"]["low"]
    for b in bars:
        if (b["low"] <= edge) if up else (b["high"] >= edge):
            return (min(edge, b["open"]) if up else max(edge, b["open"])), b["at"]
    return None, None


def plan_arms(snap: backtest.Snapshot, primary: list[dict], lower: list[dict], h: horizons.Horizon,
              bps: float) -> list[Arm]:
    pb = playbook.build(snap.sr, snap.daily, snap.window, h, now=snap.plan_at, today=snap.today, vix=snap.vix,
                        calibrate=False)                # #147: not graded by its own results
    if not pb:
        return []
    end_t = min(snap.t + backtest.LOOKAHEAD[h.id], len(primary) - 1)
    end_at = primary[end_t]["at"]
    rest = [b for b in lower if snap.plan_at <= b["at"] <= end_at]
    if len(rest) < 2:
        return []
    window = rest[:ENTRY_LOWER_BARS[trigger.LOWER[h.primary]]]
    atr = pb["frame"]["atr"]
    if not atr:
        return []
    out = []
    for side_name, up in (("long", True), ("short", False)):
        side = pb[side_name]
        if side.get("setup") in (None, "none") or not side.get("zone"):
            continue
        price, at = _touch(side, window, up)
        out.append(_arm("touch", snap.ticker, snap.day, side_name, up, price, side.get("stop"), at, side, rest, atr, bps))
        res = trigger.scan(window, side, up, atr)
        fill = res and res["state"] == "triggered" and res["fill"]
        t1 = next((x["price"] for x in side.get("targets") or [] if x.get("role") == "t1"), None)
        if fill and t1 is not None and (fill >= t1 if up else fill <= t1):
            fill = None                                      # the turn ran to T1 before it triggered
        out.append(_arm("trigger", snap.ticker, snap.day, side_name, up, fill or None,
                        res and res["invalidation"], res and res["filled_at"], side, rest, atr, bps))
    return out


def tally(arms: list[Arm]) -> dict:
    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 3) if xs else None
    filled = [a for a in arms if a.filled]
    return {"plans": len(arms), "filled": len(filled),
            "fill_rate": round(len(filled) / len(arms), 3) if arms else None,
            "expectancy_r": mean(a.r for a in filled), "atr_per_fill": mean(a.atr_pnl for a in filled),
            "atr_per_plan": round(sum(a.atr_pnl or 0 for a in filled) / len(arms), 3) if arms else None,
            "t1_rate": round(sum(a.took_t1 for a in filled) / len(filled), 3) if filled else None,
            "mae_atr": mean(a.mae_atr for a in filled), "risk_atr": mean(a.risk_atr for a in filled)}


def run(tickers: list[str], horizon: str = "swing", start: str | None = None, bps: float = backtest.COST_BPS,
        fetch_lower=None, fetch=None, progress=None) -> dict:
    """{"horizon", "lower", "rows": {arm: tally, f"{arm}:{side}": ..., f"{arm}:valid": ...}, "notes"}."""
    from .prices import HISTORY_LIMIT_DAYS, yahoo_candles

    h = horizons.get(horizon)
    lower_iv = trigger.LOWER.get(h.primary)
    if not lower_iv:
        raise ValueError(f"{h.id} has no lower bars to trigger on")
    limit = HISTORY_LIMIT_DAYS[lower_iv] - 5
    first = (date.today() - timedelta(days=limit)).isoformat()
    start = max(start or first, first)
    fetch = fetch or backtest.history
    fetch_lower = fetch_lower or (lambda t: [asdict(b) for b in yahoo_candles(t, lower_iv, backtest._count_for(lower_iv, limit))])
    notes = [f"하위 봉 {lower_iv} 은 야후에서 약 {limit}일까지만 받을 수 있어 {start} 부터만 비교합니다"]
    arms: list[Arm] = []
    for ticker in tickers:
        try:
            primary, secondary, _ = fetch(ticker, h, start)
            lower = fetch_lower(ticker)
            for snap in backtest.snapshots(ticker, h, primary, secondary, start, None, None, progress):
                arms += plan_arms(snap, primary, lower, h, bps)
        except Exception as exc:  # noqa: BLE001 - one ticker failing leaves the others
            notes.append(f"{ticker}: 건너뜀 — {type(exc).__name__}: {exc}")
    cut = backtest.split_cut(a.at for a in arms)
    rows = {}
    for name in ("touch", "trigger"):
        mine = [a for a in arms if a.arm == name]
        rows[name] = tally(mine)
        for side in ("long", "short"):
            rows[f"{name}:{side}"] = tally([a for a in mine if a.side == side])
        if cut:
            rows[f"{name}:train"] = tally([a for a in mine if a.at < cut])
            rows[f"{name}:valid"] = tally([a for a in mine if a.at >= cut])
    return {"horizon": h.id, "lower": lower_iv, "start": start, "tickers": list(tickers), "cost_bps": bps,
            "rows": rows, "notes": notes}


def print_report(rep: dict, out=None) -> None:
    import sys
    out = out or sys.stdout
    print(f"{rep['horizon']} plans, entries on {rep['lower']} bars from {rep['start']}: {', '.join(rep['tickers'])}", file=out)
    cols = ("plans", "filled", "fill_rate", "expectancy_r", "atr_per_fill", "atr_per_plan", "t1_rate", "mae_atr", "risk_atr")
    print("row".ljust(16) + "".join(c.rjust(13) for c in cols), file=out)
    for name, row in rep["rows"].items():
        print(name.ljust(16) + "".join(("-" if row[c] is None else str(row[c])).rjust(13) for c in cols), file=out)
    for n in rep["notes"]:
        print(f"- {n}", file=out)
