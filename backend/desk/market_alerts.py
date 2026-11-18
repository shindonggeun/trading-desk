"""The day's market alerts (#579): investor flow shifts (desk.flow_shift) and NQ=F alarms (desk.nq_watch),
kept in ``market_alerts`` and pushed through desk.notify.

What gets through (``admit``, read against what the day already holds):
- the same moment of the same rule is kept once (judging the same snapshots again sends nothing twice);
- ``ONCE_A_DAY`` rules once a day per market and investor;
- ``COOLDOWN_MIN`` between two alerts of a kind on the same market, whoever the investor (the futures'
  foreign and institution mostly take each other's other side: one alert, not a mirrored pair), and
  ``REPEAT_MIN`` between two of the same rule and investor (the same direction again is not news) - a turn skips both: the dead band
  and hold of desk.flow_intraday already keep it rare, and it matters most;
- ``DAILY_MAX`` a day per kind; past it only strong ones, ``STRONG_EXTRA`` more.
Info alerts go on the timeline (``GET /api/alerts/today``) but are not pushed.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from . import flow_shift, notify, nq_watch
from .db import market_alerts, now
from .futures_flow import KST

log = logging.getLogger(__name__)

COOLDOWN_MIN = 20
REPEAT_MIN = 60
DAILY_MAX = 10
STRONG_EXTRA = 3
ONCE_A_DAY = {"open_buy_reversal", "open_sell_reversal", "nq_from_high"}
NO_COOLDOWN = {"buy_turn", "sell_turn"}
PUSHED = {"caution", "strong"}
HISTORY_DAYS = 20                 # trading days of snapshots behind the flow thresholds
ALERT_URL = "/#/market"
TITLE = {"flow_shift": "수급 흐름 변화", "nq_alert": "나스닥 선물 이상 징후"}


def admit(alert: dict, day: list[dict]) -> bool:
    """Whether ``alert`` goes through, given the day's alerts so far (any order)."""
    market = [a for a in day if a["kind"] == alert["kind"] and a["market"] == alert["market"]]
    same = [a for a in market if a["who"] == alert["who"]]
    if any(a["rule"] == alert["rule"] and a["t"] == alert["t"] for a in same):
        return False
    if alert["rule"] in ONCE_A_DAY and any(a["rule"] == alert["rule"] for a in same):
        return False
    if alert["rule"] not in NO_COOLDOWN:
        at = datetime.fromisoformat(alert["t"])
        for a in market:
            if a["rule"] in NO_COOLDOWN:
                continue
            repeat = a["rule"] == alert["rule"] and a["who"] == alert["who"]
            gap = timedelta(minutes=REPEAT_MIN if repeat else COOLDOWN_MIN)
            if abs(at - datetime.fromisoformat(a["t"])) < gap:
                return False
    count = sum(1 for a in day if a["kind"] == alert["kind"])
    if count < DAILY_MAX:
        return True
    return alert["severity"] == "strong" and count < DAILY_MAX + STRONG_EXTRA


def text(alert: dict) -> str:
    return nq_watch.text(alert) if alert["kind"] == "nq_alert" else flow_shift.text(alert)


class MarketAlerts:
    def __init__(self, engine: Engine, pusher=None) -> None:
        self.engine = engine
        self.pusher = pusher
        self._limits: tuple[date, dict] | None = None

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
        for alert in sorted(alerts, key=lambda a: a["t"]):
            day = datetime.fromisoformat(alert["t"]).astimezone(KST).date().isoformat()
            row = {**alert, "trade_date": day, "text": text(alert), "pushed": alert["severity"] in PUSHED}
            try:
                with self.engine.begin() as conn:
                    if not admit(alert, self.list_in(conn, day)):
                        continue
                    conn.execute(insert(market_alerts).values(
                        trade_date=day, kind=alert["kind"], rule=alert["rule"], market=alert["market"],
                        who=alert["who"], severity=alert["severity"], at=alert["t"], value=alert.get("value"),
                        prev=alert.get("prev"), text=row["text"], pushed=row["pushed"], created_at=now()))
            except IntegrityError:            # another pass recorded it first (the transaction rolled back)
                continue
            out.append(row)
            if row["pushed"]:
                notify.send(self.pusher, alert["kind"], payload={
                    "title": TITLE[alert["kind"]], "body": row["text"], "tag": f"{alert['kind']}-{alert['market']}",
                    "url": ALERT_URL, "kind": alert["kind"], "rule": alert["rule"], "market": alert["market"],
                    "severity": alert["severity"]})
        return out

    @staticmethod
    def list_in(conn, day: str) -> list[dict]:
        rows = conn.execute(select(market_alerts.c.kind, market_alerts.c.rule, market_alerts.c.market,
                                   market_alerts.c.who, market_alerts.c.severity, market_alerts.c.at)
                            .where(market_alerts.c.trade_date == day)).mappings()
        return [{**r, "t": r["at"]} for r in rows]

    def limits(self, history, day: date) -> dict:
        """desk.flow_shift.thresholds over the HISTORY_DAYS trading days before ``day`` - once a day."""
        if self._limits and self._limits[0] == day:
            return self._limits[1]
        days = [d["date"] for d in history.daily(HISTORY_DAYS + 1) if d.get("date") and d["date"] < day.isoformat()]
        got = flow_shift.thresholds([history.intraday(date.fromisoformat(d)) for d in days[:HISTORY_DAYS]])
        self._limits = (day, got)
        return got

    def after_snapshot(self, history, day: date | None = None) -> list[dict]:
        """The snapshot loop calls this after each record (desk.futures_history ``history``)."""
        day = day or datetime.now(KST).date()
        points = history.intraday(day)
        if not points:
            return []
        return self.dispatch(flow_shift.judge(points, self.limits(history, day)))

    def nq_pass(self, bars: list[dict], now: datetime | None = None) -> list[dict]:
        """The NQ=F loop calls this with Yahoo's 5-minute bars."""
        return self.dispatch(nq_watch.judge(bars, now))
