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

from . import horizons, outcome, playbook, playbook_live, risk_regime, sr

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
    day: str                          # the market's own date of bar t (Korean bars carry the day before in UTC)
    plan_at: str
    sr: dict
    daily: list[dict]
    window: list[dict]
    today: dict | None = None         # the forming session on intraday frames (as the live run builds)
    vix: float | None = None          # ^VIX close on that day
    risk: dict | None = None          # desk.risk_regime.score on that day (#146)


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


def trades_at(snap: Snapshot, primary: list[dict], h: horizons.Horizon, costs: Costs,
              gate: bool = True) -> list[Trade]:
    """Build the snapshot's playbooks and judge each side on the bars after it. ``gate`` applies
    the market's selling rule (#146) when the snapshot has a score."""
    pb = playbook.build(snap.sr, snap.daily, snap.window, h, now=snap.plan_at, today=snap.today, vix=snap.vix)
    if gate:
        pb = risk_regime.apply(pb, snap.risk)
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
        # the entry stays valid as long as a live plan's would (scalp 15 min, intraday 1 h, 2~3 days 1 day)
        res = outcome.judge_playbook(side, direction, future, h, snap.plan_at, opens,
                                     horizons.valid_until(h, snap.plan_at))
        trade = Trade(snap.ticker, snap.day, side_name, side["setup"], regime, side.get("score"), res["status"],
                      direction=side.get("direction") or "neutral")
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
        trade.risk_phase = (snap.risk or {}).get("phase") or "unknown"
        out.append(trade)
    return out


# ---------------------------------------------------------------- tallies


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


def losing_streak(trades: list[Trade]) -> int:
    """The longest run of losing settled trades in plan order (ties by ticker)."""
    best = n = 0
    for t in sorted(trades, key=lambda t: (t.at, t.ticker, t.side)):
        if t.r_net is None or t.status not in SETTLED + MARKED:
            continue
        n = n + 1 if t.r_net < 0 else 0
        best = max(best, n)
    return best


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
        vix: dict[str, float] | None | bool = True, risk: MarketRisk | None | bool = None) -> Report:
    """``vix=True`` loads ^VIX daily closes (the regime's fear evidence); a dict supplies them; None skips.
    ``risk=True`` loads the market's indices for the selling score (#146, desk.risk_regime; the CLI
    does); a MarketRisk supplies them; None leaves the playbooks ungated."""
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
    if h.primary != "1d":
        notes.append("분봉 · 시간봉 보기는 야후 보관 기간 때문에 표본이 적습니다")
    notes.append("이어진 시점의 계획은 기간이 겹칩니다 — 규칙의 성적이지 실제 계좌 곡선이 아닙니다")
    notes.append("R 은 평균 체결가에서 손절까지를 1R 로 본 값 × 체결 비중입니다(깊게 체결될수록 1R 금액이 작아짐)")
    notes.append("사다리 비중(SHARES) 스윕은 하따(dip_catch)에만 영향 — 박스 하단 · 눌림목은 50/50 고정")
    report = Report(VERSION, h.id, list(tickers), start, end, asdict(costs), **summarize(trades, h, cut), notes=notes,
                    generated_at=datetime.now(UTC).isoformat(timespec="seconds"))
    if risk:
        report.risk_gate = {"on": gate_view(trades, cut, h),
                            "off": gate_view(evaluate(snaps, h, costs, gate=False), cut, h)}
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
    for row in report.sweep[:12]:
        print(f"sweep {row['param']}={row['value']}{' (now)' if row['current'] else ''}: "
              f"train {_fmt(row['train']['expectancy_r'])} / valid {_fmt(row['valid']['expectancy_r'])}"
              f" (settled {row['train']['settled']}{'' if row['enough'] else ', 표본 적음'})", file=out)
    for note in report.notes:
        print(f"note: {note}", file=out)
