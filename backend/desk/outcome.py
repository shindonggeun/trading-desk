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


def session_open(ticker: str) -> timedelta:
    """How long after its local midnight a market opens; daily bars are stamped at that midnight."""
    return timedelta(hours=9) if ticker.upper().endswith((".KS", ".KQ")) else timedelta(hours=9, minutes=30)


def _levels_make_sense(direction: int, entry: float, stop: float, target: float) -> bool:
    return (stop < entry < target) if direction > 0 else (target < entry < stop)


def judge(plan: dict, bars: list[dict], horizon: horizons.Horizon, plan_at: str,
          opens_after: timedelta = timedelta(hours=9, minutes=30)) -> dict:
    """The outcome of one plan on ``bars`` (oldest first, desk.prices shape). Pure function.
    ``opens_after``: the market's open after local midnight (``session_open``), used for daily
    bars, which are stamped at that midnight."""
    base = {"rules_version": RULES_VERSION, "interval": horizon.primary, "plan_at": plan_at}
    direction = {"long": 1, "short": -1}.get(plan.get("bias"))
    entry, stop, target = plan.get("entry_price"), plan.get("stop_loss"), plan.get("price_target")
    if direction is None or None in (entry, stop, target):
        return {**base, "status": "invalid", "detail": "no directional plan with entry, stop and target"}
    if not _levels_make_sense(direction, entry, stop, target):
        return {**base, "status": "invalid", "detail": "stop and target are not on the right sides of the entry"}
    reach = plan.get("entry_vs_live") or {}
    if reach.get("stop_passed") or reach.get("target_passed"):
        return {**base, "status": "invalid", "detail": "the price had already passed the stop or target"}
    kind = reach.get("kind") or plan.get("entry_type") or "limit"
    start = _at(plan_at)
    valid_until = _at(plan["valid_until"]) if plan.get("valid_until") else None
    if valid_until is not None and valid_until <= start:
        # the analysis outlasted the entry window (it counts from the quote the agents saw)
        return {**base, "status": "unfilled", "late": True, "detail": "the entry window closed before the plan arrived"}
    if not bars:
        return {**base, "status": "waiting", "detail": "no bars yet"}
    daily = horizon.primary in ("1d", "1w")
    if not daily and _at(bars[0]["at"]) > start:
        return {**base, "status": "unscorable", "detail": "the bars no longer reach back to the plan"}
    if daily:
        # A daily bar opened before the plan was made can still be the session it trades in: one whose
        # market opened after the plan counts whole; one already trading only for an entry 'now'
        # (its earlier prices cannot fill a limit or stop that did not exist yet).
        after, partial = [], False
        for bar in bars:
            opened = _at(bar["at"]) + opens_after
            if opened >= start:
                after.append(bar)
            elif _at(bar["at"]) + timedelta(days=1) > start and kind == "now":
                after.append(bar)
                partial = True
    else:
        after, partial = [b for b in bars if _at(b["at"]) >= start], False
    if not after:
        return {**base, "status": "waiting", "detail": "no bars after the plan yet"}
    sessions = _sessions(after)
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
    if partial and fill == 0:
        filled["partial_first_session"] = True
    risk = abs(entry - stop)

    def stop_hit(bar: dict) -> bool:
        return bar["low"] <= stop if direction > 0 else bar["high"] >= stop

    def target_hit(bar: dict) -> bool:
        return bar["high"] >= target if direction > 0 else bar["low"] <= target

    def stop_r(bar: dict, first: bool) -> float:
        """-1 at the stop; worse when the bar opened beyond it (a gap) after the fill bar."""
        gapped = not first and ((bar["open"] < stop) if direction > 0 else (bar["open"] > stop))
        exit_price = bar["open"] if gapped else stop
        return round((exit_price - entry) * direction / risk, 2)

    if stop_hit(after[fill]):
        return {**filled, "status": "stop", "exited_at": after[fill]["at"], "r": stop_r(after[fill], True)}
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
            return {**filled, "status": "stop", "exited_at": bar["at"], "r": stop_r(bar, False)}
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


# --- the two playbooks (#116), each scored on its own -----------------------------------------

PLAYBOOK_RULES_VERSION = 1
PB_PENDING = ("waiting", "open", "error")


