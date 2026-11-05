"""Score finished plans against the bars that came after them (#64). No LLM.

A directional plan (bias long/short with entry, stop and target) made on the day it was
analysed is replayed on its horizon's bars (desk.horizons primary interval):

1. Entry: ``now`` fills on the first bar after the plan; a ``limit`` fills when the price
   comes back to the entry, a ``stop`` when it breaks through it. Not filled inside the entry
   window -> ``unfilled``.
2. After the fill: the stop or the target, whichever is touched first. A stop touched in the
   fill bar counts (a target there does not - the order inside a bar is unknown); a bar touching
   both is ``both``. Neither inside the holding window -> ``expired`` with the R multiple at the
   last close.
3. Bars that do not reach the end of a window yet leave the plan ``waiting`` (not filled) or
   ``open`` (filled) for the next pass.

TradingAgents' own settle step (an LLM reflection fed back into the PM prompt) is deliberately
not called: it would feed the scores back into the decisions this module measures.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from . import horizons
from .prices import HISTORY_LIMIT_DAYS

log = logging.getLogger(__name__)

RULES_VERSION = 1
TERMINAL = frozenset({"target", "stop", "both", "expired", "unfilled", "invalid", "unscorable"})
RETRY = (None, "waiting", "open", "error")      # outcome_status values a sweep picks up again
SESSION_GAP = timedelta(hours=2)                # a longer gap between bars starts a new session
BARS = 500
PER_PASS = 50

# Entry window when the plan has no valid_until (swing), in sessions; holding window per horizon.
ENTRY_SESSIONS = {"swing": 5}
HOLD = {"scalp": ("bars", 60), "intraday": ("sessions", 1), "short_swing": ("sessions", 3), "swing": ("sessions", 15)}


def _at(text: str) -> datetime:
    stamp = datetime.fromisoformat(text)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _sessions(bars: list[dict]) -> list[int]:
    """A session number per bar: daily and longer bars are a session each; intraday bars split
    where the gap to the previous bar is longer than SESSION_GAP (overnight, a lunch break is not)."""
    out, session, previous = [], 0, None
    for bar in bars:
        stamp = _at(bar["at"])
        if previous is not None and stamp - previous > SESSION_GAP:
            session += 1
        out.append(session)
        previous = stamp
    return out


def judge(plan: dict, bars: list[dict], horizon: horizons.Horizon, plan_at: str) -> dict:
    """The outcome of one plan on ``bars`` (oldest first, desk.prices shape). Pure function."""
    base = {"rules_version": RULES_VERSION, "interval": horizon.primary, "plan_at": plan_at}
    direction = {"long": 1, "short": -1}.get(plan.get("bias"))
    entry, stop, target = plan.get("entry_price"), plan.get("stop_loss"), plan.get("price_target")
    if direction is None or None in (entry, stop, target) or entry == stop:
        return {**base, "status": "invalid", "detail": "no directional plan with entry, stop and target"}
    reach = plan.get("entry_vs_live") or {}
    if reach.get("stop_passed") or reach.get("target_passed"):
        return {**base, "status": "invalid", "detail": "the price had already passed the stop or target"}
    kind = reach.get("kind") or plan.get("entry_type") or "limit"
    start = _at(plan_at)
    after = [b for b in bars if _at(b["at"]) >= start]
    if not after:
        return {**base, "status": "waiting", "detail": "no bars after the plan yet"}
    sessions = _sessions(after)
    valid_until = _at(plan["valid_until"]) if plan.get("valid_until") else None
    entry_sessions = ENTRY_SESSIONS.get(horizon.id, 1)

    def entry_closed(i: int) -> bool:
        if valid_until is not None:
            return _at(after[i]["at"]) > valid_until
        return sessions[i] >= entry_sessions

    fill = None
    for i, bar in enumerate(after):
        if entry_closed(i):
            return {**base, "status": "unfilled", "detail": f"no {kind} fill before the entry window closed"}
        if kind == "now":
            hit = True
        elif kind == "stop":
            hit = bar["high"] >= entry if direction > 0 else bar["low"] <= entry
        else:   # limit
            hit = bar["low"] <= entry if direction > 0 else bar["high"] >= entry
        if hit:
            fill = i
            break
    if fill is None:
        return {**base, "status": "waiting", "detail": "not filled yet"}

    filled = {**base, "entry_kind": kind, "filled_at": after[fill]["at"]}
    risk = abs(entry - stop)

    def stop_hit(bar: dict) -> bool:
        return bar["low"] <= stop if direction > 0 else bar["high"] >= stop

    def target_hit(bar: dict) -> bool:
        return bar["high"] >= target if direction > 0 else bar["low"] <= target

    if stop_hit(after[fill]):
        return {**filled, "status": "stop", "exited_at": after[fill]["at"], "r": -1.0}
    mode, length = HOLD[horizon.id]
    for i in range(fill + 1, len(after)):
        over = (i - fill > length) if mode == "bars" else (sessions[i] - sessions[fill] >= length)
        if over:
            last = after[i - 1]
            return {**filled, "status": "expired", "exited_at": last["at"],
                    "r": round((last["close"] - entry) * direction / risk, 2)}
        bar = after[i]
        t, s = target_hit(bar), stop_hit(bar)
        if t and s:
            return {**filled, "status": "both", "exited_at": bar["at"], "r": None}
        if s:
            return {**filled, "status": "stop", "exited_at": bar["at"], "r": -1.0}
        if t:
            return {**filled, "status": "target", "exited_at": bar["at"],
                    "r": round(abs(target - entry) / risk, 2)}
    last = after[-1]
    return {**filled, "status": "open", "detail": "holding window not over yet",
            "r": round((last["close"] - entry) * direction / risk, 2)}


def plan_time(run: dict) -> str | None:
    """When the plan's prices were seen: the live quote the agents used. A run for a past date
    has none and is not scored - intraday bars for it are gone, and its levels were set on an
    old close."""
    live = (run.get("plan") or {}).get("live_price") or {}
    return live.get("fetched_at")


async def score(run: dict, prices, now: datetime | None = None) -> dict:
    """Fetch the bars and judge one run; ``unscorable`` once its bars are past Yahoo's reach."""
    plan = run.get("plan") or {}
    horizon = horizons.get(plan.get("horizon") or (run.get("options") or {}).get("horizon"))
    base = {"rules_version": RULES_VERSION, "interval": horizon.primary}
    plan_at = plan_time(run)
    if plan.get("bias") not in ("long", "short"):
        return {**base, "status": "invalid", "detail": "not a directional plan"}
    if not plan_at:
        return {**base, "status": "unscorable", "detail": "no live quote: a past-date run"}
    limit = HISTORY_LIMIT_DAYS.get(horizon.primary)
    now = now or datetime.now(UTC)
    if limit and now - _at(plan_at) > timedelta(days=limit):
        return {**base, "status": "unscorable", "detail": f"{horizon.primary} bars older than {limit} days"}
    try:
        candles = await prices.candles(run["ticker"], horizon.primary, BARS)
    except Exception as exc:  # noqa: BLE001 - try again next pass
        return {**base, "status": "error", "detail": f"{type(exc).__name__}: {exc}"}
    return judge(plan, candles["bars"], horizon, plan_at)


