"""Today's direction calls kept and scored (#572 PR-B): desk.day_call computes a call, this module makes
them on time, stores them, scores them after the close and reads them back for the API and #571.

- A round is called only inside its window (WINDOWS, the market's local time on its open days): a call
  made late would see what the cutoff hides, so a missed window stays missed (``missed`` in the stats).
- Both calls (desk.day_call.CALLS: prev_close and open) are made together; a round is done when both
  rows exist (unique market, session, round, call - a second pass inserts nothing).
- Scoring waits SCORE_AFTER past the close (13:00 ET on a US half day) and tries at most every
  SCORE_EVERY; a hold (판단 보류) has no direction hit, its stage and Brier still count.
- The record a call shows next to it: these rows over RECORD_DAYS, from the live calls once there
  are LIVE_RECORD_MIN of them, else from the hold-out backfill (``basis``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from . import day_call as dc
from . import day_rounds as dr
from . import market_calendar
from .db import day_calls as t
from .db import now as db_now

log = logging.getLogger(__name__)

WINDOWS = {("kr", 1): (time(7, 30), time(8, 0)), ("kr", 2): (time(9, 30), time(11, 0)),
           ("us", 1): (time(9, 0), time(9, 30)), ("us", 2): (time(10, 0), time(10, 30))}
SCORE_AFTER = timedelta(minutes=30)
GIVE_UP_AFTER = timedelta(days=2)    # a call Yahoo still has no answer for is marked unscorable
RETRY_EVERY = timedelta(minutes=10)  # a round tried already waits this long before the next fetch
SHARE_FOR = timedelta(minutes=1)     # bars fetched within this are reused (the calls, then the scoring)
SCORE_EVERY = timedelta(minutes=30)
TICK_SEC = 120
DAILY_BARS = 300
HOURLY_BARS = 300
RECORD_DAYS = 90
LIVE_RECORD_MIN = 30
SHOWN = ("prev_close", "open")
ROUND_WINDOW = timedelta(minutes=40)  # #619 an intraday round is called from its time until this after
JUMP_PUSH = 2                          # #619 a round is pushed when its stage moved this many from the last round
NOTE = [
    "판정 정의(전일 종가 대비 + 야간 선물)는 같은 검증 90일의 변형 비교를 보고 골랐다 — 숫자가 조금 낙관적일 수 있다",
    "야간 선물 특징은 야후 60분봉이 약 2년뿐이라 학습이 1.4~1.8년이다",
    "2차의 전일 종가 대비 적중에는 이미 찍힌 시가 갭 몫이 크다",
]
WORD = {"long": "롱 우세", "short": "숏 우세", "hold": "판단 보류"}


def fetch_series(candles: Callable, hourly_bars: int = HOURLY_BARS, daily_bars: int = DAILY_BARS) -> dict:
    """The bars a call reads: daily for every symbol, hourly futures (desk.prices yahoo_candles shape).
    A symbol that fails comes back empty: the calls that need it say NoData (prev_close falls back to
    the model without the futures), the others are still made."""
    def get(symbol: str, interval: str, count: int) -> list:
        try:
            return [b for b in candles(symbol, interval, count) if b.close]
        except Exception as exc:  # noqa: BLE001 - one flaky ticker must not lose the round
            log.info("day calls: %s %s failed: %s", symbol, interval, type(exc).__name__)
            return []

    out = {s: get(s, "1d", daily_bars) for s in dc.SYMBOLS}
    for key in (*dc.HOURLY, *dr.HOURLY.values()):
        out[key] = get(key.split("@")[0], "60m", hourly_bars)
    return out


def names_of(market: str, round_: int) -> tuple[str, ...]:
    """The calls a round makes: the day call's two (1차 · 2차), or #619's "to the close" (3차 on)."""
    return (dr.CALL,) if round_ in dr.ROUNDS[market] else SHOWN


def windows(market: str, day: date) -> dict[int, tuple[time, time]]:
    """Each round of ``market``'s session on ``day`` and its local window: the two morning rounds (a
    KRX 10:00 open has no 2차) and the intraday ones before the close (desk.day_rounds.rounds_of)."""
    out = {}
    for (mk, round_), span in WINDOWS.items():
        if mk == market and not (market == "kr" and round_ == 2 and dc.kr_late_open(day)):
            out[round_] = span
    for round_ in dr.rounds_of(market, day):
        start = dr.round_time(market, round_)
        out[round_] = (start, (datetime.combine(day, start) + ROUND_WINDOW).time())
    return out


def closes_at(market: str, day: date) -> datetime:
    """The session's close as the answer reads it (desk.day_call.session_close: 16:30 on the CSAT)."""
    return dc.session_close(market, day)


def _rate(values: Sequence) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None


class DayCalls:
    """The calls in the database. ``candles`` (desk.prices yahoo_candles) None: nothing is fetched,
    the loop does not run (fake runs) - the stored rows are still served."""

    def __init__(self, engine, candles: Callable | None = None, models: dict | None = None,
                 reports: dict | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 round_models: dict | None = None, round_reports: dict | None = None) -> None:
        self.engine = engine
        self.candles = candles
        self.models = models if models is not None else {m: dc.load_model(m) for m in dc.MARKETS}
        self.reports = reports if reports is not None else {m: _load_report(m) for m in dc.MARKETS}
        self.clock = clock
        self.round_models = round_models if round_models is not None else {m: dr.load_model(m) for m in dc.MARKETS}
        self.round_reports = (round_reports if round_reports is not None
                              else {m: dr.load_report(m) for m in dc.MARKETS})
        self._scored_try: datetime | None = None
        self._tried: dict[tuple[str, int, date], datetime] = {}
        self._fetched: tuple[datetime, dict] | None = None

    # ------------------------------------------------------------------ making calls

    def _have(self, market: str, day: date, round_: int) -> set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(t.c.call).where(and_(t.c.market == market, t.c.trade_date == day,
                                                            t.c.round == round_))).scalars().all()
        return set(rows)

    def due(self, now: datetime | None = None) -> list[tuple[str, int, date]]:
        """The rounds whose window is open now and whose calls are not all stored."""
        now = now or self.clock()
        out = []
        for market, m in dc.MARKETS.items():
            local = now.astimezone(m.zone)
            day = local.date()
            if not market_calendar.is_open(m.calendar, day):
                continue
            for round_, (start, end) in windows(market, day).items():
                names = names_of(market, round_)
                if not (self.round_models if names == (dr.CALL,) else self.models).get(market):
                    continue
                if start <= local.time() < end and not set(names) <= self._have(market, day, round_):
                    out.append((market, round_, day))
        return out

    def make(self, market: str, round_: int, day: date, series: dict, *, source: str = "live",
             called_at: datetime | None = None) -> list[dict]:
        """Both calls of a round, stored; the rows inserted now (a call already there, or one whose
        features are missing, is left out)."""
        names = names_of(market, round_)
        model_file = (self.round_models if names == (dr.CALL,) else self.models)[market]
        made = []
        for name in names:
            try:
                c = (dr.call(model_file, market, round_, day, series) if name == dr.CALL
                     else dc.call(model_file, market, round_, day, series, name))
            except dc.NoData as exc:
                log.info("day call %s %s round %s %s: %s", market, day, round_, name, exc)
                continue
            used = "prev_close_plain" if c["fallback"] else name
            row = {"market": market, "trade_date": day, "round": round_, "call": name, "model": used,
                   "model_version": model_file["version"], "source": source,
                   # UTC: SQLite keeps no offset, and a Seoul or New York time would read back hours off
                   "called_at": (called_at or db_now()).astimezone(UTC),
                   "cutoff_at": datetime.fromisoformat(c["cutoff_at"]).astimezone(UTC),
                   "stage": c["stage"], "verdict": c["verdict"], "probs": c["probs"], "reasons": c["reasons"],
                   "edges_pct": c["edges_pct"], "features": {k: round(v, 6) for k, v in c["features"].items()}}
            try:
                with self.engine.begin() as conn:
                    conn.execute(insert(t).values(**row))
            except IntegrityError:          # another pass stored it first
                continue
            made.append(row)
        return made

    def run(self, now: datetime | None = None) -> list[dict]:
        """One pass: the due rounds' calls (one fetch for all of them). The rows made. A round tried
        already (some call said NoData: a bar not out yet, Yahoo refusing) waits RETRY_EVERY before
        the next try - a window would otherwise fetch every symbol every TICK_SEC."""
        now = now or self.clock()
        due = [d for d in self.due(now)
               if not (last := self._tried.get(d)) or now - last >= RETRY_EVERY]
        if not due or self.candles is None:
            return []
        series = self._series(now)
        made = []
        for key in due:
            self._tried[key] = now
            made += self.make(*key, series)
        if len(self._tried) > 64:            # days gone by
            self._tried = {k: v for k, v in self._tried.items() if now - v < timedelta(days=1)}
        return made

    def _series(self, now: datetime) -> dict:
        """The bars, fetched once for a pass: the scoring right after the calls reuses them."""
        if self._fetched and now - self._fetched[0] < SHARE_FOR:
            return self._fetched[1]
        series = fetch_series(self.candles)
        self._fetched = (now, series)
        return series

    # ------------------------------------------------------------------ scoring

    def score(self, now: datetime | None = None, series: dict | None = None, force: bool = False) -> int:
        """Score the calls whose session closed SCORE_AFTER ago; at most every SCORE_EVERY unless
        ``force``. Returns how many were scored."""
        now = now or self.clock()
        if not force and self._scored_try and now - self._scored_try < SCORE_EVERY:
            return 0
        with self.engine.connect() as conn:
            rows = conn.execute(select(t).where(t.c.scored_at.is_(None))).mappings().all()
        ready = [r for r in rows if closes_at(r["market"], r["trade_date"]) + SCORE_AFTER <= now]
        if not ready:
            return 0
        self._scored_try = now
        if series is None:
            if self.candles is None:
                return 0
            series = self._series(now)
        done = 0
        for r in ready:
            if r["call"] == dr.CALL:
                answer = dr.label(r["market"], r["trade_date"], r["round"], series,
                                  (r["features"] or {}).get("anchor"), now)
            else:
                answer = dc.label(r["market"], r["trade_date"], series, "oc" if r["call"] == "open" else "cc", now)
            if answer is None:
                if now - closes_at(r["market"], r["trade_date"]) < GIVE_UP_AFTER:
                    continue            # Yahoo may not have the bar yet
                # no bar, or the session before is missing: stop asking (stats leave it out)
                out = {"unscorable": True, "scored_at": now.isoformat()}
                values = {"outcome": out, "hit_direction": None, "hit_stage": None, "brier": None}
            else:
                out = dc.score(r["probs"], answer)
                # the direction the probabilities lean, scored on every call (as the backtest does);
                # the call's own direction hit is None on a hold (판단 보류)
                out["raw_hit_direction"] = out["hit_direction"]
                if r["verdict"] == "hold":
                    out["hit_direction"] = None
                out["scored_at"] = now.isoformat()
                values = {"outcome": out, "hit_direction": out["hit_direction"], "hit_stage": out["hit_stage"],
                          "brier": out["brier"]}
            with self.engine.begin() as conn:
                conn.execute(update(t).where(t.c.id == r["id"]).values(**values, scored_at=now.astimezone(UTC)))
            done += 1
        return done

    # ------------------------------------------------------------------ backfill

    def backfill(self, market: str, series: dict, days: int = RECORD_DAYS, now: datetime | None = None) -> int:
        """The sessions after the model's training (its hold-out) re-made as of each cutoff and scored,
        ``source`` backfill - never a session the model was fitted on."""
        now = now or self.clock()
        m = dc.MARKETS[market]
        since = now.astimezone(m.zone).date() - timedelta(days=days)
        firsts = {}                         # call family -> the first session its model was not fitted on
        if self.models.get(market):
            firsts["day"] = max(date.fromisoformat(r["trained_through"]) for c in self.models[market]["calls"].values()
                                for r in c["rounds"].values()) + timedelta(days=1)
        if self.round_models.get(market):
            firsts["rounds"] = date.fromisoformat(self.round_models[market]["model"]["trained_through"]) + timedelta(days=1)
        sessions = sorted({dc.session_day(m.target, b) for b in series[m.target]})
        made = 0
        for day in sessions:
            if closes_at(market, day) > now:
                continue
            plan = []
            if "day" in firsts and day >= max(firsts["day"], since):
                plan += [(r, dc.cutoff_of(market, day, r)) for r in sorted(m.rounds)]
            if "rounds" in firsts and day >= max(firsts["rounds"], since):
                plan += [(r, dr.cutoff_of(market, day, r)) for r in dr.rounds_of(market, day)]
            for round_, cutoff in plan:
                made += len(self.make(market, round_, day, series, source="backfill", called_at=cutoff))
        self.score(now, series, force=True)
        return made

    # ------------------------------------------------------------------ reading

    def _rows(self, market: str, since: date | None = None) -> list[dict]:
        q = select(t).where(t.c.market == market)
        if since:
            q = q.where(t.c.trade_date >= since)
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(q.order_by(t.c.trade_date.desc(), t.c.round.desc(),
                                                              t.c.call.desc())).mappings()]

    def entry(self, r: dict) -> dict:
        """A stored call as the API gives it (#572 contract)."""
        probs = r["probs"]
        intraday = r["call"] == dr.CALL
        return {"call": r["call"], "call_label": dr.CALL_KO if intraday else dc.CALL_KO[r["call"]],
                "measured_from": "round" if intraday else "open" if r["call"] == "open" else "prev_close",
                "fallback": r["model"] == "prev_close_plain", "date": r["trade_date"].isoformat(),
                "round": r["round"],
                "round_label": dr.round_label(r["market"], r["round"]) if intraday else dc.ROUND_KO[r["round"]],
                **({"anchor": (r["features"] or {}).get("anchor")} if intraday else {}),
                "called_at": _iso(r["called_at"]), "cutoff_at": _iso(r["cutoff_at"]),
                "stage": r["stage"], "label": dc.LABEL_KO[r["stage"]], "direction": dc.direction(probs),
                "verdict": r["verdict"], "probs": probs, "edges_pct": r["edges_pct"], "reasons": r["reasons"],
                "context": [], "model_version": r["model_version"], "source": r["source"], "outcome": r["outcome"]}

    def view(self, market: str, now: datetime | None = None) -> dict:
        """GET /api/day-call: the latest session's calls (local today or before), the last round of
        each call first, with its record."""
        now = now or self.clock()
        m = dc.MARKETS[market]
        today = now.astimezone(m.zone).date()
        rows = [r for r in self._rows(market, today - timedelta(days=10)) if r["trade_date"] <= today]
        day = rows[0]["trade_date"] if rows else None
        latest = [r for r in rows if r["trade_date"] == day]
        stats = self.stats(market, RECORD_DAYS, now)
        calls, rounds = {}, {}
        for name in (*SHOWN, dr.CALL):
            mine = sorted((r for r in latest if r["call"] == name), key=lambda r: r["round"])
            rounds[name] = [self.entry(r) for r in mine]
            if mine:
                calls[name] = {**rounds[name][-1], "record": self.record(market, name, mine[-1]["round"], stats)}
            else:
                calls[name] = None
        return {"market": market, "date": day.isoformat() if day else today.isoformat(), "today": day == today,
                "stages": [{"key": s, "label": dc.LABEL_KO[s]} for s in dc.DISPLAY_ORDER],
                "calls": calls, "rounds": rounds, "schedule": self.schedule(market, day or today, latest),
                "note": NOTE}

    def schedule(self, market: str, day: date, rows: Sequence[dict] = ()) -> dict:
        """#619 the intraday rounds of ``day``'s session: each one's time (aware ISO: the local time and
        the Korean one follow from it, New York's daylight saving included) and whether it was called."""
        done = {r["round"] for r in rows if r["call"] == dr.CALL}
        return {dr.CALL: [{"round": r, "round_label": dr.round_label(market, r),
                           "at": dr.cutoff_of(market, day, r).isoformat(), "called": r in done}
                          for r in dr.rounds_of(market, day)]}

    def record(self, market: str, name: str, round_: int, stats: dict | None = None) -> dict:
        """The call's record next to it, for that round: the live calls once there are LIVE_RECORD_MIN
        of them; before that the backfilled hold-out calls; with neither, the hold-out report."""
        stats = stats or self.stats(market)
        by_round = stats["calls"][name]["by_round"].get(str(round_)) or {}
        for basis, part in (("live", by_round.get("live") or {}), ("backtest", by_round.get("backfill") or {})):
            if part.get("n") and (basis == "backtest" or part["n"] >= LIVE_RECORD_MIN):
                return {"n": part["n"], "direction_hit_rate": part["direction_hit_rate"],
                        "always_long": part["always_long"], "always_short": part["always_short"],
                        "brier_skill": part["brier_skill"], "basis": basis}
        if name == dr.CALL:
            rep = ((self.round_reports.get(market) or {}).get("rounds", {}).get(str(round_), {}).get("holdout") or {})
        else:
            rep = ((self.reports.get(market) or {}).get("calls", {}).get(name, {}).get("rounds", {})
                   .get(str(round_), {}).get("holdout") or {})
        b = rep.get("baselines") or {}
        return {"n": rep.get("n", 0), "direction_hit_rate": rep.get("direction_hit_rate"),
                "always_long": (b.get("always_long") or {}).get("direction_hit_rate"),
                "always_short": (b.get("always_short") or {}).get("direction_hit_rate"),
                "brier_skill": rep.get("brier_skill"), "basis": "backtest"}

    def history(self, market: str, days: int = RECORD_DAYS, now: datetime | None = None) -> dict:
        now = now or self.clock()
        since = now.astimezone(dc.MARKETS[market].zone).date() - timedelta(days=days)
        return {"market": market, "days": days, "items": [self.entry(r) for r in self._rows(market, since)]}

    def stats(self, market: str, days: int = RECORD_DAYS, now: datetime | None = None) -> dict:
        """Hit rates, Brier, baselines, per stage, calibration, by round and source, per call."""
        now = now or self.clock()
        m = dc.MARKETS[market]
        today = now.astimezone(m.zone).date()
        rows = self._rows(market, today - timedelta(days=days))
        model_file = self.models.get(market) or {}
        round_model = (self.round_models.get(market) or {}).get("model") or {}
        out = {}
        for name in (*SHOWN, dr.CALL):
            intraday = name == dr.CALL
            mine = [r for r in rows if r["call"] == name]
            # unscorable rows (no answer from Yahoo) are left out of every number
            scored = sorted((r for r in mine if r["scored_at"] is not None and r["brier"] is not None),
                            key=lambda r: (r["trade_date"], r["round"]))
            keys = sorted(dr.ROUNDS[market]) if intraday else sorted(m.rounds)
            if intraday:
                clim = {str(k): round_model.get("climatology") for k in keys}
            else:
                clim = {k: (model_file.get("calls", {}).get(name, {}).get("rounds", {}).get(k) or {}).get("climatology")
                        for k in ("1", "2")}
            follow = "trend" if intraday else "yesterday"
            block = _summary(market, scored, clim, follow)
            block["missed"] = self._missed(market, mine, today, now, name)
            block["by_round"] = {}
            for k in keys:
                in_round = [r for r in scored if r["round"] == k]
                block["by_round"][str(k)] = {
                    **_summary(market, in_round, clim, follow),
                    "live_n": sum(1 for r in in_round if r["source"] == "live"),
                    **{s: _summary(market, [r for r in in_round if r["source"] == s], clim, follow)
                       for s in ("live", "backfill")},
                }
                if intraday:
                    block["by_round"][str(k)]["round_label"] = dr.round_label(market, k)
            block["by_source"] = {s: {"n": sum(1 for r in scored if r["source"] == s)} for s in ("live", "backfill")}
            if name == "open":
                n = len(scored)
                called = [r for r in scored if r["verdict"] != "hold"]
                block["hold_rate"] = round((n - len(called)) / n, 3) if n else None
                block["called"] = {"n": len(called), "direction_hit_rate": _rate(r["hit_direction"] for r in called)}
            out[name] = block
        report = self.reports.get(market) or {}
        round_report = self.round_reports.get(market) or {}
        return {"market": market, "days": days, "calls": out, "note": NOTE,
                "backtest": {"model_version": model_file.get("version"), "holdout_from": report.get("holdout_from"),
                             "stale": bool(report) and report.get("code") != _code()},
                "rounds_backtest": {"model_version": (self.round_models.get(market) or {}).get("version"),
                                    "holdout_from": round_report.get("holdout_from"),
                                    "stale": bool(round_report) and round_report.get("code") != _code(rounds=True)}}

    def _missed(self, market: str, rows: list[dict], today: date, now: datetime, name: str = "prev_close") -> int:
        """Live rounds whose window passed without a call, since the first live call."""
        live = [r for r in rows if r["source"] == "live"]
        if not live:
            return 0
        first = min(r["trade_date"] for r in live)
        have = {(r["trade_date"], r["round"]) for r in live}
        m = dc.MARKETS[market]
        missed, d = 0, first
        while d <= today:
            if market_calendar.is_open(m.calendar, d):
                for round_, (_start, end_at) in windows(market, d).items():
                    if (name == dr.CALL) != (round_ in dr.ROUNDS[market]):
                        continue
                    end = datetime.combine(d, end_at, m.zone)
                    if end <= now and (d, round_) not in have:
                        missed += 1
            d += timedelta(days=1)
        return missed

    def push_of(self, row: dict) -> tuple[str, str] | None:
        """The push (title, body) a freshly made row earns, None for most: each market's first call of the
        day (1차 prev_close), and (#619) an intraday round whose stage moved JUMP_PUSH or more from the
        session's round before it - the first intraday round is not pushed (the morning call was)."""
        where = "국장" if row["market"] == "kr" else "미장"
        if row["call"] == "prev_close" and row["round"] == 1:
            return f"오늘 {where} 방향", push_text(row)
        if row["call"] != dr.CALL:
            return None
        with self.engine.connect() as conn:
            before = conn.execute(
                select(t.c.round, t.c.stage).where(and_(t.c.market == row["market"], t.c.trade_date == row["trade_date"],
                                                        t.c.call == dr.CALL, t.c.round < row["round"]))
                .order_by(t.c.round.desc()).limit(1)).mappings().first()
        if before is None or dr.jump(before["stage"], row["stage"]) < JUMP_PUSH:
            return None
        was = f"{dr.round_label(row['market'], before['round'])} {dc.LABEL_KO[before['stage']]}"
        return (f"{where} 장중 방향 바뀜",
                f"{dr.round_label(row['market'], row['round'])} 마감까지 {call_words(row)} — {was}에서 바뀜")

    def today_call(self, now: datetime | None = None) -> dict | None:
        """#571's line: each market's latest prev_close call (the one with an edge)."""
        now = now or self.clock()
        kst_today = now.astimezone(dc.KST).date()
        out: dict = {"date": kst_today.isoformat()}
        text: dict[str, str] = {}
        for market in dc.MARKETS:
            rows = [r for r in self._rows(market, kst_today - timedelta(days=5))
                    if r["call"] == "prev_close" and r["trade_date"] <= kst_today]
            if not rows:
                out[market] = None
                continue
            r = rows[0]
            if market == "kr" and r["trade_date"] != kst_today:
                # the 07:00 brief comes before the 07:30 call: yesterday's call is not today's line
                out[market] = None
                text["kr_later"] = f"오늘 국장 판정은 {WINDOWS[('kr', 1)][0]:%H:%M} 에 나온다"
                continue
            out[market] = {"label": dc.LABEL_KO[r["stage"]], "stage": r["stage"], "probs": r["probs"],
                           "round": r["round"], "session_date": r["trade_date"].isoformat(), "verdict": r["verdict"]}
            if market == "kr":
                text["kr"] = f"오늘 국장은 전일 종가 대비 {call_words(r)}"
            else:
                result = (r["outcome"] or {}).get("label")
                text["us"] = f"간밤 미장 판정은 {call_words(r)}" + (f"(결과 {result})" if result else "")
        if all(out[m] is None for m in dc.MARKETS):
            return None
        out["line"] = ", ".join(text[k] for k in ("kr", "us", "kr_later") if k in text) + "."
        return out


def call_words(row: dict) -> str:
    """"약한 롱(34%) 우세": the top stage and its probability, and the side the long and short stages
    add up to when it is not the stage's own (a flat stage, or a near tie)."""
    stage, verdict = row["stage"], row["verdict"]
    head = f"{dc.LABEL_KO[stage]}({row['probs'][stage]:.0f}%)"
    if verdict == "hold":
        return f"{head} · 판단 보류"
    side = "long" if stage.endswith("long") else "short" if stage.endswith("short") else None
    if side == verdict:
        return f"{head} 우세"
    return f"{head}, 롱 · 숏 합으로는 {'롱' if verdict == 'long' else '숏'} 쪽"


def push_text(row: dict) -> str:
    """The push body of a stored prev_close call: the stage, its probability, and where the record is."""
    return (f"전일 종가 대비 {call_words(row)}"
            f"{' · 야간 선물 없이' if row['model'] == 'prev_close_plain' else ''} — 적중 기록 · 기준선은 앱에서")


def _summary(market: str, rows: list[dict], clim: dict[str, list[float] | None], follow_by: str = "yesterday") -> dict:
    """The numbers of scored rows. ``follow_by`` "trend" (#619's rounds): the follow baseline is the
    session so far (the sign of the move from the open to the round) instead of yesterday's answer."""
    n = len(rows)
    follow_key = "follow_trend" if follow_by == "trend" else "follow_yesterday"
    if n == 0:
        return {"n": 0, "direction_hit_rate": None, "stage_hit_rate": None, "brier": None, "brier_skill": None,
                "always_long": None, "always_short": None,
                "baselines": {"always_long": {"direction_hit_rate": None}, "always_short": {"direction_hit_rate": None},
                              follow_key: {"direction_hit_rate": None}, "climatology": {"brier": None}},
                "per_stage": [], "calibration": []}
    moved = [r for r in rows if r["outcome"]["close"] != r["outcome"]["open"]]
    ups = [r["outcome"]["close"] > r["outcome"]["open"] for r in moved]
    up = round(sum(ups) / len(ups), 3) if ups else None
    # follow yesterday: the same round's answer on the market's previous session, when it was scored
    moves = {(r["round"], r["trade_date"]): r["outcome"]["close"] - r["outcome"]["open"] for r in rows}
    follow = []
    for r in rows:
        actual = moves[(r["round"], r["trade_date"])]
        if follow_by == "trend":         # the move from the open to the round (desk.day_rounds i_move)
            before = (r["features"] or {}).get("i_move")
        else:
            before = moves.get((r["round"], dc.previous_session(market, r["trade_date"])))
        if before and actual:
            follow.append((before > 0) == (actual > 0))
    brier = sum(r["brier"] for r in rows) / n
    cb = []
    for r in rows:
        c = clim.get(str(r["round"]))
        if c:
            cb.append(sum((c[i] - (1.0 if s == r["outcome"]["stage"] else 0.0)) ** 2 for i, s in enumerate(dc.STAGES)))
    clim_brier = sum(cb) / len(cb) if cb else None
    calib = []
    pairs = [(r["probs"][s], 1.0 if s == r["outcome"]["stage"] else 0.0) for r in rows for s in dc.STAGES]
    for lo in range(0, 100, 10):
        hit = [(p, y) for p, y in pairs if lo <= p < lo + 10 or (lo == 90 and p == 100)]
        if hit:
            calib.append({"lo": lo, "hi": lo + 10, "n": len(hit), "mean_prob": round(sum(p for p, _ in hit) / len(hit), 1),
                          "actual_rate": round(sum(y for _, y in hit) / len(hit) * 100, 1)})
    return {
        "n": n, "direction_hit_rate": _rate(r["outcome"].get("raw_hit_direction", r["hit_direction"]) for r in rows),
        "stage_hit_rate": _rate(r["hit_stage"] for r in rows), "brier": round(brier, 4),
        "brier_skill": round(1 - brier / clim_brier, 4) if clim_brier else None,
        "always_long": up, "always_short": None if up is None else round(1 - up, 3),
        "baselines": {"always_long": {"direction_hit_rate": up},
                      "always_short": {"direction_hit_rate": None if up is None else round(1 - up, 3)},
                      follow_key: {"direction_hit_rate": _rate(follow)},
                      "climatology": {"brier": None if clim_brier is None else round(clim_brier, 4)}},
        "per_stage": [{"stage": s, "label": dc.LABEL_KO[s], "called": sum(1 for r in rows if r["stage"] == s),
                       "called_hit": sum(1 for r in rows if r["stage"] == s and r["hit_stage"]),
                       "actual": sum(1 for r in rows if r["outcome"]["stage"] == s)} for s in dc.DISPLAY_ORDER],
        "calibration": calib,
    }


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime) and v.tzinfo is None:
        v = v.replace(tzinfo=UTC)
    return v.isoformat()


def _load_report(market: str) -> dict | None:
    import json

    try:
        return json.loads((dc.DATA / f"report_{market}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _code(rounds: bool = False) -> str | None:
    try:
        if rounds:
            from .day_rounds_train import code_hash
        else:
            from .day_call_train import code_hash
    except ImportError:              # numpy missing: the stale flag is unknown
        return None
    return code_hash()


# the bound desk for desk.day_call.today_call (#571 reads it in-process)
DESK: DayCalls | None = None


def bind(desk: DayCalls | None) -> None:
    global DESK
    DESK = desk


def today() -> dict | None:
    if DESK is None:
        return None
    try:
        return DESK.today_call()
    except Exception:  # the morning brief stands without the line
        log.exception("today_call failed")
        return None
