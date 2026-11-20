"""Foreign-flow turn alerts (#361): push each turn once.

Input is #359's ``flow_intraday.turns(points, market)`` rows:
``{t: ISO, market: "futures"|"kospi"|"kosdaq", who: "foreign", kind: "buy_turn"|"sell_turn", value}``.
A market pushes a direction once; a turn back the other way the same day pushes again. What was sent
is kept in ``flow_alerts_sent`` so judging the same snapshots again sends nothing twice.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from . import alert_tone, market_bus, notify
from .db import flow_alerts_sent, now

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

MARKETS = {"futures": "선물", "kospi": "코스피 현물", "kosdaq": "코스닥 현물"}
KINDS = {"buy_turn": "순매수 전향", "sell_turn": "순매도 전향"}
ALERT_URL = "/#/market"           # the page a tapped notification opens (the FE service worker reads ``url``)


def text(turn: dict) -> str:
    """e.g. "외인 선물 순매도 전향 · 10:32 · 누적 -620계약"."""
    market = turn["market"]
    hhmm = str(turn["t"])[11:16]
    parts = [f"외인 {MARKETS[market]} {KINDS[turn['kind']]}", hhmm]
    value = turn.get("value")
    if value is not None:
        unit = "계약" if market == "futures" else "억"
        parts.append(f"누적 {value:+,.0f}{unit}")
    return " · ".join(p for p in parts if p)


class FlowAlerts:
    def __init__(self, engine: Engine, pusher=None) -> None:
        self.engine = engine
        self.pusher = pusher

    def _last(self, conn, day: str, market: str):
        return conn.execute(select(flow_alerts_sent.c.kind, flow_alerts_sent.c.turned_at)
                            .where(flow_alerts_sent.c.trade_date == day, flow_alerts_sent.c.market == market)
                            .order_by(flow_alerts_sent.c.turned_at.desc(), flow_alerts_sent.c.id.desc())).first()

    def dispatch(self, turns: list[dict], day: str) -> list[dict]:
        """Record and push the turns not sent yet; the ones sent now."""
        out = []
        for turn in sorted(turns, key=lambda t: str(t["t"])):
            if turn.get("who", "foreign") != "foreign" or turn.get("market") not in MARKETS \
                    or turn.get("kind") not in KINDS:
                continue
            at = str(turn["t"])
            # #641 a buy turn is good news, a sell turn (buying turned to selling) a warning
            shape = {"kind": "flow_turn", "rule": turn["kind"], "who": "foreign", "severity": "caution"}
            tone = alert_tone.tone(shape)
            row = {"trade_date": day, "market": turn["market"], "kind": turn["kind"], "turned_at": at,
                   "value": turn.get("value"), "text": alert_tone.headline(shape, text(turn))}
            try:
                with self.engine.begin() as conn:
                    last = self._last(conn, day, turn["market"])
                    if last and (last.kind == turn["kind"] or at <= last.turned_at):
                        continue              # the same direction again, or a turn before the last one sent
                    conn.execute(insert(flow_alerts_sent).values(created_at=now(), **row))
            except IntegrityError:            # another pass recorded it first (the transaction rolled back)
                continue
            out.append({**row, "tone": tone})
            # #690 the open market streams: the same line as /api/alerts/today shows for it
            market_bus.publish("alert", "flow_turn", {
                "trade_date": day, "kind": "flow_turn", "rule": turn["kind"], "market": turn["market"], "who": "foreign",
                "severity": "caution", "t": at, "value": turn.get("value"), "prev": None, "text": row["text"],
                "pushed": True, "tone": tone}, "desk", at)
            notify.send(self.pusher, "flow", f"flow_{turn['market']}", f"tone_{tone}", payload={   # #579 the one exit
                "title": "외국인 수급 전향", "body": row["text"], "tag": f"flow-{turn['market']}",
                "url": ALERT_URL, "market": turn["market"], "kind": turn["kind"], "tone": tone})
        return out

    def after_snapshot(self, intraday, day: date | None = None) -> list[dict]:
        """#359's snapshot loop calls this after each record: today's points (``intraday(day)``),
        each market's turns (``flow_intraday.turns``), the ones not sent yet pushed."""
        from . import flow_intraday

        day = day or datetime.now(KST).date()
        points = intraday(day)
        turns = [t for market in MARKETS for t in flow_intraday.turns(points, market)]
        return self.dispatch(turns, day.isoformat())

    def list(self, day: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(flow_alerts_sent).where(flow_alerts_sent.c.trade_date == day)
                                .order_by(flow_alerts_sent.c.turned_at)).mappings()
            return [{"trade_date": r["trade_date"], "market": r["market"], "kind": r["kind"],
                     "t": r["turned_at"], "value": r["value"], "text": r["text"]} for r in rows]
