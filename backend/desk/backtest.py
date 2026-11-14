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

With the market's indices loaded (#146, ``desk.risk_regime``) the playbooks are gated the way a live
run gates them, and ``risk_gate`` puts the same plans ungated next to them.
"""

from __future__ import annotations

import bisect
import statistics
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from . import (
    calibration,
    entry_features,
    entry_now,
    exits,
    horizons,
    outcome,
    playbook,
    playbook_live,
    risk_regime,
    setup_filter,
    sr,
    summary,
    value_swing,
)
from .underlying import leverage_of

VERSION = 1
BASKET = ("SOXL", "TQQQ", "NVDA", "QQQ", "005930.KS", "000660.KS")
# Separate tallies for the crashes the dip-catch is meant for (plan dates, inclusive).
CRASH_WINDOWS = {"2020-03": ("2020-02-19", "2020-04-30"), "2022": ("2022-01-01", "2022-12-31"),
                 "2025-04": ("2025-03-24", "2025-05-16")}
# One plan per this many primary bars: every daily bar, about once a session on 60-minute bars,
# every ~65 minutes on 5-minute bars and every 30 minutes on 1-minute bars.
STEP = {"scalp": 30, "intraday": 13, "short_swing": 7, "swing": 1}
# How many bars after the plan the judge may look at: past the entry window and the time stop.
LOOKAHEAD = {"scalp": 400, "intraday": 160, "short_swing": 80, "swing": 40}
SETTLED = ("t2", "t1_be", "stop", "time_stop", "flat")
# Still in the trade when the look-ahead ends (after T1 the stop sits at break-even and the time
# stop no longer applies): marked at the last close so winners are not dropped from the averages.
MARKED = ("open",)
# Plans this close before the train/valid cut are left out of training: their trades run into the
# validation dates.
EMBARGO_DAYS = {"scalp": 1, "intraday": 3, "short_swing": 20, "swing": 60}
# A sweep row needs this many settled trades on the training dates to rank (or it is listed last).
MIN_SETTLED = 100
# #284: a feature bucket needs this many settled trades on the held-out dates to count as checked
MIN_VALID = 30
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
    # #145: 0 leaves the source out - a source stays on only where it beats 0 on both halves
    "AVWAP_WEIGHT": [0.0, 0.5, 1.0, 1.5],
    "GAP_FILL_WEIGHT": [0.0, 0.5, 1.0, 1.5],
}


@dataclass
class Snapshot:
    """What an analysis at the close of bar ``t`` sees. ``sr`` is the levels; ``daily`` feeds the regime."""
    ticker: str
    t: int
    at: str
    day: str                          # the market's own date of bar t (Korean bars carry the day before in UTC)
    plan_at: str
    sr: dict
    daily: list[dict]
    window: list[dict]
    today: dict | None = None         # the forming session on intraday frames (as the live run builds)
    vix: float | None = None          # ^VIX close on that day
    risk: dict | None = None          # desk.risk_regime.score on that day (#146)
    weekly: list[dict] | None = None  # the finished weekly bars on a swing frame (#167, desk.value_swing)


@dataclass
class Trade:
    ticker: str
    at: str                           # the plan's local date (YYYY-MM-DD)
    side: str
    setup: str
    regime: str
    score: int | None
    status: str
    filled_shares: float = 0.0
    steps: str = ""                   # the filled ladder steps in fill order, e.g. "1+3"
    took_t1: bool = False
    risk_phase: str = "unknown"       # desk.risk_regime phase on the plan's day (#146)
    r: float | None = None            # per filled share over the planned risk (desk.outcome)
    r_size: float | None = None       # scaled by the filled share of the position
    r_net: float | None = None        # r_size after costs
    mae_atr: float | None = None      # worst move against the average fill, in the frame's ATR
    mfe_atr: float | None = None      # best move for it
    direction: str = "neutral"        # with | against | neutral: the side against desk.direction (#166)
    held_bars: int | None = None      # primary bars from the first fill to the exit (the look-ahead's end if open)
    reach: int | None = None          # targets T1..Tn reached in order before the original stop (#167)
    exits: dict | None = None         # r_net under each exit rule on the same fills (#239, desk.exits)
    kind: str = "stock"               # stock | leveraged (#239: the product's own record)
    features: dict | None = None      # the chart at the plan (#284, desk.entry_features) - never changes the plan
    entry_kind: str | None = None     # the first step's kind (limit | stop) and its distance from the price
    distance_atr: float | None = None  # at the plan, in the frame's ATR (#166 stage 5: the #312 reach)


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
    by_direction: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    sweep: list[dict] = field(default_factory=list)
    generated_at: str = ""
    by_risk: dict = field(default_factory=dict)
    risk_gate: dict = field(default_factory=dict)   # the same plans with the #146 rule on / off
    equity: dict = field(default_factory=dict)      # cumulative R by plan day (#192, ``equity``)
    value_swing: dict = field(default_factory=dict)  # the oversold swing buy on its own (#167)
    exits: dict = field(default_factory=dict)        # the exit rules side by side (#239, ``exit_report``)
    features: dict = field(default_factory=dict)     # R by the chart at the plan (#284, ``feature_report``)
    ev_gate: dict = field(default_factory=dict)      # the #312 expected-R gate on / off (#166 stage 5, ``ev_gate_report``)


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


def local_day(at: str, ticker: str) -> str:
    return sr._local_day(at, ticker).isoformat()


def snapshot(ticker: str, h: horizons.Horizon, primary: list[dict], secondary: list[dict], t: int,
             vix: dict[str, float] | None = None, market: MarketRisk | None = None) -> Snapshot | None:
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
    day = cut.isoformat()
    return Snapshot(ticker, t, primary[t]["at"], day, _plan_at(primary[t + 1], h, ticker), levels, daily, window,
                    today=playbook_live.today(window, h), vix=_vix_on(vix, day),
                    weekly=sec if h.secondary == "1w" else None,
                    risk=market.on(day, ticker, intraday=h.primary not in ("1d", "1w", "1M"))
                    if market else None)


def _vix_on(vix: dict[str, float] | None, day: str) -> float | None:
    """The last ^VIX close on or before ``day`` (the US close a Korean morning would have seen)."""
    if not vix:
        return None
    best = None
    for d, v in vix.items():
        if d <= day and (best is None or d > best[0]):
            best = (d, v)
    return best[1] if best else None


class MarketRisk:
    """desk.risk_regime's indices over the whole history, scored as of a plan's day (each day once
    per market: the basket's tickers share them)."""

    def __init__(self, series: risk_regime.Series) -> None:
        self.series = series
        self._days = {k: [b["day"] for b in v] for k, v in series.items()}
        self._memo: dict[tuple[str, bool, bool], dict | None] = {}

    def on(self, day: str, ticker: str, intraday: bool = False) -> dict | None:
        """``intraday``: a plan made during the session of ``day`` (minute or hourly bars) - that
        day's index closes were still ahead of it, so only the days before count."""
        korean = risk_regime.is_korean(ticker)
        key = (day, korean, intraday)
        if key not in self._memo:
            cut = {}
            for symbol, bars in self.series.items():
                days = self._days[symbol]
                # as risk_regime.as_of: a Korean plan sees the US session of the day before
                strict = risk_regime.strict_for(symbol, korean, intraday)
                end = bisect.bisect_left(days, day) if strict else bisect.bisect_right(days, day)
                if end:
                    cut[symbol] = bars[max(0, end - risk_regime.HISTORY_BARS): end]
            main = risk_regime.main_index(cut, korean)
            self._memo[key] = risk_regime.score(cut, korean) if main else None
        return self._memo[key]


def market_risk(start: str | None, tickers: list[str]) -> MarketRisk:
    days_back = (date.today() - date.fromisoformat(start)).days if start else 365 * 3
    symbols = risk_regime.symbols_for_all(tickers)
    return MarketRisk(risk_regime.fetch(symbols, count=_count_for("1d", days_back) + 300))


# ---------------------------------------------------------------- one plan


def _steps(steps: list[int] | None) -> str:
    """The filled steps in fill order ("1+3" = the first limit and the confirmation, not the second)."""
    return "+".join(str(s) for s in steps or [])


def _excursions(direction: int, future: list[dict], avg: float, first_fill_at: str | None, exited_at: str | None,
                atr: float) -> tuple[float | None, float | None]:
    """MAE / MFE from the first fill's bar to the exit (or the last bar looked at), in ATR."""
    if not first_fill_at or not atr:
        return None, None
    span = [b for b in future if b["at"] >= first_fill_at and (exited_at is None or b["at"] <= exited_at)]
    if not span:
        return None, None
    low, high = min(b["low"] for b in span), max(b["high"] for b in span)
    if direction > 0:
        mae, mfe = (avg - low) / atr, (high - avg) / atr
    else:
        mae, mfe = (high - avg) / atr, (avg - low) / atr
    return round(max(mae, 0.0), 2), round(max(mfe, 0.0), 2)


def _held_and_reach(direction: int, future: list[dict], side: dict, first_fill_at: str | None,
                    exited_at: str | None) -> tuple[int | None, int | None]:
    """(bars held, targets reached) for a filled side. Held: from the first fill's bar to the exit's
    (to the look-ahead's end while open). Reached: how far down the target list the price went before
    the original stop - a bar touching both counts the stop first, as desk.outcome does."""
    at = [b["at"] for b in future]
    if first_fill_at not in at:
        return None, None
    i0 = at.index(first_fill_at)
    held = (at.index(exited_at) if exited_at in at else len(future) - 1) - i0
    stop, targets = side.get("stop"), [t["price"] for t in side.get("targets") or [] if t.get("price") is not None]
    reached = 0
    for bar in future[i0:]:
        if stop is not None and (bar["low"] <= stop if direction > 0 else bar["high"] >= stop):
            break
        best = bar["high"] if direction > 0 else bar["low"]
        while reached < len(targets) and (best >= targets[reached] if direction > 0 else best <= targets[reached]):
            reached += 1
        if reached == len(targets):
            break
    return held, reached


def trades_at(snap: Snapshot, primary: list[dict], h: horizons.Horizon, costs: Costs,
              gate: bool = True, single: bool = False) -> list[Trade]:
    """Build the snapshot's playbooks and judge each side on the bars after it. ``gate`` applies
    the market's selling rule (#146) when the snapshot has a score. ``single`` (#221) buys each side
    all at once at desk.summary's single price instead of its ladder (a side without one is skipped)."""
    pb = playbooks_at(snap, h, gate)
    if not pb:
        return []
    future = primary[snap.t + 1: snap.t + 1 + LOOKAHEAD[h.id]]
    return judge(pb, snap, future, h, costs, single=single)


def playbooks_at(snap: Snapshot, h: horizons.Horizon, gate: bool = True) -> dict | None:
    """The snapshot's playbooks as a live run would build them (market gate, oversold swing buy)."""
    pb = playbook.build(snap.sr, snap.daily, snap.window, h, now=snap.plan_at, today=snap.today, vix=snap.vix,
                        calibrate=False,                # #147: not graded by its own results
                        market=snap.risk)   # #166: the direction's index rules stay on; the gate is #146 below
    if gate:
        pb = risk_regime.apply(pb, snap.risk)
    # #167: the oversold swing buy takes the long slot when its evidence is there (no record: no conviction)
    # (gate off = the #146 selling score unused here: the fear bundle does not read it either; the
    # direction above still gets the market for its index rules - #166 stage 4)
    pb = value_swing.apply(pb, snap.daily, snap.weekly, h.id, snap.sr, snap.risk if gate else None, snap.vix,
                           calibrate=False)
    # #285: the setups kept where the chart showed an edge, scored on it
    return setup_filter.apply(pb, snap.daily, snap.weekly, snap.risk, h.id, calibrate=False)


def judge(pb: dict, snap: Snapshot, future: list[dict], h: horizons.Horizon, costs: Costs,
          single: bool = False, ticker: str | None = None) -> list[Trade]:
    """Each side of ``pb`` judged on ``future`` (the bars after the plan). ``ticker`` names the bars'
    instrument when it is not the snapshot's (#239: a leveraged product on its underlying's plan)."""
    ticker = ticker or snap.ticker
    opens = outcome.session_open(ticker)
    regime = pb["regime"]["id"]
    atr = pb["frame"]["atr"] or 0.0
    out = []
    for side_name, direction in (("long", 1), ("short", -1)):
        side = pb[side_name]
        if side.get("setup") in (None, "none"):
            continue
        if single:
            side = summary.as_single(side, direction > 0, pb["frame"].get("atr_zone") or atr)
            if side is None:
                continue
        # the entry stays valid as long as a live plan's would (scalp 15 min, intraday 1 h, 2~3 days 1 day)
        res = outcome.judge_playbook(side, direction, future, h, snap.plan_at, opens,
                                     horizons.valid_until(h, snap.plan_at))
        trade = Trade(ticker, snap.day, side_name, side["setup"], regime, side.get("score"), res["status"],
                      direction=side.get("direction") or "neutral")
        trade.entry_kind = entry_now._kind(side)
        trade.distance_atr = entry_now.distance_atr(side, direction > 0, pb["frame"].get("reference_price"), atr)
        if res.get("filled_shares"):
            avg = res["avg_fill"]
            trade.filled_shares = res["filled_shares"]
            trade.steps = _steps(res.get("steps"))
            trade.took_t1 = bool(res.get("took_t1"))
            trade.r, trade.r_size = res.get("r"), res.get("r_size")
            risk = abs(avg - side["stop"]) or 1e-9
            cost_r = 2 * costs.bps_per_side / 1e4 * avg / risk * res["filled_shares"]
            if trade.r_size is not None:
                trade.r_net = round(trade.r_size - cost_r, 3)
            trade.mae_atr, trade.mfe_atr = _excursions(direction, future, avg, res.get("first_fill_at"),
                                                       res.get("exited_at"), atr)
            trade.held_bars, trade.reach = _held_and_reach(direction, future, side, res.get("first_fill_at"),
                                                           res.get("exited_at"))
            if res["status"] in SETTLED + MARKED and side.get("targets"):
                alt = exits.replay(direction, future, res.get("first_fill_at"), avg, side["stop"],
                                   side["targets"][0]["price"], atr, res.get("r"),
                                   (side.get("time_stop") or {}).get("bars"))
                if alt is not None:
                    added = alt.pop("added")
                    trade.exits = {k: (round(v * res["filled_shares"] - cost_r * (1 + (added if k == "pyramid" else 0)), 3)
                                       if v is not None else None) for k, v in alt.items()}
        trade.risk_phase = (snap.risk or {}).get("phase") or "unknown"
        trade.kind = "leveraged" if leverage_of(ticker) else "stock"
        out.append(trade)
    return out


# ---------------------------------------------------------------- tallies


def entry_style(snaps: Iterable[tuple[Snapshot, list[dict]]], h: horizons.Horizon, costs: Costs) -> dict:
    """#221: the ladder against buying it all at the single price, on the same plans (those that have
    a single price), split at the train / held-out cut."""
    snaps = list(snaps)
    ladder = [t for s, p in snaps for t in trades_at(s, p, h, costs)]
    single = [t for s, p in snaps for t in trades_at(s, p, h, costs, single=True)]
    keys = {(t.ticker, t.at, t.side) for t in single}
    ladder = [t for t in ladder if (t.ticker, t.at, t.side) in keys]
    cut = split_cut(t.at for t in ladder)
    out = {"cut": cut}
    for name, trades in (("ladder", ladder), ("single", single)):
        tr, va = split(trades, cut, h)
        out[name] = {"train": aggregate(tr), "valid": aggregate(va)}
    return out


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def aggregate(trades: list[Trade]) -> dict:
    """Fill rate, how often T1 came before the stop, R (after costs) and excursions. "settled" also
    counts the trades still open when the look-ahead ended, marked at the last close (``marked``).
    R is desk.outcome's: per filled share over the risk from the average fill, times the filled share."""
    filled = [t for t in trades if t.filled_shares]
    settled = [t for t in filled if t.status in SETTLED + MARKED]
    rs = [t.r_net for t in settled if t.r_net is not None]
    counts = {s: sum(1 for t in trades if t.status == s) for s in (*SETTLED, "open", "waiting", "unfilled", "invalid")}
    return {
        "plans": len(trades), "filled": len(filled), "settled": len(settled),
        "marked": sum(1 for t in settled if t.status in MARKED), **counts,
        "fill_rate": round(len(filled) / len(trades), 3) if trades else None,
        # T1 before the stop: every settled trade that took T1 (t2, t1_be, a close after T1, still open after T1)
        "t1_first_rate": round(sum(1 for t in settled if t.took_t1) / len(settled), 3) if settled else None,
        "median_r": _median(rs),
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


def _in(day: str, lo: str, hi: str) -> bool:
    return lo <= day <= hi


def split_cut(days: Iterable[str]) -> str | None:
    """The first validation date: TRAIN_SHARE of the plan dates come before it."""
    dates = sorted(set(days))
    return dates[int(len(dates) * TRAIN_SHARE)] if dates else None


def split(trades: list[Trade], cut: str | None, h: horizons.Horizon) -> tuple[list[Trade], list[Trade]]:
    """Train before the cut minus an embargo (their trades would run into the validation dates)."""
    if not cut:
        return [], []
    edge = (date.fromisoformat(cut) - timedelta(days=EMBARGO_DAYS[h.id])).isoformat()
    return [t for t in trades if t.at < edge], [t for t in trades if t.at >= cut]


EV_GATE_HORIZONS = ("swing",)
EV_GATE_FOLDS = 4          # the training dates' own check: each block judged by a table of the others


def _ev_tables(trades: list[Trade], h: horizons.Horizon):
    """The calibration table and the #312 reach table from ``trades`` only."""
    reach = entry_now.Reach()
    for t in trades:
        if t.distance_atr is not None and t.entry_kind:
            reach.add(h.id, t.side, t.entry_kind, t.distance_atr, bool(t.filled_shares))
    return calibration.Table(calibration.from_trades(trades, h.id)), reach


def _ev(t: Trade, table, reach, h: horizons.Horizon) -> float | None:
    """Expected R per plan as desk.entry_now gives it: the fill rate at the plan's distance x the
    record's expected R once filled. None when either is thin."""
    cal = table.look(h.id, t.side, t.setup, t.regime, t.score)
    got = reach.look(h.id, t.side, t.entry_kind or "limit", t.distance_atr)
    if cal["thin"] or got["p"] is None:
        return None
    return got["p"] * cal["exp_r"]


def _gate_view(kept: list[Trade], every: list[Trade]) -> dict:
    """``kept`` against all the plans: R per kept fill, and per plan of ``every`` (a skipped plan is 0 R)."""
    agg = aggregate(kept)
    rows = [(t.at, t.r_net) for t in kept if t.filled_shares and t.r_net is not None
            and t.status in SETTLED + MARKED]
    total = sum(r for _d, r in rows)
    return {"plans": len(every), "kept": len(kept), "filled": agg["filled"], "settled": agg["settled"],
            "t1_first_rate": agg["t1_first_rate"], "expectancy_r": agg["expectancy_r"],
            "per_plan_r": round(total / len(every), 4) if every else None, "total_r": round(total, 1),
            "max_dd_r": _exit_stats(rows)["max_dd_r"]}


def ev_gate_report(trades: list[Trade], cut: str | None, h: horizons.Horizon) -> dict:
    """#166 stage 5: the #312 gate - "stand aside when the expected R per plan is not above 0" - as a
    step after desk.direction, side by side with no gate. ``ev>0`` skips a side whose expected R per
    plan is 0 or below and keeps one of unknown worth; ``ev>0+known`` also skips the unknown ones.
    No date is judged by a table that saw it: the held-out dates by the training dates' tables, and
    the training dates by ``EV_GATE_FOLDS`` blocks, each by a table of the other blocks (the choice
    is made there, not on the held-out dates). by_direction and the crash windows are over every date
    (each judged out of its own block).

    Measured 2026-11-14 (swing basket 2019-, 10bp a side, held-out from 2024-06-20): training -0.035R
    a fill → -0.093R with the gate (kept 753 of 3585 plans; per plan -0.024R → -0.016R only because
    fewer were taken) - the plans it kept were mostly dip-catch against the trend (-0.198R), whose
    record in the other blocks looked better than it went. Held-out +0.022R → +0.242R on 62 fills.
    Not adopted on the training dates: the report stays to re-check when the plans change."""
    if h.id not in EV_GATE_HORIZONS or not cut:
        return {}
    train, valid = split(trades, cut, h)
    days = sorted({t.at for t in train})
    if len(days) < EV_GATE_FOLDS:
        return {}
    bounds = [days[len(days) * i // EV_GATE_FOLDS] for i in range(1, EV_GATE_FOLDS)]
    fold = {d: sum(1 for b in bounds if d >= b) for d in days}
    ev: dict[int, float | None] = {}
    for k in range(EV_GATE_FOLDS):
        table, reach = _ev_tables([t for t in train if fold[t.at] != k], h)
        for t in train:
            if fold[t.at] == k:
                ev[id(t)] = _ev(t, table, reach, h)
    table, reach = _ev_tables(train, h)
    for t in valid:
        ev[id(t)] = _ev(t, table, reach, h)
    judged = train + valid
    gates = {
        "off": lambda t: True,
        "ev>0": lambda t: ev.get(id(t)) is None or ev[id(t)] > 0,
        "ev>0+known": lambda t: ev.get(id(t)) is not None and ev[id(t)] > 0,
    }
    out: dict = {"cut": cut, "folds": EV_GATE_FOLDS, "gates": {}}
    for name, keep in gates.items():
        kept = [t for t in judged if keep(t)]
        k_train, k_valid = [t for t in train if keep(t)], [t for t in valid if keep(t)]
        out["gates"][name] = {
            "train": _gate_view(k_train, train), "valid": _gate_view(k_valid, valid),
            "by_direction": _by(kept, lambda t: f"{t.side}:{t.setup}:{t.direction}"),
            "by_setup": _by(kept, lambda t: f"{t.side}:{t.setup}"),
            "crash_windows": {cw: _gate_view([t for t in kept if _in(t.at, lo, hi)],
                                             [t for t in judged if _in(t.at, lo, hi)])
                              for cw, (lo, hi) in CRASH_WINDOWS.items()},
        }
    return out


def summarize(trades: list[Trade], h: horizons.Horizon, cut: str | None) -> dict:
    train, valid = split(trades, cut, h)
    return {
        "baseline": aggregate(trades),
        "by_setup": _by(trades, lambda t: f"{t.side}:{t.setup}"),
        "by_regime": _by(trades, lambda t: f"{t.side}:{t.setup}:{t.regime}"),
        "by_steps": _by([t for t in trades if t.filled_shares], lambda t: f"{t.side}:{t.setup}:{t.steps}"),
        "by_ticker": _by(trades, lambda t: t.ticker),
        "by_direction": _by(trades, lambda t: f"{t.side}:{t.setup}:{t.direction}"),
        "crash_windows": {name: aggregate([t for t in trades if _in(t.at, lo, hi)])
                          for name, (lo, hi) in CRASH_WINDOWS.items()},
        "by_risk": _by(trades, lambda t: f"{t.side}:{t.setup}:{t.risk_phase}"),
        "split": {"cut": cut, "embargo_days": EMBARGO_DAYS[h.id], "train": aggregate(train), "valid": aggregate(valid)},
    }


def _exit_stats(rows: list[tuple[str, float]]) -> dict:
    """(plan day, r) rows. ``losing_days``: the longest run of plan days whose summed R is negative -
    one day's trades (several tickers, overlapping plans) move together, so they count once.
    ``max_dd_r``: the deepest fall of the running R total, plan day by plan day (#239 채택 조건)."""
    rs = [r for _d, r in rows]
    if not rs:
        return {"n": 0, "win_rate": None, "avg_r": None, "median_r": None, "big_share": None, "losing_days": 0,
                "max_dd_r": None}
    by_day: dict[str, float] = {}
    for d, r in rows:
        by_day[d] = by_day.get(d, 0.0) + r
    streak = best = 0
    total = peak = dd = 0.0
    for d in sorted(by_day):
        streak = streak + 1 if by_day[d] < 0 else 0
        best = max(best, streak)
        total += by_day[d]
        peak = max(peak, total)
        dd = max(dd, peak - total)
    return {"n": len(rs), "win_rate": round(sum(1 for r in rs if r > 0) / len(rs), 3), "avg_r": _mean(rs),
            "median_r": _median(rs), "big_share": round(sum(1 for r in rs if r >= exits.BIG_R) / len(rs), 3),
            "losing_days": best, "max_dd_r": round(dd, 3)}


def exit_report(trades: list[Trade], cut: str | None, h: horizons.Horizon) -> dict:
    """#239: each exit rule on the same filled trades - win rate, average / median R after costs, the
    share of big wins (>= exits.BIG_R) and the longest run of losing plan days - for stocks and leveraged products,
    on the training dates and the held-out ones. A rule is only worth turning on if it beats ``fixed``
    on the held-out dates."""
    # the oversold swing keeps its own trail (desk.value_swing) - the rules compared here are for the rest
    replayed = sorted((t for t in trades if t.exits and t.setup != "value_swing"), key=lambda t: (t.at, t.ticker))
    train, valid = split(replayed, cut, h)
    out: dict = {"cut": cut, "rules": list(exits.VARIANTS)}
    for kind in ("all", "stock", "leveraged"):
        pick = (lambda ts, k=kind: [t for t in ts if k == "all" or t.kind == k])
        out[kind] = {v: {"train": _exit_stats([(t.at, t.exits[v]) for t in pick(train) if t.exits.get(v) is not None]),
                         "valid": _exit_stats([(t.at, t.exits[v]) for t in pick(valid) if t.exits.get(v) is not None])}
                     for v in exits.VARIANTS}
    return out


def attach_features(trades: list[Trade], snaps: Iterable[tuple[Snapshot, list[dict]]]) -> None:
    """#284: each trade gets the chart of its own snapshot (desk.entry_features, the plan's bars
    only) - only when the report asks, so a sweep or a calibration does not pay for it."""
    by_plan = {(s.ticker, s.day): s for s, _ in snaps}
    memo: dict[tuple[str, str], dict] = {}
    for t in trades:
        key = (t.ticker, t.at)
        snap = by_plan.get(key)
        if snap is None:
            continue
        if key not in memo:
            memo[key] = entry_features.compute(snap.daily, snap.weekly, snap.risk)
        t.features = memo[key]


def _quartile_edges(values: list[float]) -> list[float]:
    """The 25 / 50 / 75% cut points (duplicates dropped: a feature stuck on one value has fewer
    buckets, and a bucket's number is its place among the cut points, so q1 may be missing)."""
    xs = sorted(values)
    return sorted({xs[min(len(xs) - 1, int(len(xs) * q))] for q in (0.25, 0.5, 0.75)})


def _bucket(value, edges: list[float] | None) -> str | None:
    if value is None:
        return None
    if edges is None:                     # a category
        return str(value)
    i = bisect.bisect_right(edges, value)
    lo = f"{edges[i - 1]:g}" if i else "-inf"
    hi = f"{edges[i]:g}" if i < len(edges) else "+inf"
    return f"q{i + 1} [{lo}, {hi})"


def _feature_stats(trades: list[Trade]) -> dict:
    rows = [(t.at, t.r_net) for t in trades]
    out = _exit_stats(rows)
    out["t1_rate"] = round(sum(1 for t in trades if t.took_t1) / len(trades), 3) if trades else None
    return out


def feature_report(trades: list[Trade], cut: str | None, h: horizons.Horizon) -> dict:
    """#284: per side:setup, the settled trades bucketed by each feature of the chart at the plan
    (desk.entry_features) - numeric ones by the training dates' quartiles, so the held-out dates
    never choose an edge - with the win rate, T1 rate, average R after costs and the deepest fall
    of the running R, on the training dates and the held-out ones. ``top`` lists the buckets that
    are above 0 R on both halves with enough trades (MIN_SETTLED training, MIN_VALID held-out),
    best held-out first; ``beats_base`` marks those that also beat their setup's own average on both."""
    done = [t for t in trades if t.features and t.filled_shares and t.r_net is not None
            and t.status in SETTLED + MARKED]
    train, valid = split(done, cut, h)
    keys = sorted({f"{t.side}:{t.setup}" for t in done})
    out: dict = {"cut": cut, "min_train": MIN_SETTLED, "min_valid": MIN_VALID, "names": entry_features.NAMES,
                 "setups": {}, "top": []}
    for key in keys:
        tr = [t for t in train if f"{t.side}:{t.setup}" == key]
        va = [t for t in valid if f"{t.side}:{t.setup}" == key]
        base = {"train": _feature_stats(tr), "valid": _feature_stats(va)}
        feats: dict[str, list[dict]] = {}
        for name in (*entry_features.CATEGORICAL, *entry_features.NUMERIC):
            if name in entry_features.NUMERIC:
                seen = [t.features[name] for t in tr if t.features.get(name) is not None]
                if len(seen) < 8:
                    continue
                edges = _quartile_edges(seen)
            else:
                edges = None
            groups: dict[str, tuple[list[Trade], list[Trade]]] = {}
            for half, ts in ((0, tr), (1, va)):
                for t in ts:
                    b = _bucket(t.features.get(name), edges)
                    if b is not None:
                        groups.setdefault(b, ([], []))[half].append(t)
            rows = []
            for b, (gt, gv) in sorted(groups.items()):
                row = {"feature": name, "bucket": b, "train": _feature_stats(gt), "valid": _feature_stats(gv)}
                tr_r, va_r = row["train"]["avg_r"], row["valid"]["avg_r"]
                row["enough"] = row["train"]["n"] >= MIN_SETTLED and row["valid"]["n"] >= MIN_VALID
                row["positive"] = row["enough"] and tr_r is not None and va_r is not None and tr_r > 0 and va_r > 0
                row["beats_base"] = (row["enough"] and tr_r is not None and va_r is not None
                                     and base["train"]["avg_r"] is not None and base["valid"]["avg_r"] is not None
                                     and tr_r > base["train"]["avg_r"] and va_r > base["valid"]["avg_r"])
                rows.append(row)
                if row["positive"]:
                    out["top"].append({"setup": key, **row})
            feats[name] = rows
        out["setups"][key] = {"base": base, "features": feats}
    out["top"].sort(key=lambda r: -(r["valid"]["avg_r"] or 0))
    return out


def losing_streak(trades: list[Trade]) -> int:
    """The longest run of losing settled trades in plan order (ties by ticker)."""
    best = n = 0
    for t in sorted(trades, key=lambda t: (t.at, t.ticker, t.side)):
        if t.r_net is None or t.status not in SETTLED + MARKED:
            continue
        n = n + 1 if t.r_net < 0 else 0
        best = max(best, n)
    return best


EQUITY_POINTS = 400        # the curve's points at most (the report ships with the image, #185)


def equity(trades: list[Trade], max_points: int = EQUITY_POINTS) -> dict:
    """The rule's record over time (#192): settled trades' R after costs summed by the plan's day,
    cumulative, for the basket (``all``) and per side:setup, with each curve's deepest fall from a
    peak (``max_dd``, R, on every day - the curve itself is thinned to ``max_points`` days, each
    point the exact running total on that day). Plans overlap, so this is not an account's curve."""
    rows = [t for t in trades if t.filled_shares and t.status in SETTLED + MARKED and t.r_net is not None]
    if not rows:
        return {}
    keys = ["all", *sorted({f"{t.side}:{t.setup}" for t in rows})]
    days = sorted({t.at for t in rows})
    at = {d: i for i, d in enumerate(days)}
    per_day = {k: [0.0] * len(days) for k in keys}
    counts = dict.fromkeys(keys, 0)
    for t in rows:
        for k in ("all", f"{t.side}:{t.setup}"):
            per_day[k][at[t.at]] += t.r_net
            counts[k] += 1
    series, max_dd = {}, {}
    for k in keys:
        total = peak = worst = 0.0
        run = []
        for v in per_day[k]:
            total += v
            peak = max(peak, total)
            worst = min(worst, total - peak)
            run.append(total)
        series[k], max_dd[k] = run, round(worst, 2)
    if len(days) > max_points:
        keep = sorted({round(i * (len(days) - 1) / (max_points - 1)) for i in range(max_points)})
    else:
        keep = range(len(days))
    return {"basis": "plan_day", "days": [days[i] for i in keep],
            "series": {k: [round(series[k][i], 2) for i in keep] for k in keys},
            "max_dd": max_dd, "trades": counts}


def gate_view(trades: list[Trade], cut: str | None, h: horizons.Horizon) -> dict:
    """What the #146 rule changes: the dip catch, the short, the held-out dates and the crashes."""
    _, valid = split(trades, cut, h)
    dip = [t for t in trades if t.side == "long" and t.setup == "dip_catch"]
    view = {"all": aggregate(trades), "valid": aggregate(valid), "long:dip_catch": aggregate(dip),
            "short": aggregate([t for t in trades if t.side == "short"]),
            "losing_streak": losing_streak(trades)}
    for name, (lo, hi) in CRASH_WINDOWS.items():
        view[f"crash:{name}"] = aggregate([t for t in trades if _in(t.at, lo, hi)])
        view[f"crash:{name}:long:dip_catch"] = aggregate([t for t in dip if _in(t.at, lo, hi)])
    return view


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


def value_swing_report(trades: list[Trade], cut: str | None, h: horizons.Horizon) -> dict:
    """#167: the oversold swing buy on its own - expectancy on the train and held-out dates, how
    often each target was reached before the stop (the plan shows these), the worst loss, how long a
    trade was held, against the dip catch in the crashes, and by conviction score."""
    vs = [t for t in trades if t.side == "long" and t.setup == "value_swing"]
    if not vs:
        return {}
    train, valid = split(vs, cut, h)
    filled = [t for t in vs if t.filled_shares and t.reach is not None]
    settled = [t for t in vs if t.filled_shares and t.status in SETTLED + MARKED and t.r_net is not None]
    held = [t.held_bars for t in filled if t.held_bars is not None]
    dip = [t for t in trades if t.side == "long" and t.setup == "dip_catch"]
    return {
        "all": aggregate(vs), "train": aggregate(train), "valid": aggregate(valid),
        # P(target k reached before the stop | filled), the first fill onwards within the look-ahead
        "reach": {f"t{k}": round(sum(1 for t in filled if t.reach >= k) / len(filled), 3) if filled else None
                  for k in (1, 2, 3)},
        "reach_n": len(filled),
        "worst_r": min((t.r_net for t in settled), default=None),
        "r_p10": _pct([t.r_net for t in settled], 0.1),
        "mae_atr_p90": _pct([t.mae_atr for t in settled if t.mae_atr is not None], 0.9),
        "held_bars": {"median": _median(held), "p90": _pct(held, 0.9), "max": max(held, default=None)},
        "crash": {name: {"value_swing": aggregate([t for t in vs if _in(t.at, lo, hi)]),
                         "dip_catch": aggregate([t for t in dip if _in(t.at, lo, hi)])}
                  for name, (lo, hi) in CRASH_WINDOWS.items()},
        "by_score": _by(vs, lambda t: str(t.score)),
    }


# ---------------------------------------------------------------- runs


def snapshots(ticker: str, h: horizons.Horizon, primary: list[dict], secondary: list[dict],
              start: str | None = None, end: str | None = None, step: int | None = None,
              progress: Callable[[str], None] | None = None, vix: dict[str, float] | None = None,
              market: MarketRisk | None = None) -> Iterator[Snapshot]:
    """Every ``step`` bars from the first full window to the last bar that still has a full
    look-ahead after it (a later plan would be cut short and read as still open)."""
    step = step or STEP[h.id]
    total = len(primary)
    last = total - 1 - LOOKAHEAD[h.id]
    for t in range(h.primary_bars - 1, max(last, 0), step):
        day = local_day(primary[t]["at"], ticker)
        if (start and day < start) or (end and day > end):
            continue
        snap = snapshot(ticker, h, primary, secondary, t, vix, market)
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
             min_score: int = 0, notes: list[str] | None = None, gate: bool = True) -> list[Trade]:
    out, failed = [], {}
    for snap, primary in snaps:
        try:
            trades = trades_at(snap, primary, h, costs, gate)
        except Exception as exc:  # noqa: BLE001 - one odd bar (a flat series) does not end the run
            failed.setdefault(snap.ticker, f"{snap.day} {type(exc).__name__}: {exc}")
            continue
        out += [t for t in trades if (t.score or 0) >= min_score]
    if notes is not None:
        notes += [f"{ticker}: 일부 시점 계산 실패(처음: {first})" for ticker, first in failed.items()]
    return out


def sweep(snaps: list[tuple[Snapshot, list[dict]]], h: horizons.Horizon, costs: Costs,
          grid: dict[str, list] | None = None) -> list[dict]:
    """One parameter at a time around the current values; ranked by the training expectancy, with
    the held-out dates next to it (a choice that only wins on the training dates is over-fitted)."""
    grid = grid or SWEEP
    cut = split_cut(s.day for s, _ in snaps)
    rows = []
    for name, values in grid.items():
        if name != "min_score" and not hasattr(playbook, name):
            continue
        current = getattr(playbook, name, 0)
        for value in values:
            with patched(name, value):
                trades = evaluate(snaps, h, costs, min_score=value if name == "min_score" else 0)
            tr, va = split(trades, cut, h)
            train, valid = aggregate(tr), aggregate(va)
            keys = ("plans", "settled", "fill_rate", "t1_first_rate", "expectancy_r", "expectancy_per_plan_r")
            rows.append({"param": name, "value": value, "current": value == current,
                         "enough": train["settled"] >= MIN_SETTLED,
                         "train": {k: train[k] for k in keys}, "valid": {k: valid[k] for k in keys}})
    # a filter that keeps a handful of trades can top the list by luck: thin rows go last
    rows.sort(key=lambda r: (not r["enough"], r["train"]["expectancy_r"] is None, -(r["train"]["expectancy_r"] or 0)))
    return rows


def run(tickers: list[str], horizon: str | None = None, start: str | None = None, end: str | None = None,
        step: int | None = None, costs: Costs | None = None, do_sweep: bool = False,
        fetch: Callable[[str, horizons.Horizon, str | None], tuple[list[dict], list[dict], list[str]]] | None = None,
        progress: Callable[[str], None] | None = None,
        vix: dict[str, float] | None | bool = True, risk: MarketRisk | None | bool = None,
        on_trades: Callable[[list[Trade], str | None], None] | None = None, features: bool = False) -> Report:
    """``vix=True`` loads ^VIX daily closes (the regime's fear evidence); a dict supplies them; None skips.
    ``risk=True`` loads the market's indices for the selling score (#146, desk.risk_regime; the CLI
    does); a MarketRisk supplies them; None leaves the playbooks ungated. ``on_trades(trades, cut)``
    receives the gated trades and the train/held-out cut (desk.cli calibrate, #147). ``features``
    adds the R by the chart at the plan (#284, ``feature_report``)."""
    h = horizons.get(horizon)
    costs = costs or Costs()
    fetch = fetch or history
    notes: list[str] = []
    if vix is True:
        try:
            vix = vix_closes(start)
        except Exception as exc:  # noqa: BLE001 - the regime reads without VIX, as a live run would
            notes.append(f"^VIX 를 받지 못해 국면 판정에서 뺌 — {type(exc).__name__}: {exc}")
            vix = None
    if risk is True:
        risk = market_risk(start, tickers)
        missing = [s for s in risk_regime.symbols_for_all(tickers) if s not in risk.series]
        if not risk.series:
            notes.append("시장 투매 지표를 하나도 받지 못해 #146 규칙을 뺌")
            risk = None
        elif missing:
            notes.append(f"시장 투매 지표 일부 없음(남은 지표로 계산): {', '.join(missing)}")
    snaps: list[tuple[Snapshot, list[dict]]] = []
    for ticker in tickers:
        try:
            primary, secondary, more = fetch(ticker, h, start)
            notes += [f"{ticker}: {n}" for n in more]
            got = [(s, primary) for s in snapshots(ticker, h, primary, secondary, start, end, step, progress, vix or None,
                                                    risk or None)]
        except Exception as exc:  # noqa: BLE001 - one ticker failing leaves the others
            notes.append(f"{ticker}: 건너뜀 — {type(exc).__name__}: {exc}")
            continue
        if not got:
            notes.append(f"{ticker}: 계획을 하나도 만들지 못함(봉 {len(primary)}개)")
        snaps += got
    trades = evaluate(snaps, h, costs, notes=notes)
    cut = split_cut(t.at for t in trades)
    if on_trades:
        on_trades(trades, cut)
    if h.primary != "1d":
        notes.append("분봉 · 시간봉 보기는 야후 보관 기간 때문에 표본이 적습니다")
    notes.append("이어진 시점의 계획은 기간이 겹칩니다 — 규칙의 성적이지 실제 계좌 곡선이 아닙니다")
    notes.append("R 은 평균 체결가에서 손절까지를 1R 로 본 값 × 체결 비중입니다(깊게 체결될수록 1R 금액이 작아짐)")
    notes.append("사다리 비중(SHARES) 스윕은 하따(dip_catch)에만 영향 — 박스 하단 · 눌림목은 50/50 고정")
    report = Report(VERSION, h.id, list(tickers), start, end, asdict(costs), **summarize(trades, h, cut), notes=notes,
                    generated_at=datetime.now(UTC).isoformat(timespec="seconds"))
    report.value_swing = value_swing_report(trades, cut, h)
    report.exits = exit_report(trades, cut, h)
    if features:
        attach_features(trades, snaps)
        report.features = feature_report(trades, cut, h)
    if risk:
        report.risk_gate = {"on": gate_view(trades, cut, h),
                            "off": gate_view(evaluate(snaps, h, costs, gate=False), cut, h)}
    report.equity = equity(trades)
    report.ev_gate = ev_gate_report(trades, cut, h)
    if do_sweep:
        report.sweep = sweep(snaps, h, costs)
    return report


def vix_closes(start: str | None) -> dict[str, float]:
    from .prices import yahoo_candles

    days_back = (date.today() - date.fromisoformat(start)).days if start else 365 * 3
    bars = yahoo_candles("^VIX", "1d", _count_for("1d", days_back) + 300)
    return {local_day(b.at, "^VIX"): b.close for b in bars}


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
    rows = [("ALL", report.baseline), *report.by_setup.items(), *report.by_regime.items(), *report.by_direction.items(),
            *((f"split:{k}", v) for k, v in report.split.items() if isinstance(v, dict)),
            *((f"crash:{k}", v) for k, v in report.crash_windows.items() if v["plans"])]
    for name, agg in rows:
        print(name[:33].ljust(34) + "".join(_fmt(agg.get(c)).rjust(13) for c in cols), file=out)
    for name, agg in report.by_risk.items():
        print(f"risk:{name}"[:33].ljust(34) + "".join(_fmt(agg.get(c)).rjust(13) for c in cols), file=out)
    for key, on in (report.risk_gate.get("on") or {}).items():
        off = report.risk_gate["off"][key]
        if key == "losing_streak":
            print(f"gate {key}: on {on} / off {off}", file=out)
        elif on["plans"] or off["plans"]:
            print(f"gate {key}: expectancy on {_fmt(on['expectancy_r'])} / off {_fmt(off['expectancy_r'])}"
                  f" · per plan {_fmt(on['expectancy_per_plan_r'])} / {_fmt(off['expectancy_per_plan_r'])}"
                  f" (settled {on['settled']} / {off['settled']})", file=out)
    vs = report.value_swing
    if vs:
        held = vs["held_bars"]
        print(f"value_swing: valid {_fmt(vs['valid']['expectancy_r'])} (settled {vs['valid']['settled']}) · "
              f"reach T1 {_fmt(vs['reach']['t1'])} T2 {_fmt(vs['reach']['t2'])} T3 {_fmt(vs['reach']['t3'])} "
              f"(filled {vs['reach_n']}) · worst {_fmt(vs['worst_r'])}R p10 {_fmt(vs['r_p10'])}R · "
              f"held median {_fmt(held['median'])} p90 {_fmt(held['p90'])} max {_fmt(held['max'])} bars", file=out)
        for name, pair in vs["crash"].items():
            if pair["value_swing"]["plans"]:
                print(f"value_swing crash:{name}: {_fmt(pair['value_swing']['expectancy_r'])} vs dip_catch "
                      f"{_fmt(pair['dip_catch']['expectancy_r'])}", file=out)
        for score, agg in vs["by_score"].items():
            print(f"value_swing score {score}: {_fmt(agg['expectancy_r'])} (settled {agg['settled']})", file=out)
    ex = report.exits
    for kind in ("stock", "leveraged") if ex else ():
        for rule in ex["rules"]:
            tr, va = ex[kind][rule]["train"], ex[kind][rule]["valid"]
            if tr["n"] or va["n"]:
                print(f"exit {kind}:{rule}".ljust(28) + f"valid avg {_fmt(va['avg_r'])}R win {_fmt(va['win_rate'])} "
                      f"big {_fmt(va['big_share'])} losing days {va['losing_days']} dd {_fmt(va['max_dd_r'])}R (n {va['n']}) | "
                      f"train avg {_fmt(tr['avg_r'])}R dd {_fmt(tr['max_dd_r'])}R (n {tr['n']})", file=out)
    fr = report.features
    if fr:
        print(f"features: above 0 R on both halves (train n >= {fr['min_train']}, valid n >= {fr['min_valid']}, "
              f"buckets by the training quartiles)", file=out)
        for row in fr["top"]:
            tr, va = row["train"], row["valid"]
            base = fr["setups"][row["setup"]]["base"]["valid"]
            print(f"  {row['setup']} {row['feature']} {row['bucket']}".ljust(56)
                  + f"valid {_fmt(va['avg_r'])}R (base {_fmt(base['avg_r'])}) win {_fmt(va['win_rate'])} "
                  f"dd {_fmt(va['max_dd_r'])}R n {va['n']} | train {_fmt(tr['avg_r'])}R n {tr['n']}"
                  f"{' *' if row['beats_base'] else ''}", file=out)
        if not fr["top"]:
            print("  (none)", file=out)
    eg = report.ev_gate
    for name, g in (eg.get("gates") or {}).items():
        tr, va = g["train"], g["valid"]
        crash = " ".join(f"{cw} {_fmt(v['expectancy_r'])}R/{_fmt(v['per_plan_r'])}" for cw, v in g["crash_windows"].items())
        print(f"ev_gate {name}".ljust(22) + f"train {_fmt(tr['expectancy_r'])}R per plan {_fmt(tr['per_plan_r'])} "
              f"dd {_fmt(tr['max_dd_r'])}R kept {tr['kept']}/{tr['plans']} | valid {_fmt(va['expectancy_r'])}R "
              f"per plan {_fmt(va['per_plan_r'])} dd {_fmt(va['max_dd_r'])}R kept {va['kept']}/{va['plans']} | {crash}",
              file=out)
    for row in report.sweep[:12]:
        print(f"sweep {row['param']}={row['value']}{' (now)' if row['current'] else ''}: "
              f"train {_fmt(row['train']['expectancy_r'])} / valid {_fmt(row['valid']['expectancy_r'])}"
              f" (settled {row['train']['settled']}{'' if row['enough'] else ', 표본 적음'})", file=out)
    for note in report.notes:
        print(f"note: {note}", file=out)