def judge_playbook(side: dict, direction: int, bars: list[dict], horizon: horizons.Horizon, plan_at: str,
                   opens_after: timedelta = timedelta(hours=9, minutes=30),
                   valid_until: str | None = None) -> dict:
    """One playbook's outcome: ladder fills (limits first; a stop entry only on a later bar - the
    long's confirmation step after a limit fill, a lone stop entry (the short fade) after the price
    has reached its zone), then stop / T1 (stop to break-even) / T2, the time stop, and the close
    for a flat-by-close side. ``r`` is per filled share over the planned risk; ``r_size`` scales
    it by the filled share of the full position. Pure function."""
    base = {"rules_version": PLAYBOOK_RULES_VERSION, "setup": side.get("setup")}
    entries = side.get("entries") or []
    stop0, targets = side.get("stop"), side.get("targets") or []
    if side.get("setup") in (None, "none") or not entries or stop0 is None or not targets:
        return {**base, "status": "invalid", "detail": "no playbook"}
    t1 = targets[0]["price"]
    t2 = targets[1]["price"] if len(targets) > 1 else None
    zone = side.get("zone") or {}
    start = _at(plan_at)
    daily = horizon.primary in ("1d", "1w", "1M")
    after = [b for b in bars if (_at(b["at"]) + opens_after >= start if daily else _at(b["at"]) >= start)]
    if not after:
        return {**base, "status": "waiting", "detail": "no bars after the plan yet"}
    sessions = _sessions(after)
    until = _at(valid_until) if valid_until else None
    entry_sessions = ENTRY_SESSIONS.get(horizon.id, 1)
    sign = 1 if direction > 0 else -1
    filled: list[dict] = []          # entries with the price they actually filled at
    stop, took_t1, last_fill, first_limit, armed_at = stop0, False, None, None, None
    first_fill = None                # the bar of the first fill (for excursions, desk.backtest)
    bars_limit = (side.get("time_stop") or {}).get("bars")
    flat = bool(side.get("flat_by_close")) and not daily
    lone_stop = len(entries) == 1 and entries[0]["kind"] == "stop"

    def avg() -> float:
        total = sum(e["share"] for e in filled)
        return sum(e["fill"] * e["share"] for e in filled) / total

    def result(status: str, exit_price: float | None, at: str, **extra) -> dict:
        shares = round(sum(e["share"] for e in filled), 4)
        a = avg()
        risk = abs(a - stop0) or 1e-9
        if status == "t2":
            pnl = 0.5 * (t1 - a) + 0.5 * (t2 - a)
        elif took_t1 and exit_price is not None:    # half at T1, the rest where it left (t1_be incl. a gap)
            pnl = 0.5 * (t1 - a) + 0.5 * (exit_price - a)
        else:
            pnl = (exit_price - a) if exit_price is not None else 0.0
        r = round(sign * pnl / risk, 2)
        return {**base, "status": status, "filled_shares": shares, "avg_fill": round(a, 4),
                "exited_at": at, "r": r, "r_size": round(r * shares, 2), "took_t1": took_t1,
                "steps": [e["step"] for e in filled],
                "first_fill_at": after[first_fill]["at"] if first_fill is not None else None, **extra}

    for i, bar in enumerate(after):
        window_open = (until is None or _at(bar["at"]) <= until) and (until is not None or sessions[i] < entry_sessions)
        # the fade sells a failed bounce: the price has to reach the resistance zone first
        if lone_stop and armed_at is None and zone and (
                (bar["low"] <= zone.get("high", float("inf"))) if sign > 0
                else (bar["high"] >= zone.get("low", float("-inf")))):
            armed_at = i
        if window_open and not took_t1:
            for e in entries:
                if any(f["step"] == e["step"] for f in filled):
                    continue
                if e["kind"] == "limit":
                    hit = bar["low"] <= e["price"] if sign > 0 else bar["high"] >= e["price"]
                    fill = e["price"]
                else:
                    if lone_stop:
                        ready = armed_at is not None and armed_at < i if zone else True
                    else:   # the confirmation step comes on a later bar than a limit fill
                        ready = first_limit is not None and first_limit < i
                    hit = ready and (bar["high"] >= e["price"] if sign > 0 else bar["low"] <= e["price"])
                    # a gap through a stop entry fills at the open, not at the better trigger
                    fill = max(e["price"], bar["open"]) if sign > 0 else min(e["price"], bar["open"])
                if hit:
                    filled.append({**e, "fill": fill})
                    last_fill = i
                    if first_fill is None:
                        first_fill = i
                    if e["kind"] == "limit" and first_limit is None:
                        first_limit = i
        if not filled:
            if not window_open:
                return {**base, "status": "unfilled", "detail": "no fill before the entry window closed"}
            continue
        # after a fill: the stop (fill bar included), then the targets
        if (bar["low"] <= stop) if sign > 0 else (bar["high"] >= stop):
            gapped = (bar["open"] < stop) if sign > 0 else (bar["open"] > stop)
            exit_price = bar["open"] if gapped and i != last_fill else stop
            return result("t1_be" if took_t1 else "stop", exit_price, bar["at"])
        if not took_t1 and ((bar["high"] >= t1) if sign > 0 else (bar["low"] <= t1)):
            took_t1, stop = True, avg()
        if took_t1 and t2 is not None and ((bar["high"] >= t2) if sign > 0 else (bar["low"] <= t2)):
            return result("t2", t2, bar["at"])
        if not took_t1 and bars_limit and last_fill is not None and i - last_fill >= bars_limit:
            return result("time_stop", bar["close"], bar["at"])
        if flat and i + 1 < len(after) and sessions[i + 1] != sessions[i]:
            return result("flat", bar["close"], bar["at"])
    if not filled:
        return {**base, "status": "waiting", "detail": "not filled yet"}
    last = after[-1]
    out = result("open", last["close"], last["at"])
    return {**out, "exited_at": None, "detail": "still in the trade"}


