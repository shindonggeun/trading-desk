"""SQLite store for runs and their event stream. The event table is the source of
truth the SSE endpoint replays from, so a reconnecting browser misses nothing."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    status       TEXT NOT NULL,          -- queued | running | done | failed
    options_json TEXT NOT NULL,
    plan_json    TEXT,
    error        TEXT,
    report_path  TEXT,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS events (
    run_id    TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    type      TEXT NOT NULL,              -- status | message | tool | plan | error | done
    agent     TEXT,
    content   TEXT,
    data_json TEXT,
    PRIMARY KEY (run_id, seq)
);
"""

TERMINAL = ("done", "failed")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- runs -----------------------------------------------------------------------

    def create_run(self, ticker: str, trade_date: str, options: dict) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._write_lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (id, ticker, trade_date, status, options_json, created_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?)",
                (run_id, ticker, trade_date, json.dumps(options), now()),
            )
        return run_id

    def mark_running(self, run_id: str) -> None:
        self._update(run_id, status="running", started_at=now())

    def mark_done(self, run_id: str, plan: dict, report_path: str | None) -> None:
        self._update(run_id, status="done", plan_json=json.dumps(plan), report_path=report_path,
                     finished_at=now())

    def mark_failed(self, run_id: str, error: str) -> None:
        self._update(run_id, status="failed", error=error, finished_at=now())

    def fail_unfinished(self, reason: str) -> int:
        """Runs a previous process left mid-way cannot continue; say so instead of spinning forever."""
        with self._write_lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE runs SET status='failed', error=?, finished_at=? WHERE status IN ('queued','running')",
                (reason, now()),
            )
            return cur.rowcount

    def _update(self, run_id: str, **fields: Any) -> None:
        columns = ", ".join(f"{key}=?" for key in fields)
        with self._write_lock, self._conn() as conn:
            conn.execute(f"UPDATE runs SET {columns} WHERE id=?", (*fields.values(), run_id))

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return _run(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_run(row) for row in rows]

    # --- events ---------------------------------------------------------------------

    def append_event(self, run_id: str, event: dict) -> dict:
        with self._write_lock, self._conn() as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            stored = {
                "run_id": run_id,
                "seq": seq,
                "ts": now(),
                "type": event["type"],
                "agent": event.get("agent"),
                "content": event.get("content"),
                "data": event.get("data"),
            }
            conn.execute(
                "INSERT INTO events (run_id, seq, ts, type, agent, content, data_json) VALUES (?,?,?,?,?,?,?)",
                (run_id, seq, stored["ts"], stored["type"], stored["agent"], stored["content"],
                 json.dumps(stored["data"]) if stored["data"] is not None else None),
            )
        return stored

    def events_after(self, run_id: str, after: int = 0) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after)
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "seq": row["seq"],
                "ts": row["ts"],
                "type": row["type"],
                "agent": row["agent"],
                "content": row["content"],
                "data": json.loads(row["data_json"]) if row["data_json"] else None,
            }
            for row in rows
        ]


def _run(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "trade_date": row["trade_date"],
        "status": row["status"],
        "options": json.loads(row["options_json"]),
        "plan": json.loads(row["plan_json"]) if row["plan_json"] else None,
        "error": row["error"],
        "report_path": row["report_path"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }
