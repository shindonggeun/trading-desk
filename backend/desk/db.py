"""Database schema and engine. SQLAlchemy Core, so the same code runs on SQLite
(local dev, tests) and PostgreSQL (the homelab). Alembic owns schema changes:
``migrate()`` brings any database to head and runs on every start."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url

MIGRATIONS = Path(__file__).parent / "migrations"

# Explicit constraint names keep Alembic diffs stable across SQLite and PostgreSQL.
metadata = MetaData(naming_convention={
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})
# none_as_null: a Python None is SQL NULL, not the JSON literal null.
Json = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
Timestamp = DateTime(timezone=True)

runs = Table(
    "runs", metadata,
    Column("id", String(32), primary_key=True),
    Column("ticker", String(20), nullable=False),
    Column("trade_date", String(10), nullable=False),
    Column("status", String(16), nullable=False),        # queued | running | done | failed
    Column("options", Json, nullable=False),
    Column("plan", Json),
    Column("error", Text),
    Column("report_path", Text),
    Column("created_at", Timestamp, nullable=False),
    Column("started_at", Timestamp),
    Column("finished_at", Timestamp),
    Index("ix_runs_created_at", "created_at"),
)

events = Table(
    "events", metadata,
    Column("run_id", String(32), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("ts", Timestamp, nullable=False),
    Column("type", String(16), nullable=False),          # status | message | tool | plan | error | done
    Column("agent", String(64)),
    Column("content", Text),
    Column("data", Json),
)

# One row in practice: accounts are made with `python -m desk.cli create-user`, never over HTTP.
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("created_at", Timestamp, nullable=False),
    Column("password_changed_at", Timestamp, nullable=False),
)

# The cookie holds a random token; only its SHA-256 is stored, so a leaked table logs no one in.
sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created_at", Timestamp, nullable=False),
    Column("last_seen_at", Timestamp, nullable=False),
    Column("expires_at", Timestamp, nullable=False),
    Column("ip", String(64)),
    Column("user_agent", String(256)),
)


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands timestamps back without a zone; everything stored is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    # pool_pre_ping: the homelab DB restarts now and then; a dead pooled connection should not fail a request.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def alembic_config(url: str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    # ConfigParser interpolation would eat a '%' in a password.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def migrate(url: str) -> None:
    from alembic import command

    command.upgrade(alembic_config(url), "head")
