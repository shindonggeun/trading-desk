"""The day's market alerts (#579): investor flow shifts (desk.flow_shift), flow warnings (desk.flow_warn,
#699), program trading turns (desk.program_flow, #743) and NQ=F alarms (desk.nq_watch), kept in
``market_alerts`` and pushed through desk.notify.

What gets through (``admit``, read against what the day already holds):
- the same moment of the same rule is kept once (judging the same snapshots again sends nothing twice);
- ``ONCE_A_DAY`` rules once a day per market and investor - NQ=F's once per CME session (18:00 ET to
  17:00 ET runs over the KST midnight, in New York's regular hours), the KST day before included;
- ``REPEAT_MIN`` between two of the same rule and investor (the same direction again is not news);
- ``COOLDOWN_MIN`` between two pushed alerts of a kind on the same market, whoever the investor (the
  futures' foreign and institution mostly take each other's other side: one alert, not a mirrored pair);
- ``DAILY_MAX`` pushed alerts a day per kind, past it only strong ones, turns and warnings (#641),
  ``STRONG_EXTRA`` more.
A turn skips the two waits: the dead band and hold of desk.flow_intraday already keep it rare, and it
matters most. #743 a program turn also goes at most ``PROGRAM_MAX`` times a day per market, class and rule. A flow warning (``flow_warn``) skips them too and goes once a day per rule and market, plus
once more when it turns strong after a caution - the structure of the day, not a change of pace. Info alerts go on the timeline (``GET /api/alerts/today``) but are not pushed: they have
their own DAILY_MAX, and they never hold back a pushed one (nor the other way round). Alerts of the
same moment are judged strongest first.

#641 each alert also has a ``tone`` (desk.alert_tone: positive · neutral · caution · warning, by
direction - the severity is the size): its text starts with the tone's mark and ends with one line of
reading, and it is pushed under its kind and ``tone_<tone>``.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import (
    alert_tone,
    auth,
    flow_shift,
    flow_warn,
    holding_watch,
    market_bus,
    notify,
    nq_watch,
    program_flow,
    sector_movers,
)
from .db import market_alerts, now
from .futures_flow import KST

log = logging.getLogger(__name__)

COOLDOWN_MIN = 20
REPEAT_MIN = 60
DAILY_MAX = 10
STRONG_EXTRA = 3
PROGRAM_MAX = 3                   # #743 program turns a day per market, class and rule
ONCE_A_DAY = {"open_buy_reversal", "open_sell_reversal", "nq_from_high",
              # #694 each holding rule once a KST day per holding (market = the ticker)
              "hold_stop", "hold_near_stop", "hold_trend_down", "hold_take_profit",
              # #680 a sector's big day once a session per direction
              "sector_drop", "sector_jump"}
NO_COOLDOWN = {"buy_turn", "sell_turn", "hold_stop"}      # a stop is said at once, past the cooldown
UNCAPPED = {"hold_stop"}                          # #694 and past the day's cap: other holdings' cautions never hold it
CROSS_MIDNIGHT = {"nq_alert", "holding_alert", "sector_mover"}    # the waits read the KST day before too (a US session spans it)
PUSHED = {"caution", "strong"}
HISTORY_DAYS = 20                 # trading days of snapshots behind the flow thresholds
ALERT_URL = "/#/market"
URLS = {"holding_alert": "/#/",                  # #694 the dashboard's holdings
        "sector_mover": "/#/sectors?movers"}     # #740 the sectors page, scrolled to its movers card
# #690 an alert as GET /api/alerts/today lists it (and the market stream pushes it)
STREAM_FIELDS = ("trade_date", "kind", "rule", "market", "who", "severity", "t", "value", "prev", "text", "pushed", "tone")
OWNER_ONLY = {"holding_alert"}                   # #694 the owner's holdings: web push to the owner's devices only
TITLE = {"flow_shift": "수급 흐름 변화", "flow_warn": "수급 경고", "nq_alert": "나스닥 선물 이상 징후",
         "holding_alert": "보유 종목 손절 · 익절", "sector_mover": "섹터 급등락 주범",
         "program_alert": "프로그램매매 전환"}


def _session(alert: dict) -> str:
    return nq_watch.session_of(datetime.fromisoformat(alert["t"])).isoformat()


def admit(alert: dict, day: list[dict], before: list[dict] = ()) -> bool:
    """Whether ``alert`` goes through, given the day's alerts so far (any order) and, for NQ=F's
    once-a-session rule, the KST day before's."""
    # #674 · #694 NQ=F's and a holding's waits look across the KST midnight too (10:00 ET in winter, and a
    # US session runs over it): the 1-minute alarm at 23:58 and Yahoo's late one at 00:00 are the same move
    pool = [*day, *before] if alert["kind"] in CROSS_MIDNIGHT else day
    market = [a for a in pool if a["kind"] == alert["kind"] and a["market"] == alert["market"]]
    same = [a for a in market if a["who"] == alert["who"]]
    if any(a["rule"] == alert["rule"] and a["t"] == alert["t"] for a in same):
        return False
    if alert["kind"] == "program_alert" and sum(1 for a in same if a["rule"] == alert["rule"]) >= PROGRAM_MAX:
        return False
    if alert["kind"] == "flow_warn":                  # #699 once a day, once more when it turns strong
        earlier = [a for a in market if a["rule"] == alert["rule"]]
        if earlier and (alert["severity"] != "strong" or len(earlier) > 1
                        or any(a["severity"] == "strong" for a in earlier)):
            return False
        count = sum(1 for a in day if a["kind"] == alert["kind"])
        return count < DAILY_MAX + STRONG_EXTRA
    if alert["rule"] in ONCE_A_DAY:
        if alert["kind"] == "nq_alert":
            earlier = [a for a in [*day, *before] if a["kind"] == "nq_alert" and a["rule"] == alert["rule"]]
            if any(_session(a) == _session(alert) for a in earlier):
                return False
        elif alert["kind"] == "holding_alert":     # #694 once a day of the holding's own market
            earlier = [a for a in same if a["rule"] == alert["rule"]]
            if any(holding_watch.session_day(a) == holding_watch.session_day(alert) for a in earlier):
                return False
        elif alert["kind"] == "sector_mover":      # #680 once a session of the sector's own market
            earlier = [a for a in same if a["rule"] == alert["rule"]]
            if any(sector_movers.session_day(a) == sector_movers.session_day(alert) for a in earlier):
                return False
        elif any(a["rule"] == alert["rule"] for a in same):
            return False
    if alert["rule"] in UNCAPPED:
        return True                              # #694 a stop is said whatever else the day held
    pushed = alert["severity"] in PUSHED
    turn = alert["rule"] in NO_COOLDOWN
    if not turn:
        at = datetime.fromisoformat(alert["t"])
        for a in market:
            if a["rule"] in NO_COOLDOWN:
                continue
            if a["rule"] == alert["rule"] and a["who"] == alert["who"]:
                gap = REPEAT_MIN
            elif pushed and a["severity"] in PUSHED:
                gap = COOLDOWN_MIN
            else:
                continue
            if abs(at - datetime.fromisoformat(a["t"])) < timedelta(minutes=gap):
                return False
    count = sum(1 for a in day if a["kind"] == alert["kind"] and (a["severity"] in PUSHED) == pushed)
    if count < DAILY_MAX:
        return True
    urgent = alert["severity"] == "strong" or turn or alert_tone.tone(alert) == "warning"
    return pushed and urgent and count < DAILY_MAX + STRONG_EXTRA


def _tag(alert: dict) -> str:
    """The push's replace-me tag: a warning (#699) or a holding's alert (#694) of another rule must not hide
    the one before it."""
    if alert["kind"] == "flow_warn":
        return f"flow_warn-{alert['rule']}-{alert['market']}"
    if alert["kind"] in OWNER_ONLY:
        return f"{alert['kind']}-{alert['market']}-{alert['rule']}"
    return f"{alert['kind']}-{alert['market']}"


def text(alert: dict) -> str:
    """The stored and pushed text: the tone's mark, the rule's words, one line of reading (#641)."""
    words = {"nq_alert": nq_watch.text, "flow_warn": flow_warn.text,
             "holding_alert": holding_watch.text, "sector_mover": sector_movers.text,
             "program_alert": program_flow.text}.get(alert["kind"], flow_shift.text)(alert)
    return alert_tone.headline(alert, words)


class MarketAlerts:
    def __init__(self, engine: Engine, pusher=None) -> None:
        self.engine = engine
        self.pusher = pusher
        self._limits: tuple[date, dict] | None = None
        self._warn_history: tuple[date, dict] | None = None
        self._lock = threading.Lock()   # #674 two loops dispatch NQ=F alarms: admit and insert one at a time

    def list(self, day: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(market_alerts).where(market_alerts.c.trade_date == day)
                                .order_by(market_alerts.c.at, market_alerts.c.id)).mappings().all()
        return [{"trade_date": r["trade_date"], "kind": r["kind"], "rule": r["rule"], "market": r["market"],
                 "who": r["who"], "severity": r["severity"], "t": r["at"], "value": r["value"],
                 "prev": r["prev"], "text": r["text"], "pushed": r["pushed"]} for r in rows]

    def dispatch(self, alerts: list[dict]) -> list[dict]:
        """Record (and push the caution / strong ones of) the alerts that get through; those."""
        out = []
        rank = {"strong": 0, "caution": 1, "info": 2}
        for alert in sorted(alerts, key=lambda a: (datetime.fromisoformat(a["t"]), rank[a["severity"]])):
            day = datetime.fromisoformat(alert["t"]).astimezone(KST).date().isoformat()
            row = {**alert, "trade_date": day, "text": text(alert), "pushed": alert["severity"] in PUSHED,
                   "tone": alert_tone.tone(alert)}
            try:
                with self._lock, self.engine.begin() as conn:
                    before = (self.list_in(conn, (date.fromisoformat(day) - timedelta(days=1)).isoformat())
                              if alert["kind"] in CROSS_MIDNIGHT else [])
                    if not admit(alert, self.list_in(conn, day), before):
                        continue
                    conn.execute(insert(market_alerts).values(
                        trade_date=day, kind=alert["kind"], rule=alert["rule"], market=alert["market"],
                        who=alert["who"], severity=alert["severity"], at=alert["t"], value=alert.get("value"),
                        prev=alert.get("prev"), text=row["text"], pushed=row["pushed"], created_at=now()))
            except IntegrityError:            # another pass recorded it first (the transaction rolled back)
                continue
            except SQLAlchemyError as exc:    # #743 one alert the database refuses never costs the others
                log.warning("market alert %s/%s not recorded: %s", alert["kind"], alert["rule"], type(exc).__name__)
                continue
            out.append(row)
            # #690 the open market streams (a holding's alert only to the owner's): the timeline's shape
            market_bus.publish("alert", alert["kind"], {k: row.get(k) for k in STREAM_FIELDS}, "desk", alert["t"])
            if row["pushed"]:
                owner = self.owner() if alert["kind"] in OWNER_ONLY else None
                notify.send(self.pusher, alert["kind"], f"tone_{row['tone']}", user_id=owner, payload={
                    "title": TITLE[alert["kind"]], "body": row["text"], "tag": _tag(alert),
                    "url": URLS.get(alert["kind"], ALERT_URL), "kind": alert["kind"], "rule": alert["rule"], "market": alert["market"],
                    "severity": alert["severity"], "tone": row["tone"]})
        return out

    def owner(self) -> int | None:
        """The desk's owner: the first account (desk.auth.owner_id, #775 - one rule)."""
        return auth.owner_id(self.engine)

    @staticmethod
    def list_in(conn, day: str) -> list[dict]:
        rows = conn.execute(select(market_alerts.c.kind, market_alerts.c.rule, market_alerts.c.market,
                                   market_alerts.c.who, market_alerts.c.severity, market_alerts.c.at)
                            .where(market_alerts.c.trade_date == day)).mappings()
        return [{**r, "t": r["at"]} for r in rows]

    def limits(self, history, day: date) -> dict:
        """desk.flow_shift.thresholds over the HISTORY_DAYS trading days before ``day`` - once a day."""
        if not (self._limits and self._limits[0] == day):
            self._load(history, day)
        return self._limits[1]

    def warn_history(self, history, day: date) -> dict:
        """#699 desk.flow_warn.history over the same days - once a day."""
        if not (self._warn_history and self._warn_history[0] == day):
            self._load(history, day)
        return self._warn_history[1]

    def _load(self, history, day: date) -> None:
        """The HISTORY_DAYS trading days before ``day`` read once for both."""
        days = [d["date"] for d in history.daily(HISTORY_DAYS + 1) if d.get("date") and d["date"] < day.isoformat()]
        points = [history.intraday(date.fromisoformat(d)) for d in days[:HISTORY_DAYS]]
        self._limits = (day, flow_shift.thresholds(points))
        try:
            got = flow_warn.history(points)
        except Exception:        # the warnings then run on their floors - not read again every minute
            log.exception("flow warn history failed - floors only today")
            got = {"n": 0, "at": {}}
        self._warn_history = (day, got)
        program_flow.bands_for(history, day, points)     # #743 the same days, once (the chart reads it too)

    def program_bands(self, history, day: date) -> dict:
        """#743 desk.program_flow.bands_for - once a day, on the days read above."""
        if not program_flow.cached(day):
            self._load(history, day)
        return program_flow.bands_for(history, day)

    def after_snapshot(self, history, day: date | None = None) -> list[dict]:
        """The snapshot loop calls this after each record (desk.futures_history ``history``)."""
        day = day or datetime.now(KST).date()
        points = history.intraday(day)
        if not points:
            return []
        alerts = flow_shift.judge(points, self.limits(history, day))
        try:                     # #699 a fault in the warnings never costs the shifts
            alerts += flow_warn.judge(points, self.warn_history(history, day))
        except Exception:
            log.exception("flow warn judge failed")
        try:                     # #743 nor in the program turns
            alerts += program_flow.judge(points, self.program_bands(history, day)["band"])
        except Exception:
            log.exception("program turn judge failed")
        return self.dispatch(alerts)

    def nq_pass(self, bars: list[dict], now: datetime | None = None) -> list[dict]:
        """The NQ=F loop calls this with Yahoo's 5-minute bars: since #674 the late check - a move the
        1-minute pass (desk.nq_fast) already sent is held back by the waits above (same rules)."""
        now = now or datetime.now(UTC)
        got = nq_watch.judge(bars, now)
        for a in got:
            a.update(source="yahoo", delay_sec=max(0, round((now - datetime.fromisoformat(a["t"])).total_seconds())))
        return self.dispatch(got)
