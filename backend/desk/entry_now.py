"""The entry now (#312): one answer to "where do I get in, where am I wrong, where do I take it".

The desk already builds a long and a short playbook on the bars and the price now
(desk.playbook_live), each with its zone, ladder, stop, targets and its record (desk.calibration:
T1 rate and expected R per filled trade). The page used to lead with the side of the higher *score*
(desk.summary), and a score is a sum of rules, not a result. This ranks the two sides by what a
plan is worth before it is filled:

    expected R per plan = P(the first step fills | distance to it) x expected R once filled

``reach`` (the fill rate by the distance from the price now to the first step, in the frame's ATR)
comes from the basket backtest like the calibration table (``python -m desk.cli entry-backtest``
writes ``desk/data/entry_reach.json``). The better side is ``best``; the other is ``other``. When
the best is not worth taking - its expected R per plan is zero or below after costs, or its record
is thin (fewer than calibration.MIN_N trades) - ``stand_aside`` says why and ``best`` stays as the
reference the chart can draw faintly.

The long-term bottom zone (desk.bottom_zone, #240 / #310) is read only to say when one of its
ladder steps sits in the long zone; it never changes the pick. desk.sr is read, never changed.

``compare`` replays the rule on history next to the score pick, with the calibration and reach
tables built from the training dates only (the shipped tables saw every date). One bias is shared
by every arm: the playbooks pass desk.setup_filter (#285), whose rules were kept because they also
held on the held-out dates - so the arms compare fairly, but the absolute held-out R is optimistic.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import backtest, calibration, horizons, playbook, playbook_live, summary

log = logging.getLogger(__name__)

VERSION = 1
REACH_PATH = Path(__file__).parent / "data" / "entry_reach.json"
REPORT_DIR = Path(__file__).parent / "data" / "backtest"
EDGES = (0.25, 1.0, 2.0, 3.0)    # distance buckets in ATR: in the zone, under 1, 1-2, 2-3, beyond
MIN_N = calibration.MIN_N        # a record or a reach cell thinner than this gives no number
TIE_R = 0.02                     # expected R per plan this close: the side with the trend wins (#166)
BOTTOM_SEC = 5.0                 # the bottom zone's own budget (a cold cache fetches ~20 years of weeks)
BOTTOM_NEAR_ATR = 0.5            # a bottom ladder step this close to the long zone counts as in it
BOTTOM_STAGES = ("approach", "bottom")   # the stages whose ladder the chart draws (#241) - the overlap is named only then
KIND_KO = {"now": "지금 구역 안", "limit": "지정가(구역까지 기다림)", "breakout": "돌파 · 이탈 확인 뒤(스톱 진입)"}
SIDE_KO = {"long": "롱", "short": "숏"}


# ---------------------------------------------------------------- reach


def bucket(distance_atr: float) -> str:
    for edge in EDGES:
        if distance_atr <= edge:
            return f"<={edge:g}"
    return f">{EDGES[-1]:g}"


def _first(side: dict) -> dict | None:
    entries = side.get("entries") or []
    return entries[0] if entries else None


def _kind(side: dict) -> str:
    first = _first(side) or {}
    return "stop" if first.get("kind") == "stop" else "limit"


def distance_atr(side: dict, up: bool, ref: float | None, atr: float | None) -> float | None:
    """How far the first step is from the price now, in ATR; 0 with the price already in the zone."""
    first, zone = _first(side), side.get("zone") or {}
    if not first or ref is None or not atr:
        return None
    lo, hi = zone.get("low"), zone.get("high")
    if lo is not None and hi is not None and _kind(side) == "limit" and (ref <= hi if up else ref >= lo):
        return 0.0
    return round(abs(first["price"] - ref) / atr, 3)


def _reach_keys(horizon: str, side: str, kind: str, b: str) -> list[str]:
    return [f"{horizon}|{side}|{kind}|{b}", f"{horizon}|{side}|*|{b}", f"{horizon}|{side}|*|*"]


class Reach:
    """Fill rate of the first ladder step by (horizon, side, entry kind, distance bucket)."""

    def __init__(self, cells: dict | None = None, meta: dict | None = None) -> None:
        self.cells = cells or {}
        self.meta = meta or {}

    def add(self, horizon: str, side: str, kind: str, distance: float, filled: bool) -> None:
        for k in _reach_keys(horizon, side, kind, bucket(distance)):
            c = self.cells.setdefault(k, {"n": 0, "filled": 0})
            c["n"] += 1
            c["filled"] += 1 if filled else 0

    def look(self, horizon: str, side: str, kind: str, distance: float | None) -> dict:
        """{"p", "n", "level"}; p None with fewer than MIN_N plans even in the side's overall cell."""
        if distance is None:
            return {"p": None, "n": 0, "level": None}
        for level, k in zip(("kind", "bucket", "side"), _reach_keys(horizon, side, kind, bucket(distance)), strict=True):
            c = self.cells.get(k)
            if c and c["n"] >= MIN_N:
                return {"p": round(c["filled"] / c["n"], 3), "n": c["n"], "level": level}
        return {"p": None, "n": 0, "level": None}

    @classmethod
    def load(cls, path: Path = REACH_PATH) -> Reach:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("entry reach table %s unreadable: %s", path, exc)
            return cls()
        return cls(data.get("cells") or {}, {k: v for k, v in data.items() if k != "cells"})

    def write(self, meta: dict, path: Path = REACH_PATH) -> None:
        body = {"version": VERSION, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), **meta,
                "cells": dict(sorted(self.cells.items()))}
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as f:      # LF on Windows too (.gitattributes)
            f.write(json.dumps(body, ensure_ascii=False, indent=1) + "\n")


REACH = Reach.load()


# ---------------------------------------------------------------- the pick (pure)


def _round(x: float | None) -> float | None:
    return summary._round(x)


def candidate(name: str, side: dict | None, frame: dict, horizon: str, reach: Reach) -> dict | None:
    """One side as an entry: where, how, how likely it fills, what it is worth. None without a setup."""
    if not side or side.get("setup") in (None, "none") or not side.get("entries") or side.get("stop") is None:
        return None
    if side.get("state") in ("invalid", "closed"):      # the price is through its stop: not an entry now
        return None
    up = name == "long"
    ref, atr = frame.get("reference_price"), frame.get("atr")
    dist = distance_atr(side, up, ref, atr)
    kind = _kind(side)
    got = reach.look(horizon, name, kind, dist)
    cal = side.get("calibration") or {}
    thin = cal.get("thin", True) if cal else True
    exp_r = None if thin else cal.get("exp_r")
    ev = round(got["p"] * exp_r, 3) if got["p"] is not None and exp_r is not None else None
    zone = side.get("zone") or {}
    first = _first(side)["price"]
    stop, avg = side["stop"], side.get("avg_entry")
    targets = [{"role": t.get("role"), "price": t.get("price"), "rr": t.get("rr")} for t in side.get("targets") or []]
    # the live page's zone state allows a little slack (desk.playbook.side_state): "in the zone" there is "now" here
    inside = dist == 0.0 or (kind == "limit" and side.get("state") in ("in_zone", "confirmed"))
    how = "now" if inside else ("breakout" if kind == "stop" else "limit")
    members = [m.get("basis") or m.get("source") for m in zone.get("members") or []]
    why = list(dict.fromkeys(m for m in members if m))[:4]
    if cal and not thin:
        why.append(f"데스크 기록 {cal.get('n')}건 · 1차 목표 먼저 {round((cal.get('p_t1') or 0) * 100)}% · "
                   f"체결되면 기대 {exp_r:+.2f}R")
    elif cal:
        why.append(f"데스크 기록 {cal.get('n', 0)}건 — 표본이 얇아 기대값 없음")
    if got["p"] is not None:
        why.append(f"지금가에서 {dist:.1f} ATR — 과거 같은 거리의 체결 {round(got['p'] * 100)}% ({got['n']}건)")
    if side.get("direction") == "with":
        why.append("추세 방향과 같은 쪽")
    elif side.get("direction") == "against":
        why.append("추세 반대쪽(역추세)")
    return {
        "side": name, "side_ko": SIDE_KO[name], "setup": side.get("setup"), "kind": how, "kind_ko": KIND_KO[how],
        "zone": [zone.get("low"), zone.get("high")], "first": first,
        "entries": [{"step": e.get("step"), "price": e.get("price"), "share": e.get("share"), "kind": e.get("kind")}
                    for e in side.get("entries") or []],
        "avg_entry": avg, "stop": stop,
        "stop_pct": round((stop / avg - 1) * 100, 2) if stop and avg else None,
        "targets": targets,
        "from_now_pct": round((first / ref - 1) * 100, 2) if ref else None,
        "distance_atr": dist,
        "reach_prob": got["p"], "reach_n": got["n"],
        "p_t1": None if thin else cal.get("p_t1"),
        "expected_r": exp_r,               # per filled trade (desk.calibration)
        "ev_r": ev,                        # per plan: reach x expected R
        "n": cal.get("n", 0) if cal else 0, "thin": thin,
        "score": side.get("score"), "direction": side.get("direction") or "neutral",
        "state": side.get("state"), "why": why,
    }


def _order(cands: list[dict]) -> list[dict]:
    """By the worth per plan; two sides within TIE_R of each other and on the same side of zero go to
    the side with the trend (#166). A side of unknown worth comes last (trend, then score)."""
    known = sorted((c for c in cands if c["ev_r"] is not None), key=lambda c: (-c["ev_r"], -(c["score"] or 0)))
    unknown = sorted((c for c in cands if c["ev_r"] is None),
                     key=lambda c: (c["direction"] != "with", -(c["score"] or 0)))
    if len(known) == 2:
        a, b = known
        if (abs(a["ev_r"] - b["ev_r"]) <= TIE_R and (a["ev_r"] > 0) == (b["ev_r"] > 0)
                and b["direction"] == "with" and a["direction"] != "with"):
            known = [b, a]
    return known + unknown


def choose(pb: dict | None, horizon: str, reach: Reach | None = None) -> dict:
    """{"best", "other", "stand_aside"} from both playbooks. ``best`` is the top candidate even when
    it is not worth taking (then ``stand_aside`` says why); None only when neither side has a setup."""
    reach = reach or REACH
    if not pb:
        return {"best": None, "other": None, "stand_aside": {"reason": "플레이북을 만들지 못했다(봉 · 레벨 부족)"}}
    frame = pb.get("frame") or {}
    cands = [c for c in (candidate(s, pb.get(s), frame, horizon, reach) for s in ("long", "short")) if c]
    if not cands:
        reasons = [(pb.get(s) or {}).get("reason") for s in ("long", "short")]
        return {"best": None, "other": None,
                "stand_aside": {"reason": next((r for r in reasons if r), "롱 · 숏 모두 타점 근거가 없다")}}
    cands = _order(cands)
    best, other = cands[0], (cands[1] if len(cands) > 1 else None)
    stand = None
    if best["ev_r"] is None:
        if best["thin"]:
            reason = f"{best['side_ko']} 타점의 기록이 얇다({best['n']}건 < {MIN_N}) — 기대값을 말할 근거 없음"
        elif best["distance_atr"] is None:
            reason = "지금가나 ATR 이 없어 타점까지 거리를 잴 수 없다"
        else:
            reason = f"{best['side_ko']} 타점까지 거리({best['distance_atr']:.1f} ATR)의 체결 기록이 얇다"
        stand = {"reason": reason}
    elif best["ev_r"] <= 0:
        stand = {"reason": f"가장 나은 {best['side_ko']} 타점도 계획당 기대 {best['ev_r']:+.2f}R — 비용 뒤 음수"}
    return {"best": best, "other": other, "stand_aside": stand}


def pick(pb: dict | None, horizon: str, reach: Reach | None = None) -> str | None:
    """The side to take, or None to stand aside (the backtest's arm)."""
    got = choose(pb, horizon, reach)
    return None if got["stand_aside"] or not got["best"] else got["best"]["side"]


# ---------------------------------------------------------------- live


def _bottom_note(best: dict | None, other: dict | None, z: dict | None, atr: float | None) -> dict | None:
    """The bottom zone in one line; its ladder steps inside the long zone go to that candidate's why."""
    if not z or not z.get("supported", True) or z.get("stage") in (None, "none"):
        return None
    out = {"stage": z.get("stage"), "stage_ko": z.get("stage_ko"), "score": z.get("score")}
    if z.get("stage") not in BOTTOM_STAGES:      # "watch": its first step is just the price now - no overlap to speak of
        return out
    for c in (best, other):
        if not c or c["side"] != "long" or not atr:
            continue
        lo, hi = c["zone"]
        if lo is None or hi is None:
            continue
        for step in z.get("ladder") or []:
            p = step.get("price")
            if p is not None and lo - BOTTOM_NEAR_ATR * atr <= p <= hi + BOTTOM_NEAR_ATR * atr:
                c["why"].append(f"인생 타점 {step.get('step')}차 {p} 와 겹침({z.get('stage_ko')})")
    return out


async def entry_now(prices, ticker: str, horizon: str | None = None, risk=None, bottoms=None,
                    reach: Reach | None = None) -> dict:
    """The entry now on the live playbooks (desk.playbook_live, cached per horizon)."""
    h = horizons.get(horizon)
    pb = await playbook_live.current(prices, ticker, h.id, risk=risk)
    got = choose(pb, h.id, reach)
    live = pb.get("live") or {}
    frame = pb.get("frame") or {}
    bottom = None
    if bottoms is not None:
        try:
            z = await asyncio.wait_for(asyncio.to_thread(bottoms.one, ticker, live.get("price"), live.get("source")),
                                       BOTTOM_SEC)
            bottom = _bottom_note(got["best"], got["other"], z, frame.get("atr"))
        except Exception as exc:  # noqa: BLE001 - an extra line; the entry stands without it
            log.info("bottom zone for the entry of %s skipped: %s", ticker, type(exc).__name__)
    return {
        "version": VERSION, "ticker": ticker, "horizon": h.id,
        "price_now": frame.get("reference_price"), "price_source": live.get("source"),
        "price_at": live.get("fetched_at"), "stale": live == {},
        "atr": frame.get("atr"), "regime": (pb.get("regime") or {}).get("id"),
        **got, "bottom": bottom, "as_of": pb.get("as_of"),
        "basis": ("계획당 기대 R = 지금가에서 첫 단계까지 거리의 과거 체결률 × 체결된 거래의 기대 R(비용 포함, "
                  "데스크 백테스트 + 실거래 기록). 예측이 아니라 같은 규칙의 과거 성적이다."),
    }


# ---------------------------------------------------------------- backtest


def _side_row(pb: dict, name: str, h: str, trade) -> dict | None:
    side = pb.get(name) or {}
    if side.get("setup") in (None, "none") or not side.get("entries"):
        return None
    frame = pb.get("frame") or {}
    dist = distance_atr(side, name == "long", frame.get("reference_price"), frame.get("atr"))
    if dist is None:
        return None
    return {"side": name, "kind": _kind(side), "distance": dist, "filled": bool(trade and trade.filled_shares)}


def _plan_r(trade) -> float:
    """What the plan made: a settled (or marked) trade's net R; unfilled, waiting or invalid is 0."""
    if trade is None or not trade.filled_shares or trade.status not in backtest.SETTLED + backtest.MARKED:
        return 0.0
    return trade.r_net or 0.0


def _max_dd(rows: list[tuple[str, float]]) -> float:
    """The deepest fall of the cumulative R summed by plan day (overlapping plans: the rules, not an account)."""
    by_day: dict[str, float] = {}
    for day, r in rows:
        by_day[day] = by_day.get(day, 0.0) + r
    peak = cum = dd = 0.0
    for day in sorted(by_day):
        cum += by_day[day]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 2)


def _arm(picks: list[tuple[str, object]]) -> dict:
    """picks: (day, Trade or None) per plan - None is a plan not taken (0 R)."""
    taken = [t for _d, t in picks if t is not None]
    filled = [t for t in taken if t.filled_shares]
    settled = [t for t in filled if t.status in backtest.SETTLED + backtest.MARKED and t.r_net is not None]
    rs = [_plan_r(t) for _d, t in picks]
    return {
        "plans": len(picks), "taken": len(taken), "filled": len(filled), "settled": len(settled),
        "fill_rate": round(len(filled) / len(taken), 3) if taken else None,
        "t1_first_rate": round(sum(1 for t in settled if t.took_t1) / len(settled), 3) if settled else None,
        "expectancy_r": round(sum(t.r_net for t in settled) / len(settled), 3) if settled else None,
        "expectancy_per_plan_r": round(sum(rs) / len(rs), 4) if rs else None,
        "total_r": round(sum(rs), 1),
        "max_dd_r": _max_dd([(d, _plan_r(t)) for d, t in picks]),
        # where the taken plans came from: a pick that only wins on one ticker or one setup is luck
        "by_setup": _tally(taken, lambda t: f"{t.side}:{t.setup}"),
        "by_ticker": _tally(taken, lambda t: t.ticker),
    }


def _tally(taken: list, key) -> dict:
    out: dict[str, dict] = {}
    for t in taken:
        row = out.setdefault(key(t), {"taken": 0, "settled": 0, "r": 0.0})
        row["taken"] += 1
        if t.filled_shares and t.status in backtest.SETTLED + backtest.MARKED and t.r_net is not None:
            row["settled"] += 1
            row["r"] += t.r_net
    return {k: {**v, "r": round(v["r"], 2)} for k, v in sorted(out.items())}


def _reliability(rows: list[dict], reach: Reach, table: calibration.Table, h: str) -> dict:
    """Held-out: the reach the training table gave against the fills that happened, per bucket, and
    the expected R it gave against the R the filled trades made."""
    by: dict[str, dict] = {}
    exp = {"n": 0, "predicted": 0.0, "actual": 0.0}
    for row in rows:
        for name in ("long", "short"):
            s = row["sides"].get(name)
            if not s:
                continue
            got = reach.look(h, name, s["kind"], s["distance"])
            if got["p"] is not None:
                b = by.setdefault(bucket(s["distance"]), {"n": 0, "predicted": 0.0, "filled": 0})
                b["n"] += 1
                b["predicted"] += got["p"]
                b["filled"] += 1 if s["filled"] else 0
            t = row["trades"].get(name)
            side = row["pb"].get(name) or {}
            if t is None or not t.filled_shares or t.status not in backtest.SETTLED + backtest.MARKED or t.r_net is None:
                continue
            cal = table.look(h, name, side.get("setup"), (row["pb"].get("regime") or {}).get("id"), side.get("score"))
            if not cal["thin"]:
                exp["n"] += 1
                exp["predicted"] += cal["exp_r"]
                exp["actual"] += t.r_net
    reach_rows = [{"bucket": k, "n": v["n"], "predicted": round(v["predicted"] / v["n"], 3),
                   "actual": round(v["filled"] / v["n"], 3)} for k, v in sorted(by.items())]
    return {"reach": reach_rows,
            "expected_r": ({"n": exp["n"], "predicted": round(exp["predicted"] / exp["n"], 3),
                            "actual": round(exp["actual"] / exp["n"], 3)} if exp["n"] else None)}


def replay(snaps, h: horizons.Horizon, costs: backtest.Costs | None = None, progress=None) -> list[dict]:
    """Per snapshot: the playbooks (as a live run builds them), each side's trade on the bars after it."""
    costs = costs or backtest.Costs()
    rows = []
    for i, (snap, primary) in enumerate(snaps):
        try:
            pb = backtest.playbooks_at(snap, h)
            if not pb:
                continue
            future = primary[snap.t + 1: snap.t + 1 + backtest.LOOKAHEAD[h.id]]
            trades = {t.side: t for t in backtest.judge(pb, snap, future, h, costs)}
        except Exception as exc:  # noqa: BLE001 - one odd bar does not end the run
            log.debug("entry replay %s %s: %s", snap.ticker, snap.day, exc)
            continue
        sides = {name: _side_row(pb, name, h.id, trades.get(name)) for name in ("long", "short")}
        rows.append({"ticker": snap.ticker, "day": snap.day, "pb": pb, "trades": trades,
                     "sides": {k: v for k, v in sides.items() if v}})
        if progress and i % 500 == 0:
            progress(f"{snap.ticker} {snap.day} ({i})")
    return rows


