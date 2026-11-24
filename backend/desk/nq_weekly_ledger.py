"""Durable, owner-scoped NQ weekly rule and notification outbox (#914)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine

from .db import account_owner_binding, nq_weekly_deliveries, nq_weekly_rules, users
from .nq_weekly_signal import DEFAULT_SETTINGS
from .privacy import ProcessPolicy

CHANNELS = ("telegram", "kakao")
WINDOW = timedelta(hours=24)
MAX_ATTEMPTS = 3
CLAIM_TIMEOUT = timedelta(minutes=5)
WAITING = ("pending", "rate_limited")


def _utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


@dataclass(frozen=True)
class Rule:
    scope: str
    owner: int
    revision: int
    settings: dict


class Ledger:
    def __init__(self, engine: Engine, policy: ProcessPolicy) -> None:
        self.engine = engine
        self.policy = policy

    def _owner(self, conn: Connection, *, lock: bool = False) -> tuple[str, int] | None:
        if lock:
            self.policy.lock(conn)
        epoch = self.policy.expected.epoch
        if self.policy.protected:
            query = select(account_owner_binding.c.user_id, account_owner_binding.c.generation).where(
                account_owner_binding.c.id == 1,
            )
            row = conn.execute(query.with_for_update() if lock else query).one_or_none()
            if row is None or row.user_id is None:
                return None
            return f"protected:{epoch}:{row.generation}:{row.user_id}", row.user_id
        query = select(users.c.id, users.c.created_at).order_by(users.c.id).limit(1)
        row = conn.execute(query.with_for_update() if lock else query).one_or_none()
        return (f"legacy:{epoch}:{row.id}:{_utc(row.created_at).isoformat()}", row.id) if row is not None else None

    @staticmethod
    def _row(conn: Connection, scope: str):
        return conn.execute(select(nq_weekly_rules).where(nq_weekly_rules.c.scope == scope)).mappings().one_or_none()

    @staticmethod
    def _rule(owner: tuple[str, int], row) -> Rule:
        return Rule(*owner, row["revision"], row["settings"]) if row else Rule(*owner, 0, dict(DEFAULT_SETTINGS))

    def rule(self) -> Rule | None:
        if not self.policy.current():
            return None
        with self.engine.connect() as conn:
            owner = self._owner(conn)
            return self._rule(owner, self._row(conn, owner[0])) if owner else None

    def save(self, owner_id: int, settings: dict, now: datetime) -> Rule | None:
        with self.engine.begin() as conn:
            owner = self._owner(conn, lock=True)
            if owner is None or owner[1] != owner_id:
                return None
            row = self._row(conn, owner[0])
            rule = self._rule(owner, row)
            if settings == rule.settings:
                return rule
            revision = rule.revision + 1
            values = {"revision": revision, "settings": settings, "changed_at": now,
                      "last_week": None, "last_match": None}
            if row:
                conn.execute(update(nq_weekly_rules).where(nq_weekly_rules.c.scope == owner[0]).values(**values))
                conn.execute(update(nq_weekly_deliveries).where(
                    nq_weekly_deliveries.c.scope == owner[0], nq_weekly_deliveries.c.status.in_(WAITING),
                ).values(status="superseded", updated_at=now, retry_at=None))
            else:
                conn.execute(insert(nq_weekly_rules).values(scope=owner[0], **values))
            return Rule(*owner, revision, settings)

    def record(self, rule: Rule, observation: dict, payload: dict, now: datetime) -> bool:
        status = observation["status"]
        if status not in ("matched", "not_matched"):
            return False
        trade_week = date.fromisoformat(observation["week"]["start"])
        confirmed = datetime.fromisoformat(observation["week"]["confirmed_at"])
        expires = confirmed + WINDOW
        with self.engine.begin() as conn:
            if self._owner(conn, lock=True) != (rule.scope, rule.owner):
                return False
            row = self._row(conn, rule.scope)
            if not row or row["revision"] != rule.revision or not row["settings"]["enabled"]:
                return False
            last_week = row["last_week"]
            if last_week is not None and trade_week <= last_week:
                return False
            matched = status == "matched"
            conn.execute(update(nq_weekly_rules).where(nq_weekly_rules.c.scope == rule.scope).values(
                last_week=trade_week, last_match=matched,
            ))
            fresh = _utc(row["changed_at"]) < confirmed <= now < expires
            if not (fresh and matched and row["last_match"] is False
                    and last_week is not None and trade_week - last_week == timedelta(weeks=1)):
                return False
            conn.execute(insert(nq_weekly_deliveries), [
                {"scope": rule.scope, "revision": rule.revision, "week": trade_week, "channel": channel,
                 "payload": payload, "status": "pending", "attempts": 0, "updated_at": now, "expires_at": expires}
                for channel in CHANNELS
            ])
            return True

    def _where(self, rule: Rule):
        return (nq_weekly_deliveries.c.scope == rule.scope, nq_weekly_deliveries.c.revision == rule.revision)

    def claim(self, rule: Rule, channel: str, now: datetime):
        table = nq_weekly_deliveries
        with self.engine.begin() as conn:
            if self._owner(conn, lock=True) != (rule.scope, rule.owner):
                return None
            current = self._row(conn, rule.scope)
            if not current or current["revision"] != rule.revision or not current["settings"]["enabled"]:
                return None
            where = (*self._where(rule), table.c.channel == channel)
            conn.execute(update(table).where(*where, table.c.status == "sending",
                                             table.c.updated_at <= now - CLAIM_TIMEOUT)
                         .values(status="unknown", updated_at=now))
            conn.execute(update(table).where(*where, table.c.status.in_(WAITING), table.c.expires_at <= now)
                         .values(status="expired", retry_at=None, updated_at=now))
            row = conn.execute(select(table).where(
                *where, table.c.status.in_(WAITING),
                table.c.retry_at.is_(None) | (table.c.retry_at <= now),
            ).order_by(table.c.week).limit(1)).mappings().one_or_none()
            if row is None:
                return None
            conn.execute(update(table).where(*where, table.c.week == row["week"]).values(
                status="sending", updated_at=now, attempts=row["attempts"] + 1, retry_at=None,
            ))
            return {**row, "expires_at": _utc(row["expires_at"])}

    def active(self, rule: Rule) -> bool:
        current = self.rule()
        return bool(current and current.scope == rule.scope and current.revision == rule.revision
                    and current.settings["enabled"])

    def locked_active(self, conn: Connection, rule: Rule) -> bool:
        if self._owner(conn, lock=True) != (rule.scope, rule.owner):
            return False
        current = self._row(conn, rule.scope)
        return bool(current and current["revision"] == rule.revision and current["settings"]["enabled"])

    def finish(self, rule: Rule, row: dict, status: str, now: datetime, retry_after: float = 0) -> None:
        retry_at = None
        if status == "rate_limited" and row["attempts"] + 1 < MAX_ATTEMPTS:
            next_attempt = now + timedelta(seconds=retry_after)
            if next_attempt < _utc(row["expires_at"]):
                retry_at = next_attempt
        if status == "rate_limited" and retry_at is None:
            status = "rate_limit_exhausted"
        table = nq_weekly_deliveries
        with self.engine.begin() as conn:
            if self._owner(conn, lock=True) == (rule.scope, rule.owner):
                conn.execute(update(table).where(*self._where(rule), table.c.week == row["week"],
                                                 table.c.channel == row["channel"], table.c.status == "sending")
                             .values(status=status, retry_at=retry_at, updated_at=now))

    def latest(self, rule: Rule) -> list[dict]:
        table = nq_weekly_deliveries
        with self.engine.connect() as conn:
            rows = conn.execute(select(table).where(*self._where(rule))
                                .order_by(table.c.week.desc(), table.c.channel).limit(len(CHANNELS))).mappings()
            return [{"week": row["week"].isoformat(), "channel": row["channel"], "status": row["status"],
                     "attempts": row["attempts"], "updated_at": _utc(row["updated_at"]).isoformat(),
                     "retry_at": _utc(row["retry_at"]).isoformat() if row["retry_at"] else None}
                    for row in rows]
