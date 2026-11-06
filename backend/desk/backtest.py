"""Walk-forward backtest of the desk's playbooks (#144).

The deterministic part of a playbook (regime, zones, ladder, stop, targets: ``desk.playbook.build``)
needs only bars, so it can be replayed on history. At every step ``t`` the levels are computed from
the bars an analysis at the close of ``t`` could have seen (the horizon's primary window ending at
``t``, finished secondary bars only), the playbooks are built, and ``desk.outcome.judge_playbook``
scores them on the bars after ``t``. Nothing after ``t`` reaches the plan (``tests/test_backtest.py``
changes the future and checks the plan stays the same).

The model's edits to a playbook (the agents' Playbook lines) cannot be replayed; those are
measured by the live scoring (#137). Trades from consecutive steps overlap: the numbers measure
the rules, not a tradable equity curve.

    python -m desk.cli backtest SOXL --horizon swing --from 2018-01-01
    python -m desk.cli backtest --basket --sweep --out report.json
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from . import horizons, outcome, playbook, sr

VERSION = 1
BASKET = ("SOXL", "TQQQ", "NVDA", "QQQ", "005930.KS", "000660.KS")
# Separate tallies for the crashes the dip-catch is meant for (plan dates, inclusive).
CRASH_WINDOWS = {"2020-03": ("2020-02-19", "2020-04-30"), "2022": ("2022-01-01", "2022-12-31"),
                 "2025-04": ("2025-03-24", "2025-05-16")}
# One plan per this many primary bars (about once a session on the intraday frames).
STEP = {"scalp": 30, "intraday": 13, "short_swing": 7, "swing": 1}
# How many bars after the plan the judge may look at: past the entry window and the time stop.
LOOKAHEAD = {"scalp": 400, "intraday": 160, "short_swing": 80, "swing": 40}
SETTLED = ("t2", "t1_be", "stop", "time_stop", "flat")
COST_BPS = 10.0          # commission + slippage per side, in basis points of the fill
TRAIN_SHARE = 0.7        # the first 70% of the plan dates choose a parameter, the rest check it

# Parameters the sweep tries one at a time around the current values (desk.playbook constants;
# a name the module no longer has is skipped). "min_score" filters plans instead of changing them.
SWEEP: dict[str, list] = {
    "ZONE_WIDTH_ATR": [0.75, 1.0, 1.5],
    "LONG_STOP_ATR": [0.25, 0.5, 0.75],
    "SHORT_STOP_ATR": [0.2, 0.3, 0.5],
    "CONFIRM_ATR": [0.1, 0.25, 0.5],
    "SHARES": [{"crash": (0.25, 0.25, 0.5), "default": (0.34, 0.33, 0.33)},
               {"crash": (0.34, 0.33, 0.33), "default": (0.34, 0.33, 0.33)},
               {"crash": (0.5, 0.3, 0.2), "default": (0.5, 0.3, 0.2)}],
    "min_score": [0, 40, 60],
}


@dataclass
class Snapshot:
    """What an analysis at the close of bar ``t`` sees. ``sr`` is the levels; ``daily`` feeds the regime."""
    ticker: str
    t: int
    at: str
    plan_at: str
    sr: dict
    daily: list[dict]
    window: list[dict]


@dataclass
class Trade:
    ticker: str
    at: str
    side: str
    setup: str
    regime: str
    score: int | None
    status: str
    filled_shares: float = 0.0
    steps: int = 0
    took_t1: bool = False
    r: float | None = None            # per filled share over the planned risk (desk.outcome)
    r_size: float | None = None       # scaled by the filled share of the position
    r_net: float | None = None        # r_size after costs
    mae_atr: float | None = None      # worst move against the average fill, in the frame's ATR
    mfe_atr: float | None = None      # best move for it


@dataclass
class Costs:
    bps_per_side: float = COST_BPS


@dataclass
class Report:
    version: int
    horizon: str
    tickers: list[str]
    start: str | None
    end: str | None
    costs: dict
    baseline: dict
    by_setup: dict
    by_regime: dict
    by_steps: dict
    by_ticker: dict
    crash_windows: dict
    split: dict
    notes: list[str] = field(default_factory=list)
    sweep: list[dict] = field(default_factory=list)
    generated_at: str = ""


# ---------------------------------------------------------------- data


def history(ticker: str, h: horizons.Horizon, start: str | None) -> tuple[list[dict], list[dict], list[str]]:
    """Primary and secondary bars from Yahoo (unadjusted, like the live levels) reaching back far
    enough for ``start`` plus one primary window. Intraday frames only go as far as Yahoo keeps."""
    from .prices import HISTORY_LIMIT_DAYS, yahoo_candles

    notes = []
    days_back = (date.today() - date.fromisoformat(start)).days if start else 365 * 3
    primary_count = _count_for(h.primary, days_back) + h.primary_bars
    limit = HISTORY_LIMIT_DAYS.get(h.primary)
    if limit and days_back > limit:
        notes.append(f"{h.primary} 봉은 야후에서 약 {limit}일까지만 받을 수 있어 그 이전 구간은 빠집니다")
    primary = [asdict(b) for b in yahoo_candles(ticker, h.primary, primary_count)]
    secondary = [asdict(b) for b in yahoo_candles(ticker, h.secondary, _count_for(h.secondary, days_back) + h.secondary_bars)]
    return primary, secondary, notes


def _count_for(interval: str, days: int) -> int:
    per_day = {"1m": 390, "5m": 78, "15m": 26, "30m": 13, "60m": 7, "240m": 2, "1d": 5 / 7, "1w": 1 / 7}
    return int(days * per_day.get(interval, 1)) + 10


def _plan_at(bar_next: dict, h: horizons.Horizon, ticker: str) -> str:
    """When the plan starts counting: the next primary bar. Daily bars are stamped at the market's
    local midnight and judged from the session open (desk.outcome), so the start is that open."""
    at = outcome._at(bar_next["at"])
    if h.primary in ("1d", "1w", "1M"):
        at += outcome.session_open(ticker)
    return at.astimezone(UTC).isoformat(timespec="seconds")


def snapshot(ticker: str, h: horizons.Horizon, primary: list[dict], secondary: list[dict], t: int) -> Snapshot | None:
    """The levels an analysis at the close of ``primary[t]`` could have computed; None when the
    window is too short or the levels fail. Only ``primary[:t+1]`` and finished secondary bars are read."""
    if t + 1 >= len(primary):
        return None
    window = primary[max(0, t - h.primary_bars + 1): t + 1]
    cut = sr._local_day(primary[t]["at"], ticker)
    if h.secondary == "1d":          # the prior sessions under intraday bars (desk.sr.frame_bars)
        sec = [b for b in secondary if sr._local_day(b["at"], ticker) < cut]
    else:                            # secondary bars that had ended by the close of t
        sec = [b for b in secondary if sr._local_day(b["at"], ticker) + timedelta(days=h.secondary_days) <= cut]
    sec = sec[-h.secondary_bars:]
    try:
        levels = sr.compute(window, sec, None, weekly_complete=True, horizon=h.id)
    except ValueError:
        return None
    sr.add_trend(levels, window, sec, h, None)
    sr.add_volume_profile(levels, window, h)
    daily = window if h.primary == "1d" else sec
    return Snapshot(ticker, t, primary[t]["at"], _plan_at(primary[t + 1], h, ticker), levels, daily, window)


# ---------------------------------------------------------------- one plan


def _steps(side: dict, filled: float) -> int:
    """How many ladder steps the filled share covers (fills go in step order)."""
    total, n = 0.0, 0
    for e in side.get("entries") or []:
        if filled + 1e-6 < total + e["share"]:
            break
        total += e["share"]
        n += 1
    return n


def _excursions(side: dict, direction: int, future: list[dict], avg: float, exited_at: str | None,
                atr: float) -> tuple[float | None, float | None]:
    """MAE / MFE from the first bar that could fill the first step to the exit, in ATR. The
    window starts at the first touch, not at the exact fill, so it is a slight overstatement."""
    entries = side.get("entries") or []
    if not entries or not atr:
        return None, None
    first = entries[0]
    start = None
    for i, b in enumerate(future):
        if first["kind"] == "limit":
            hit = b["low"] <= first["price"] if direction > 0 else b["high"] >= first["price"]
        else:
            hit = b["high"] >= first["price"] if direction > 0 else b["low"] <= first["price"]
        if hit:
            start = i
            break
    if start is None:
        return None, None
    span = [b for b in future[start:] if exited_at is None or b["at"] <= exited_at] or future[start:start + 1]
    low, high = min(b["low"] for b in span), max(b["high"] for b in span)
    if direction > 0:
        mae, mfe = (avg - low) / atr, (high - avg) / atr
    else:
        mae, mfe = (high - avg) / atr, (avg - low) / atr
    return round(max(mae, 0.0), 2), round(max(mfe, 0.0), 2)


def trades_at(snap: Snapshot, primary: list[dict], h: horizons.Horizon, costs: Costs) -> list[Trade]:
    """Build the snapshot's playbooks and judge each side on the bars after it."""
    pb = playbook.build(snap.sr, snap.daily, snap.window, h, now=snap.plan_at)
    if not pb:
        return []
    future = primary[snap.t + 1: snap.t + 1 + LOOKAHEAD[h.id]]
    opens = outcome.session_open(snap.ticker)
    regime = pb["regime"]["id"]
    atr = pb["frame"]["atr"] or 0.0
    out = []
    for side_name, direction in (("long", 1), ("short", -1)):
        side = pb[side_name]
        if side.get("setup") in (None, "none"):
            continue
        res = outcome.judge_playbook(side, direction, future, h, snap.plan_at, opens)
        trade = Trade(snap.ticker, snap.at, side_name, side["setup"], regime, side.get("score"), res["status"])
        if res.get("filled_shares"):
            avg = res["avg_fill"]
            trade.filled_shares = res["filled_shares"]
            trade.steps = _steps(side, res["filled_shares"])
            trade.took_t1 = bool(res.get("took_t1"))
            trade.r, trade.r_size = res.get("r"), res.get("r_size")
            risk = abs(avg - side["stop"]) or 1e-9
            cost_r = 2 * costs.bps_per_side / 1e4 * avg / risk * res["filled_shares"]
            if trade.r_size is not None:
                trade.r_net = round(trade.r_size - cost_r, 3)
            trade.mae_atr, trade.mfe_atr = _excursions(side, direction, future, avg, res.get("exited_at"), atr)
        out.append(trade)
    return out


