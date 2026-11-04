"""runs, events, users, sessions

Revision ID: 0001
Revises:
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

Json = sa.JSON().with_variant(JSONB(), "postgresql")
Timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("trade_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("options", Json, nullable=False),
        sa.Column("plan", Json),
        sa.Column("error", sa.Text),
        sa.Column("report_path", sa.Text),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("started_at", Timestamp),
        sa.Column("finished_at", Timestamp),
        sa.PrimaryKeyConstraint("id", name="pk_runs"),
    )
    op.create_index("ix_runs_created_at", "runs", ["created_at"])

    op.create_table(
        "events",
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False, autoincrement=False),
        sa.Column("ts", Timestamp, nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("agent", sa.String(64)),
        sa.Column("content", sa.Text),
        sa.Column("data", Json),
        sa.PrimaryKeyConstraint("run_id", "seq", name="pk_events"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_events_run_id_runs", ondelete="CASCADE"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("password_changed_at", Timestamp, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("created_at", Timestamp, nullable=False),
        sa.Column("last_seen_at", Timestamp, nullable=False),
        sa.Column("expires_at", Timestamp, nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(256)),
        sa.PrimaryKeyConstraint("token_hash", name="pk_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("events")
    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_table("runs")