def _score_playbooks(plan: dict, bars: list[dict], horizon: horizons.Horizon, plan_at: str,
                     opens_after: timedelta) -> dict | None:
    pb = plan.get("playbooks")
    if not pb:
        return None
    out = {"version": PLAYBOOK_RULES_VERSION, "regime": (pb.get("regime") or {}).get("id")}
    for side, direction in (("long", 1), ("short", -1)):
        s = pb.get(side) or {}
        out[side] = {**judge_playbook(s, direction, bars, horizon, plan_at, opens_after, plan.get("valid_until")),
                     "score": s.get("score")}
    return out


def _sweep_status(result: dict) -> str:
    """What the sweep keys on: the plan's own status while it is pending, else a playbook still
    pending keeps the run in the sweep (a hold plan's playbooks outlive its 'invalid')."""
    if result.get("status") in PB_PENDING:
        return result["status"]
    pbs = result.get("playbooks") or {}
    pending = [pbs[s]["status"] for s in ("long", "short") if s in pbs and pbs[s]["status"] in PB_PENDING]
    return pending[0] if pending else result.get("status")


# After this long a plan still waiting or open is closed as unscorable: the entry and holding
# windows are long over, so missing bars (a delisted symbol, a feed gap) will not come back.
MAX_AGE = {"scalp": timedelta(days=3), "intraday": timedelta(days=7), "short_swing": timedelta(days=21),
           "swing": timedelta(days=60)}


async def score(run: dict, prices, now: datetime | None = None) -> dict:
    """Fetch the bars and judge one run; ``unscorable`` once its bars are past reach."""
    plan = run.get("plan") or {}
    horizon = horizons.get(plan.get("horizon") or (run.get("options") or {}).get("horizon"))
    base = {"rules_version": RULES_VERSION, "interval": horizon.primary}
    plan_at = plan_time(run)
    directional = plan.get("bias") in ("long", "short")
    if not directional and not plan.get("playbooks"):
        return {**base, "status": "invalid", "detail": "not a directional plan"}
    if not plan_at:
        return {**base, "status": "unscorable", "detail": "no live quote: a past-date run"}
    now = now or datetime.now(UTC)
    age = now - _at(plan_at)
    limit = HISTORY_LIMIT_DAYS.get(horizon.primary)
    if limit and age > timedelta(days=limit):
        return {**base, "status": "unscorable", "detail": f"{horizon.primary} bars older than {limit} days"}
    try:
        candles = await prices.candles(run["ticker"], horizon.primary, BARS)
        opens = session_open(run["ticker"])
        result = (judge(plan, candles["bars"], horizon, plan_at, opens) if directional
                  else {**base, "status": "invalid", "detail": "not a directional plan"})
        playbooks = _score_playbooks(plan, candles["bars"], horizon, plan_at, opens)
        if playbooks:
            result["playbooks"] = playbooks
    except Exception as exc:  # noqa: BLE001 - try again next pass, until MAX_AGE
        result = {**base, "status": "error", "detail": f"{type(exc).__name__}: {exc}"}
    if age > MAX_AGE[horizon.id]:
        if result["status"] in ("waiting", "open", "error"):
            result = {**result, "status": "unscorable",
                      "detail": f"still {result['status']} after {MAX_AGE[horizon.id].days} days: {result.get('detail', '')}"}
        for side in ("long", "short"):
            pb = (result.get("playbooks") or {}).get(side)
            if pb and pb["status"] in PB_PENDING:
                result["playbooks"][side] = {**pb, "status": "unscorable", "detail": f"still {pb['status']}"}
    result["sweep_status"] = _sweep_status(result)
    return result


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
    probabilities, resolved_probabilities, expiry_r = [], [], []
    for row in rows:
        status = row["status"]
        if status in counts:
            counts[status] += 1
        if row["probability"] is not None:
            probabilities.append(row["probability"])
            if status in ("target", "stop", "both"):
                resolved_probabilities.append(row["probability"])
        if status == "expired" and row["r"] is not None:
            expiry_r.append(row["r"])
    # hit_rate = target / (target + stop + both): a bar touching both counts as a miss
    resolved = counts["target"] + counts["stop"] + counts["both"]
    filled = resolved + counts["expired"] + counts["open"]
    return {"plans": len(rows), "filled": filled, **counts,
            "hit_rate": round(counts["target"] / resolved, 3) if resolved else None,
            "avg_probability": round(sum(probabilities) / len(probabilities), 3) if probabilities else None,
            # the same average over the resolved plans only - the one to set against hit_rate
            "avg_probability_resolved": (round(sum(resolved_probabilities) / len(resolved_probabilities), 3)
                                         if resolved_probabilities else None),
            "avg_r_at_expiry": round(sum(expiry_r) / len(expiry_r), 2) if expiry_r else None}