async def sweep(store, prices, now: datetime | None = None, limit: int = PER_PASS) -> int:
    """Score the runs still waiting for an outcome; one database write per run. Returns how many
    were scored this pass."""
    done = 0
    for run in await asyncio.to_thread(store.runs_to_score, limit):
        try:
            result = await score(run, prices, now)
        except Exception as exc:
            log.exception("scoring run %s failed", run["id"])
            result = {"rules_version": RULES_VERSION, "status": "error", "detail": f"{type(exc).__name__}: {exc}"}
        await asyncio.to_thread(store.set_outcome, run["id"], result)
        done += 1
    return done


# --- statistics for GET /api/stats/plans ---------------------------------------------------

_COUNTED = ("target", "stop", "both", "expired", "unfilled", "open", "waiting")


def _group(rows: list[dict]) -> dict:
    counts = {key: 0 for key in _COUNTED}
    probabilities, expiry_r = [], []
    for row in rows:
        status = row["status"]
        if status in counts:
            counts[status] += 1
        if row["probability"] is not None:
            probabilities.append(row["probability"])
        if status == "expired" and row["r"] is not None:
            expiry_r.append(row["r"])
    resolved = counts["target"] + counts["stop"] + counts["both"]
    filled = resolved + counts["expired"] + counts["open"]
    return {"plans": len(rows), "filled": filled, **counts,
            "hit_rate": round(counts["target"] / resolved, 3) if resolved else None,
            "avg_probability": round(sum(probabilities) / len(probabilities), 3) if probabilities else None,
            "avg_r_at_expiry": round(sum(expiry_r) / len(expiry_r), 2) if expiry_r else None}


def stats(runs: list[dict]) -> dict:
    """Plans and their outcomes grouped every way the bias question needs: horizon, rating,
    direction, the desk's opinion (did the filter help?), analyst set, and a calibration of the
    model's win probability against what happened."""
    rows = []
    total = {"plans": 0, "scored": 0, "waiting": 0, "unscorable": 0}
    for run in runs:
        plan = run.get("plan") or {}
        if plan.get("bias") not in ("long", "short"):
            continue
        outcome = run.get("outcome") or {}
        status = run.get("outcome_status")
        total["plans"] += 1
        if status in TERMINAL:
            total["scored"] += 1
        if status in (None, "waiting", "open", "error"):
            total["waiting"] += 1
        if status == "unscorable":
            total["unscorable"] += 1
        analysts = (run.get("options") or {}).get("analysts") or []
        rows.append({"status": status, "probability": plan.get("win_probability"), "r": outcome.get("r"),
                     "horizon": plan.get("horizon") or "swing", "rating": plan.get("rating"),
                     "direction": plan.get("bias"), "opinion": plan.get("opinion") or "unknown",
                     "analysts": "+".join(sorted(analysts)) or "unknown"})

    def by(key: str) -> list[dict]:
        keys = sorted({row[key] for row in rows})
        return [{"key": k, **_group([row for row in rows if row[key] == k])} for k in keys]

    calibration = []
    for low in range(0, 100, 10):
        bucket = [row for row in rows if row["probability"] is not None
                  and low <= row["probability"] * 100 < low + 10 + (1 if low == 90 else 0)]
        if bucket:
            group = _group(bucket)
            calibration.append({"bucket": f"{low}-{low + 10}", "plans": group["plans"],
                                "resolved": group["target"] + group["stop"] + group["both"],
                                "hits": group["target"], "hit_rate": group["hit_rate"],
                                "avg_probability": group["avg_probability"]})
    return {"rules_version": RULES_VERSION, "total": total,
            "by_horizon": by("horizon"), "by_rating": by("rating"), "by_direction": by("direction"),
            "by_opinion": by("opinion"), "by_analysts": by("analysts"), "calibration": calibration}
