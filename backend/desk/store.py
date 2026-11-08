"""Runs and their event stream. The events table is the source of truth the SSE
endpoint replays from, so a reconnecting browser misses nothing.

All writes happen on the event loop thread (workers hand events over with
``call_soon_threadsafe``), so ``seq`` = max + 1 has a single writer per run; the
primary key on (run_id, seq) catches it if that ever stops being true."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Engine, RowMapping

from .db import aware, events, now, runs

TERMINAL = ("done", "failed")


def iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat(timespec="seconds") if value else None


class Store:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # --- runs -----------------------------------------------------------------------

    def create_run(self, ticker: str, trade_date: str, options: dict) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self.engine.begin() as conn:
            conn.execute(runs.insert().values(
                id=run_id, ticker=ticker, trade_date=trade_date, status="queued",
                options=options, created_at=now(),
            ))
        return run_id

    def mark_running(self, run_id: str) -> None:
        self._update(run_id, status="running", started_at=now())

    def mark_done(self, run_id: str, plan: dict, report_path: str | None) -> None:
        self._update(run_id, status="done", plan=plan, report_path=report_path, finished_at=now())

    def mark_failed(self, run_id: str, error: str) -> None:
        self._update(run_id, status="failed", error=error, finished_at=now())

    def fail_unfinished(self, reason: str) -> int:
        """Runs a previous process left mid-way cannot continue; say so instead of spinning forever."""
        with self.engine.begin() as conn:
            result = conn.execute(
                update(runs).where(runs.c.status.in_(("queued", "running")))
                .values(status="failed", error=reason, finished_at=now())
            )
            return result.rowcount

    def purge_finished_before(self, cutoff: datetime) -> int:
        """Delete done/failed runs created before ``cutoff`` with their events. Queued and
        running runs stay whatever their age. Events are deleted explicitly rather than
        left to the FK cascade, so a SQLite connection without ``foreign_keys=ON`` cannot
        leave orphans behind."""
        old = select(runs.c.id).where(runs.c.created_at < cutoff, runs.c.status.in_(TERMINAL))
        with self.engine.begin() as conn:
            conn.execute(delete(events).where(events.c.run_id.in_(old)))
            return conn.execute(delete(runs).where(runs.c.id.in_(old))).rowcount

    def _update(self, run_id: str, **fields: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.id == run_id).values(**fields))

    def get_run(self, run_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        return _run(row) if row else None

    def run_status(self, run_id: str) -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(select(runs.c.status).where(runs.c.id == run_id)).scalar()

    def delete_run(self, run_id: str) -> str:
        """Delete one finished run with its events. Returns ``deleted``, ``missing`` or
        ``active`` (queued/running stay: the worker still writes to them). The status
        condition sits on both deletes, so a run cannot slip from active to gone between
        a check and the delete; a finished run never becomes active again. Should a run
        finish between the two statements (PostgreSQL READ COMMITTED), its events go with
        the FK's ON DELETE CASCADE instead. Call it from the event loop thread: RunManager
        writes its last events right after mark_done() without yielding."""
        finished = select(runs.c.id).where(runs.c.id == run_id, runs.c.status.in_(TERMINAL))
        with self.engine.begin() as conn:
            conn.execute(delete(events).where(events.c.run_id.in_(finished)))
            if conn.execute(delete(runs).where(runs.c.id.in_(finished))).rowcount:
                return "deleted"
            exists = conn.execute(select(runs.c.id).where(runs.c.id == run_id)).first()
        return "active" if exists else "missing"

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(runs).order_by(runs.c.created_at.desc(), runs.c.id).limit(limit).offset(offset)
            ).mappings().all()
        return [_run(row) for row in rows]

    def done_runs_for(self, tickers: list[str], since: str) -> list[dict]:
        """Finished runs of these tickers with trade_date >= since (YYYY-MM-DD), newest first —
        the plans the trade review compares executions against."""
        if not tickers:
            return []
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(runs).where(runs.c.status == "done", runs.c.ticker.in_(tickers),
                                   runs.c.trade_date >= since)
                .order_by(runs.c.trade_date.desc(), runs.c.created_at.desc())
            ).mappings().all()
        return [_run(row) for row in rows]

    def runs_to_score(self, limit: int) -> list[dict]:
        """Finished directional-or-not runs without a final outcome yet (desk.outcome.RETRY),
        newest first."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(runs).where(runs.c.status == "done", runs.c.plan.is_not(None),
                                   runs.c.outcome_status.is_(None)
                                   | runs.c.outcome_status.in_(("waiting", "open", "error")))
                .order_by(runs.c.scored_at.asc().nulls_first(), runs.c.created_at.desc()).limit(limit)
            ).mappings().all()
        return [_run(row) for row in rows]

    def set_outcome(self, run_id: str, outcome: dict) -> None:
        # the sweep's key: a pending playbook (#116) keeps a run whose own plan is settled in the sweep
        self._update(run_id, outcome=outcome, outcome_status=outcome.get("sweep_status") or outcome.get("status"),
                     scored_at=now())

    def scored_since(self, since: datetime) -> list[dict]:
        """Finished runs created since ``since`` with their outcomes, for the statistics."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(runs).where(runs.c.status == "done", runs.c.created_at >= since)
                .order_by(runs.c.created_at.desc())
            ).mappings().all()
        return [_run(row) for row in rows]

    def clear_outcomes(self, since: datetime | None = None) -> int:
        """Forget outcomes (rules changed) so the next sweep scores them again."""
        stmt = update(runs).values(outcome=None, outcome_status=None, scored_at=None)
        if since is not None:
            stmt = stmt.where(runs.c.created_at >= since)
        with self.engine.begin() as conn:
            return conn.execute(stmt).rowcount

    def count_runs(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(runs)).scalar_one()

    # --- events ---------------------------------------------------------------------

    def append_event(self, run_id: str, event: dict) -> dict:
        with self.engine.begin() as conn:
            seq = conn.execute(
                select(func.coalesce(func.max(events.c.seq), 0) + 1).where(events.c.run_id == run_id)
            ).scalar_one()
            stored = {
                "run_id": run_id,
                "seq": seq,
                "ts": now(),
                "type": event["type"],
                "agent": event.get("agent"),
                "content": event.get("content"),
                "data": event.get("data"),
            }
            conn.execute(events.insert().values(**stored))
        return {**stored, "ts": iso(stored["ts"])}

    def events_after(self, run_id: str, after: int = 0) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(events).where(events.c.run_id == run_id, events.c.seq > after).order_by(events.c.seq)
            ).mappings().all()
        return [{**row, "ts": iso(row["ts"])} for row in rows]

    def recent_reports(self, ticker: str, trade_date: str, agents: list[str], since: datetime) -> list[dict]:
        """#171: these agents' messages in finished runs of this ticker and day, filed since ``since``,
        newest first, each with its run's options (horizon, language) for the caller to match."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(events.c.run_id, events.c.ts, events.c.agent, events.c.content, events.c.data,
                       runs.c.options)
                .select_from(events.join(runs, events.c.run_id == runs.c.id))
                .where(runs.c.ticker == ticker, runs.c.trade_date == trade_date, runs.c.status == "done",
                       events.c.type == "message", events.c.agent.in_(agents), events.c.ts >= since)
                .order_by(events.c.ts.desc())
            ).mappings().all()
        return [{**row, "ts": aware(row["ts"])} for row in rows]


def _run(row: RowMapping) -> dict:
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "trade_date": row["trade_date"],
        "status": row["status"],
        "options": row["options"],
        "plan": row["plan"],
        "error": row["error"],
        "report_path": row["report_path"],
        "created_at": iso(row["created_at"]),
        "started_at": iso(row["started_at"]),
        "finished_at": iso(row["finished_at"]),
        "outcome": row["outcome"],
        # the plan's own status for the page; the column is the sweep's key (a pending playbook
        # keeps a settled plan in the sweep, #116)
        "outcome_status": (row["outcome"] or {}).get("status") or row["outcome_status"],
        "scored_at": iso(row["scored_at"]),
    }