def compare(rows: list[dict], h: horizons.Horizon) -> dict:
    """The score pick (desk.summary, today's card) against the expected-R pick, with and without
    standing aside, on the training and the held-out dates. The calibration and reach tables are
    built on training only (desk.setup_filter's bias is shared by every arm - see the module note)."""
    cut = backtest.split_cut(r["day"] for r in rows)
    if cut is None:
        return {"cut": None, "note": "계획이 없다"}
    edge = (date.fromisoformat(cut) - timedelta(days=backtest.EMBARGO_DAYS[h.id])).isoformat()
    train = [r for r in rows if r["day"] < edge]
    valid = [r for r in rows if r["day"] >= cut]
    table = calibration.Table(calibration.from_trades([t for r in train for t in r["trades"].values()], h.id))
    reach = Reach()
    for r in train:
        for s in r["sides"].values():
            reach.add(h.id, s["side"], s["kind"], s["distance"], s["filled"])
    arms: dict[str, dict] = {}
    for split_name, part in (("train", train), ("valid", valid)):
        picks: dict[str, list] = {"score": [], "ev": [], "entry_now": []}
        for r in part:
            # the training record, not the shipped one; on a copy (calibrate_sides writes into the sides)
            pb = playbook.calibrate_sides(copy.deepcopy(r["pb"]), h, table)
            base = summary._pick_side(None, pb)
            got = choose(pb, h.id, reach)
            ev_side = got["best"]["side"] if got["best"] and got["best"]["ev_r"] is not None else base
            now_side = None if got["stand_aside"] or not got["best"] else got["best"]["side"]
            for arm, side in (("score", base), ("ev", ev_side), ("entry_now", now_side)):
                picks[arm].append((r["day"], r["trades"].get(side) if side else None))
        for arm, p in picks.items():
            arms.setdefault(arm, {})[split_name] = _arm(p)
    return {"cut": cut, "embargo_days": backtest.EMBARGO_DAYS[h.id],
            "plans": {"train": len(train), "valid": len(valid)},
            "arms": arms, "reliability_valid": _reliability(valid, reach, table, h.id)}


def full_reach(rows: list[dict], h: horizons.Horizon) -> Reach:
    """Every date's fills: the table a live page reads (like the calibration prior, all dates)."""
    reach = Reach()
    for r in rows:
        for s in r["sides"].values():
            reach.add(h.id, s["side"], s["kind"], s["distance"], s["filled"])
    return reach


def gather(tickers: list[str], h: horizons.Horizon, start: str | None, fetch=None, progress=None,
           vix=True, risk=True, step: int | None = None) -> tuple[list, list[str]]:
    """The basket's snapshots as desk.backtest.run takes them (VIX, the market's selling score)."""
    fetch = fetch or backtest.history
    notes: list[str] = []
    if vix is True:
        try:
            vix = backtest.vix_closes(start)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"^VIX 없음 — {type(exc).__name__}")
            vix = None
    if risk is True:
        risk = backtest.market_risk(start, tickers)
        if not risk.series:
            notes.append("시장 투매 지표 없음 — #146 규칙 뺌")
            risk = None
    snaps = []
    for ticker in tickers:
        try:
            primary, secondary, more = fetch(ticker, h, start)
            notes += [f"{ticker}: {n}" for n in more]
            snaps += [(s, primary) for s in backtest.snapshots(ticker, h, primary, secondary, start, None, step,
                                                                 progress, vix or None, risk or None)]
        except Exception as exc:  # noqa: BLE001 - one ticker failing leaves the others
            notes.append(f"{ticker}: 건너뜀 — {type(exc).__name__}: {exc}")
    return snaps, notes