# ---------------------------------------------------------------- tallies


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def aggregate(trades: list[Trade]) -> dict:
    """Fill rate, how often T1 came before the stop, R (after costs) and excursions."""
    filled = [t for t in trades if t.filled_shares]
    settled = [t for t in filled if t.status in SETTLED]
    rs = [t.r_net for t in settled if t.r_net is not None]
    counts = {s: sum(1 for t in trades if t.status == s) for s in (*SETTLED, "open", "waiting", "unfilled", "invalid")}
    return {
        "plans": len(trades), "filled": len(filled), "settled": len(settled), **counts,
        "fill_rate": round(len(filled) / len(trades), 3) if trades else None,
        # T1 before the stop: every settled trade that took T1 (t2, t1_be, or a close after T1)
        "t1_first_rate": round(sum(1 for t in settled if t.took_t1) / len(settled), 3) if settled else None,
        "avg_r": _mean(rs), "median_r": _median(rs),
        # expectancy per settled trade and per plan (an unfilled plan is 0 R)
        "expectancy_r": _mean(rs),
        "expectancy_per_plan_r": round(sum(rs) / len(trades), 3) if trades and rs else None,
        "mae_atr_median": _median([t.mae_atr for t in settled if t.mae_atr is not None]),
        "mfe_atr_median": _median([t.mfe_atr for t in settled if t.mfe_atr is not None]),
    }