_PB_COUNTED = ("t1", "t2", "t1_be", "stop", "time_stop", "flat", "unfilled", "open", "waiting")


def _playbook_stats(runs: list[dict]) -> list[dict]:
    """By "<side>:<setup>:<regime>": how the desk's playbooks played out (#116). hit_rate =
    (t1 + t2 + t1_be) over the settled trades (those + stop + time_stop + flat); avg_r is over the
    settled trades, each scaled by its filled share of the position."""
    groups: dict[str, list[dict]] = {}
    for run in runs:
        pb_plan = (run.get("plan") or {}).get("playbooks") or {}
        scored = (run.get("outcome") or {}).get("playbooks") or {}
        regime = (pb_plan.get("regime") or {}).get("id") or "unknown"
        for side in ("long", "short"):
            setup = (pb_plan.get(side) or {}).get("setup")
            if not setup or setup == "none":
                continue
            groups.setdefault(f"{side}:{setup}:{regime}", []).append(
                {"status": (scored.get(side) or {}).get("status"), "r": (scored.get(side) or {}).get("r_size"),
                 "filled": (scored.get(side) or {}).get("filled_shares"), "score": (pb_plan.get(side) or {}).get("score")})
    out = []
    for key in sorted(groups):
        items = groups[key]
        counts = {k: sum(1 for i in items if i["status"] == k) for k in _PB_COUNTED}
        won = counts["t1"] + counts["t2"] + counts["t1_be"]
        settled = won + counts["stop"] + counts["time_stop"] + counts["flat"]
        # settled trades only, each R scaled by how much of the position actually filled
        rs = [i["r"] for i in items if i["r"] is not None and i["status"] in ("t2", "t1_be", "stop", "time_stop", "flat")]
        scores = [i["score"] for i in items if i["score"] is not None]
        out.append({"key": key, "plans": len(items), "filled": sum(1 for i in items if i["filled"]), **counts,
                    "hit_rate": round(won / settled, 3) if settled else None,
                    "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
                    "avg_score": round(sum(scores) / len(scores), 1) if scores else None})
    return out


def stats(runs: list[dict]) -> dict:
    """Plans and their outcomes grouped every way the bias question needs: horizon, rating,
    direction, the desk's opinion (did the filter help?), analyst set, and a calibration of the
    model's win probability against what happened."""
    rows = []
    total = {"plans": 0, "scored": 0, "waiting": 0, "errors": 0, "unscorable": 0}
    playbooks = _playbook_stats(runs)
    for run in runs:
        plan = run.get("plan") or {}
        if plan.get("bias") not in ("long", "short"):
            continue
        outcome = run.get("outcome") or {}
        # the plan's own status; outcome_status may be a pending playbook's (#116)
        status = outcome.get("status") if outcome else run.get("outcome_status")
        total["plans"] += 1
        if status in TERMINAL:
            total["scored"] += 1
        if status in (None, "waiting", "open"):
            total["waiting"] += 1
        if status == "error":
            total["errors"] += 1
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
                                "avg_probability": group["avg_probability_resolved"]})
    return {"rules_version": RULES_VERSION, "total": total, "by_playbook": playbooks,
            "by_horizon": by("horizon"), "by_rating": by("rating"), "by_direction": by("direction"),
            "by_opinion": by("opinion"), "by_analysts": by("analysts"), "calibration": calibration}
