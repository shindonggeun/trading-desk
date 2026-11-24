"""Point-in-time extreme-fear research. Callers provide licensed observations and a full session grid."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import isfinite, sqrt
from statistics import median, quantiles
from zoneinfo import ZoneInfo

from .indicators import rsi

SYMBOLS = ("SOXL", "KORU")
HORIZONS = (5, 20, 60, 252)
INCEPTION = {"SOXL": date(2010, 3, 11), "KORU": date(2013, 4, 10)}
_NY = ZoneInfo("America/New_York")
_THRESHOLDS = {
    "rsi": ("<=", 30, "Wilder RSI(14), adjusted ETF regular-session close"),
    "cnn": ("<=", 10, "CNN Fear & Greed"),
    "vix": (">=", 30, "Cboe VIX regular-session close"),
}
_VERIFIED = ("matched", "not_matched")
_HISTORY_LIMIT = 20
_VIX_CLOSE_LAG = timedelta(minutes=15)


@dataclass(frozen=True)
class Session:
    day: date
    opens_at: datetime
    closes_at: datetime


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    available_at: datetime


@dataclass(frozen=True)
class Fixing:
    day: date
    value: float
    available_at: datetime


@dataclass(frozen=True)
class _Condition:
    status: str
    value: float | None = None
    available_at: datetime | None = None
    reason: str | None = None

    def wire(self, key: str) -> dict:
        operator, threshold, source = _THRESHOLDS[key]
        return {"value": self.value, "operator": operator, "threshold": threshold, "source": source,
                "status": self.status, "available_at": _iso(self.available_at), "reason": self.reason}


@dataclass(frozen=True)
class _Signal:
    day: date
    available_at: datetime
    entry: int | None


def _iso(at: datetime | None) -> str | None:
    return at.astimezone(UTC).isoformat() if at is not None else None


def _aware(at: datetime) -> bool:
    return isinstance(at, datetime) and at.utcoffset() is not None


def _finite(value: float) -> bool:
    # Bound integer conversion before math.isfinite converts it to a C double.
    return isfinite(value) if type(value) is float else type(value) is int and value.bit_length() <= 1000


def _verified_condition(key: str, value: float, at: datetime) -> _Condition:
    operator, threshold, _ = _THRESHOLDS[key]
    matched = value >= threshold if operator == ">=" else value <= threshold
    return _Condition("matched" if matched else "not_matched", value, at)


def _availability(at: datetime, session: Session, next_close: datetime | None) -> _Condition | None:
    if at < session.closes_at:
        return _Condition("unverified", reason="before_session_close")
    if next_close is not None and at >= next_close:
        return _Condition("unverified", reason="publication_after_window")
    return None


def _fixing_conditions(key: str, fixings: list[Fixing], sessions: list[Session], completed: int,
                       as_of: datetime) -> list[_Condition]:
    by_day: dict[date, Fixing] = {}
    for fixing in fixings:
        if (fixing.day in by_day or not _aware(fixing.available_at) or not _finite(fixing.value)
                or fixing.value < 0 or (key == "cnn" and fixing.value > 100)):
            raise ValueError(f"invalid or duplicate {key} fixing")
        by_day[fixing.day] = fixing

    result = []
    for i, session in enumerate(sessions[:completed]):
        fixing = by_day.get(session.day)
        if key == "vix" and as_of < session.closes_at + _VIX_CLOSE_LAG:
            result.append(_Condition("delayed", reason="vix_session_still_open"))
            continue
        if fixing is None or fixing.available_at > as_of:
            result.append(_Condition("missing", reason="no_same_session_fixing"))
            continue
        next_close = sessions[i + 1].closes_at if i + 1 < len(sessions) else None
        unavailable = _availability(fixing.available_at, session, next_close)
        if key == "vix" and unavailable is None and fixing.available_at < session.closes_at + _VIX_CLOSE_LAG:
            unavailable = _Condition("unverified", reason="before_vix_session_close")
        result.append(unavailable or _verified_condition(key, fixing.value, fixing.available_at))
    return result


def _bar_grid(symbol: str, raw_bars: list[Bar], close_by_day: dict[date, datetime]) -> dict[date, Bar]:
    result = {}
    for bar in raw_bars:
        closes_at = close_by_day.get(bar.day)
        if closes_at is None or bar.day < INCEPTION[symbol]:
            continue
        prices = (bar.open, bar.high, bar.low, bar.close)
        if (bar.day in result or not _aware(bar.available_at) or bar.available_at < closes_at
                or any(not _finite(value) or not 1e-12 <= value <= 1e12 for value in prices)
                or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
            raise ValueError(f"invalid or duplicate adjusted bar for {symbol} on {bar.day}")
        result[bar.day] = bar
    return result


def _rsi_run(run: list[tuple[int, Bar]], sessions: list[Session]) -> list[_Condition]:
    values = rsi([bar.close for _, bar in run])
    result = []
    ready = run[0][1].available_at
    for offset, ((i, bar), value) in enumerate(zip(run, values, strict=True)):
        session = sessions[i]
        next_close = sessions[i + 1].closes_at if i + 1 < len(sessions) else None
        ready = max(ready, bar.available_at)
        unavailable = _availability(bar.available_at, session, next_close)
        if unavailable is not None:
            result.append(unavailable)
            continue
        at = ready
        if next_close is not None and at >= next_close:
            # A gap in the then-available sequence resets the Wilder seed. The fast path
            # above keeps ordinary, chronologically published histories linear.
            tail = []
            for _, previous in reversed(run[:offset + 1]):
                if previous.available_at >= next_close:
                    break
                tail.append(previous)
            value = rsi([previous.close for previous in reversed(tail)])[-1]
            at = max(previous.available_at for previous in tail)
        result.append(_Condition("unverified", reason="rsi_warmup_or_gap") if value is None
                      else _verified_condition("rsi", value, at))
    return result


def _rsi_conditions(symbol: str, bars: dict[date, Bar], sessions: list[Session], completed: int,
                    as_of: datetime) -> list[_Condition]:
    result = []
    run = []
    for i, session in enumerate(sessions[:completed]):
        bar = bars.get(session.day)
        if bar is not None and bar.available_at <= as_of:
            run.append((i, bar))
            continue
        if run:
            result.extend(_rsi_run(run, sessions))
            run.clear()
        reason = "before_inception" if session.day < INCEPTION[symbol] else "missing_adjusted_bar"
        result.append(_Condition("missing", reason=reason))
    if run:
        result.extend(_rsi_run(run, sessions))
    return result


def _verdict(conditions: tuple[_Condition, ...]) -> str:
    if any(item.status == "not_matched" for item in conditions):
        return "not_matched"
    if all(item.status == "matched" for item in conditions):
        return "matched"
    return "unverified"


def _net_return_pct(entry: float, exit_price: float, cost: float) -> float:
    return round(100 * (exit_price * (1 - cost) / (entry * (1 + cost)) - 1), 10)


def _outcome(signal: _Signal, horizon: int, path: list[Bar | None], sessions: list[Session],
             as_of: datetime, cost: float) -> dict:
    result: dict = {"status": "not_entered", "exit_date": None, "return_pct": None, "mae_pct": None}
    entry = signal.entry
    if entry is None or sessions[entry].opens_at > as_of:
        return result
    entry_bar = path[entry]
    if sessions[entry].closes_at <= as_of and (entry_bar is None or entry_bar.available_at > as_of):
        return result
    end = entry + horizon - 1
    if end >= len(sessions) or sessions[end].closes_at > as_of:
        return {**result, "status": "immature"}
    window = path[entry:end + 1]
    exit_day = sessions[end].day.isoformat()
    if any(bar is None or bar.available_at > as_of for bar in window):
        return {**result, "status": "missing", "exit_date": exit_day}
    valid = [bar for bar in window if bar is not None]
    entry_price = valid[0].open
    return {"status": "complete", "exit_date": exit_day,
            "return_pct": _net_return_pct(entry_price, valid[-1].close, cost),
            "mae_pct": round(100 * (min(bar.low for bar in valid) / entry_price - 1), 10)}


def _ex_post(signal: _Signal, outcome: dict, path: list[Bar | None], cost: float) -> dict:
    result: dict = {"status": outcome["status"], "max_return_pct": None}
    if signal.entry is not None and outcome["status"] == "complete":
        valid = [bar for bar in path[signal.entry:signal.entry + 252] if bar is not None]
        result["max_return_pct"] = _net_return_pct(valid[0].open, max(bar.high for bar in valid), cost)
    return result


def _wilson(wins: int, n: int) -> list[float] | None:
    if not n:
        return None
    z = 1.959963984540054
    p = wins / n
    center = (p + z**2 / (2 * n)) / (1 + z**2 / n)
    delta = z * sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / (1 + z**2 / n)
    return [max(0, center - delta), min(1, center + delta)]


def _metrics(outcomes: list[dict], overlap_skipped: int) -> dict:
    returns = [outcome["return_pct"] for outcome in outcomes if outcome["status"] == "complete"]
    maes = [outcome["mae_pct"] for outcome in outcomes if outcome["status"] == "complete"]
    losses = [value for value in returns if value < 0]
    wins = sum(value > 0 for value in returns)
    n = len(returns)
    loss_quartiles = quantiles(losses, n=4, method="inclusive") if len(losses) > 1 else (losses * 3 or None)
    statuses = {status: sum(outcome["status"] == status for outcome in outcomes)
                for status in ("not_entered", "immature", "missing")}
    return {"n": n, "wins": wins, "losses": len(losses), "flat": n - wins - len(losses),
            "win_rate": wins / n if n else None, "win_rate_ci95": _wilson(wins, n),
            "median_return_pct": median(returns) if returns else None,
            "median_loss_pct": median(losses) if losses else None,
            "loss_quartiles_pct": loss_quartiles,
            "worst_return_pct": min(returns, default=None),
            "worst_mae_pct": min(maes, default=None), "overlap_skipped": overlap_skipped, **statuses}


def _strategy(signals: list[_Signal], path: list[Bar | None], sessions: list[Session], as_of: datetime,
              cost: float, prelisting: int, unverified: int, suppressed: int) -> tuple[dict, dict[_Signal, dict]]:
    horizons = {}
    peaks = []
    history: dict[_Signal, dict] = {signal: {} for signal in signals[-_HISTORY_LIMIT:]}
    for horizon in HORIZONS:
        outcomes = []
        occupied_until = 0
        skipped = 0
        for signal in signals:
            if signal.entry is not None and signal.entry < occupied_until:
                skipped += 1
                if signal in history:
                    history[signal][str(horizon)] = {"status": "overlap_skipped", "exit_date": None,
                                                      "return_pct": None, "mae_pct": None}
                continue
            outcome = _outcome(signal, horizon, path, sessions, as_of, cost)
            outcomes.append(outcome)
            if signal in history:
                history[signal][str(horizon)] = outcome
            if signal.entry is not None and outcome["status"] != "not_entered":
                occupied_until = signal.entry + horizon
            if horizon == 252 and outcome["status"] == "complete":
                peaks.append(_ex_post(signal, outcome, path, cost)["max_return_pct"])
        horizons[str(horizon)] = _metrics(outcomes, skipped)
    return ({"signal_count": len(signals), "prelisting_sessions": prelisting, "unverified_sessions": unverified,
             "episodes_suppressed": suppressed, "horizons": horizons,
             "ex_post_252": {"n": len(peaks), "median_max_return_pct": median(peaks) if peaks else None}}, history)


def _signals(target: str, mode: str, exchange: list[Session], holds: range, macro_covered: set[int],
             rsi_conditions: list[_Condition], cnn: list[_Condition], vix: list[_Condition],
             ) -> tuple[list[_Signal], int, int, int, int]:
    opens = [session.opens_at for session in exchange]
    signals = []
    in_episode = False
    prelisting = unverified = suppressed = verified = 0
    for i in holds:
        session = exchange[i]
        if session.day < INCEPTION[target]:
            prelisting += 1
            continue
        conditions = () if mode == "buy_hold" else (rsi_conditions[i],)
        if mode in ("triple", "soxl_to_koru"):
            conditions += (cnn[i], vix[i])
        verdict = _verdict(conditions)
        if verdict == "not_matched":
            in_episode = False
        if mode in ("rsi_only", "buy_hold") and i not in macro_covered:
            continue
        if all(item.status in _VERIFIED for item in conditions):
            verified += 1
        elif verdict == "unverified":
            unverified += 1
        if verdict != "matched":
            continue
        if mode != "buy_hold" and in_episode:
            suppressed += 1
            continue
        in_episode = True
        # The controls also use the macro availability to choose the common candidate set.
        # They cannot enter before that selection was observable.
        ready = max(item.available_at for item in (*conditions, cnn[i], vix[i]) if item.available_at is not None)
        entry = bisect_right(opens, ready)
        signals.append(_Signal(session.day, ready, entry if entry < len(opens) else None))
    return signals, prelisting, unverified, suppressed, verified


def evaluate(symbol: str, sessions: list[Session], bars: dict[str, list[Bar]], cnn: list[Fixing], vix: list[Fixing],
             *, as_of: datetime, cost_bps: float = 10.0) -> dict:
    """Use the first confirmed values and explicit sessions. Return published evidence only through as_of."""
    if symbol not in SYMBOLS or not _aware(as_of) or not _finite(cost_bps) or not 0 <= cost_bps <= 1000:
        raise ValueError("invalid research symbol, cutoff or execution cost")
    prior = None
    for session in sessions:
        if (not _aware(session.opens_at) or not _aware(session.closes_at)
                or session.opens_at >= session.closes_at
                or session.opens_at.astimezone(_NY).date() != session.day
                or session.closes_at.astimezone(_NY).date() != session.day
                or (prior is not None and (prior.day >= session.day or prior.closes_at >= session.opens_at))):
            raise ValueError("sessions must be unique, ordered New York market days with aware open/close times")
        prior = session
    cost = cost_bps / 10_000
    completed = bisect_right([session.closes_at for session in sessions], as_of)
    close_by_day = {session.day: session.closes_at for session in sessions}
    target_bars = _bar_grid(symbol, bars.get(symbol, []), close_by_day)
    path = [target_bars.get(session.day) for session in sessions]
    target_rsi = _rsi_conditions(symbol, target_bars, sessions, completed, as_of)
    cnn_conditions = _fixing_conditions("cnn", cnn, sessions, completed, as_of)
    vix_conditions = _fixing_conditions("vix", vix, sessions, completed, as_of)
    available = [i for i, pair in enumerate(zip(cnn_conditions, vix_conditions, strict=True))
                 if all(item.status in _VERIFIED for item in pair)]
    macro_covered = set(available)
    scope = range(available[0], completed) if available else range(0)
    modes = {"triple": target_rsi, "rsi_only": target_rsi, "buy_hold": target_rsi}
    if symbol == "KORU":
        soxl_bars = _bar_grid("SOXL", bars.get("SOXL", []), close_by_day)
        modes["soxl_to_koru"] = _rsi_conditions("SOXL", soxl_bars, sessions, completed, as_of)
    strategies = {}
    history = []
    joint_verified = 0
    for mode, mode_rsi in modes.items():
        signals, prelisting, unverified, suppressed, verified = _signals(
            symbol, mode, sessions, scope, macro_covered, mode_rsi, cnn_conditions, vix_conditions)
        strategies[mode], selected = _strategy(signals, path, sessions, as_of, cost, prelisting,
                                               unverified, suppressed)
        if mode != "triple":
            continue
        joint_verified = verified
        for signal, results in reversed(selected.items()):
            history.append({"signal_date": signal.day.isoformat(), "available_at": _iso(signal.available_at),
                            "entry_date": sessions[signal.entry].day.isoformat() if signal.entry is not None else None,
                            "outcomes": results, "ex_post_252": _ex_post(signal, results["252"], path, cost)})
    conditions = tuple(items[-1] if completed else _Condition("missing", reason="no_completed_session")
                       for items in (target_rsi, cnn_conditions, vix_conditions))
    current = {"date": sessions[completed - 1].day.isoformat() if completed else None,
               "matched_count": sum(item.status == "matched" for item in conditions), "status": _verdict(conditions),
               "conditions": {key: item.wire(key) for key, item in zip(_THRESHOLDS, conditions, strict=True)}}
    coverage = {"start_date": sessions[scope.start].day.isoformat() if scope else None,
                "end_date": sessions[scope.stop - 1].day.isoformat() if scope else None,
                "completed_sessions": completed, "verified_macro_sessions": len(available),
                "verified_joint_sessions": joint_verified}
    return {"symbol": symbol, "as_of": _iso(as_of), "cost_bps": cost_bps,
            "status": "available" if joint_verified else "unverified",
            "coverage": coverage,
            "current": current, "signal_history": history, "strategies": strategies}