def _by(trades: list[Trade], key: Callable[[Trade], str]) -> dict:
    groups: dict[str, list[Trade]] = {}
    for t in trades:
        groups.setdefault(key(t), []).append(t)
    return {k: aggregate(v) for k, v in sorted(groups.items())}


def _in(at: str, lo: str, hi: str) -> bool:
    return lo <= at[:10] <= hi


def summarize(trades: list[Trade]) -> dict:
    plan_dates = sorted({t.at[:10] for t in trades})
    cut = plan_dates[int(len(plan_dates) * TRAIN_SHARE)] if plan_dates else None
    return {
        "baseline": aggregate(trades),
        "by_setup": _by(trades, lambda t: f"{t.side}:{t.setup}"),
        "by_regime": _by(trades, lambda t: f"{t.side}:{t.setup}:{t.regime}"),
        "by_steps": _by([t for t in trades if t.filled_shares], lambda t: f"{t.side}:{t.steps}"),
        "by_ticker": _by(trades, lambda t: t.ticker),
        "crash_windows": {name: aggregate([t for t in trades if _in(t.at, lo, hi)])
                          for name, (lo, hi) in CRASH_WINDOWS.items()},
        "split": {"cut": cut,
                  "train": aggregate([t for t in trades if cut and t.at[:10] < cut]),
                  "valid": aggregate([t for t in trades if cut and t.at[:10] >= cut])},
    }


# ---------------------------------------------------------------- runs


def snapshots(ticker: str, h: horizons.Horizon, primary: list[dict], secondary: list[dict],
              start: str | None = None, end: str | None = None, step: int | None = None,
              progress: Callable[[str], None] | None = None) -> Iterator[Snapshot]:
    step = step or STEP[h.id]
    first = h.primary_bars - 1 if h.primary == "1d" else max(h.primary_bars - 1, 0)
    total = len(primary)
    for t in range(first, total - 1, step):
        day = primary[t]["at"][:10]
        if (start and day < start) or (end and day > end):
            continue
        snap = snapshot(ticker, h, primary, secondary, t)
        if snap:
            yield snap
        if progress and t % (step * 100) < step:
            progress(f"{ticker} {day} ({t}/{total})")


@contextmanager
def patched(name: str, value) -> Iterator[None]:
    """Set a desk.playbook constant for the duration (the sweep); always put back."""
    if name == "min_score" or not hasattr(playbook, name):
        yield
        return
    old = getattr(playbook, name)
    setattr(playbook, name, value)
    try:
        yield
    finally:
        setattr(playbook, name, old)


def evaluate(snaps: Iterable[tuple[Snapshot, list[dict]]], h: horizons.Horizon, costs: Costs,
             min_score: int = 0) -> list[Trade]:
    out = []
    for snap, primary in snaps:
        out += [t for t in trades_at(snap, primary, h, costs) if (t.score or 0) >= min_score]
    return out


def sweep(snaps: list[tuple[Snapshot, list[dict]]], h: horizons.Horizon, costs: Costs,
          grid: dict[str, list] | None = None) -> list[dict]:
    """One parameter at a time around the current values; ranked by the training expectancy, with
    the held-out dates next to it (a choice that only wins on the training dates is over-fitted)."""
    grid = grid or SWEEP
    dates = sorted({s.at[:10] for s, _ in snaps})
    cut = dates[int(len(dates) * TRAIN_SHARE)] if dates else None
    rows = []
    for name, values in grid.items():
        if name != "min_score" and not hasattr(playbook, name):
            continue
        current = getattr(playbook, name, 0)
        for value in values:
            with patched(name, value):
                trades = evaluate(snaps, h, costs, min_score=value if name == "min_score" else 0)
            train = aggregate([t for t in trades if cut and t.at[:10] < cut])
            valid = aggregate([t for t in trades if cut and t.at[:10] >= cut])
            rows.append({"param": name, "value": value, "current": value == current,
                         "train": {k: train[k] for k in ("plans", "settled", "fill_rate", "t1_first_rate", "expectancy_r")},
                         "valid": {k: valid[k] for k in ("plans", "settled", "fill_rate", "t1_first_rate", "expectancy_r")}})
    rows.sort(key=lambda r: (r["train"]["expectancy_r"] is None, -(r["train"]["expectancy_r"] or 0)))
    return rows


def run(tickers: list[str], horizon: str | None = None, start: str | None = None, end: str | None = None,
        step: int | None = None, costs: Costs | None = None, do_sweep: bool = False,
        fetch: Callable[[str, horizons.Horizon, str | None], tuple[list[dict], list[dict], list[str]]] = history,
        progress: Callable[[str], None] | None = None) -> Report:
    h = horizons.get(horizon)
    costs = costs or Costs()
    notes: list[str] = []
    snaps: list[tuple[Snapshot, list[dict]]] = []
    for ticker in tickers:
        try:
            primary, secondary, more = fetch(ticker, h, start)
        except Exception as exc:  # noqa: BLE001 - one ticker's data failing leaves the others
            notes.append(f"{ticker}: 봉을 받지 못함 — {type(exc).__name__}: {exc}")
            continue
        notes += [f"{ticker}: {n}" for n in more]
        snaps += [(s, primary) for s in snapshots(ticker, h, primary, secondary, start, end, step, progress)]
    trades = evaluate(snaps, h, costs)
    if h.primary != "1d":
        notes.append("분봉 · 시간봉 보기는 야후 보관 기간 때문에 표본이 적습니다")
    notes.append("이어진 시점의 계획은 기간이 겹칩니다 — 규칙의 성적이지 실제 계좌 곡선이 아닙니다")
    report = Report(VERSION, h.id, list(tickers), start, end, asdict(costs), **summarize(trades), notes=notes,
                    generated_at=datetime.now(UTC).isoformat(timespec="seconds"))
    if do_sweep:
        report.sweep = sweep(snaps, h, costs)
    return report


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def print_summary(report: Report, out=None) -> None:
    out = out or sys.stdout
    cols = ("plans", "filled", "settled", "fill_rate", "t1_first_rate", "expectancy_r", "median_r", "mae_atr_median")
    print(f"backtest {report.horizon} {','.join(report.tickers)} {report.start or ''}~{report.end or ''} "
          f"(cost {report.costs['bps_per_side']}bp/side)", file=out)
    print("group".ljust(34) + "".join(c[:12].rjust(13) for c in cols), file=out)
    rows = [("ALL", report.baseline), *report.by_setup.items(), *report.by_regime.items(),
            *((f"split:{k}", v) for k, v in report.split.items() if k != "cut"),
            *((f"crash:{k}", v) for k, v in report.crash_windows.items() if v["plans"])]
    for name, agg in rows:
        print(name[:33].ljust(34) + "".join(_fmt(agg.get(c)).rjust(13) for c in cols), file=out)
    for row in report.sweep[:12]:
        print(f"sweep {row['param']}={row['value']}{' (now)' if row['current'] else ''}: "
              f"train {_fmt(row['train']['expectancy_r'])} / valid {_fmt(row['valid']['expectancy_r'])}", file=out)
    for note in report.notes:
        print(f"note: {note}", file=out)
