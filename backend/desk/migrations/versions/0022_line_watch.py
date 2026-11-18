"""line_watch · line_signals: the channel / trendline signal watch and the signals sent (#613)

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "line_watch",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("body", JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_line_watch"),
    )
    op.create_table(
        "line_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(length=10), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("event", sa.String(length=8), nullable=False),
        sa.Column("rule", sa.String(length=24), nullable=False),
        sa.Column("line_id", sa.String(length=160), nullable=False),
        sa.Column("at", sa.String(length=32), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("line_price", sa.Float(), nullable=False),
        sa.Column("text", sa.String(length=600), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_line_signals"),
        sa.UniqueConstraint("ticker", "timeframe", "rule", "line_id", "at", name="uq_line_signals_bar"),
    )
    op.create_index("ix_line_signals_trade_date", "line_signals", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_line_signals_trade_date", table_name="line_signals")
    op.drop_table("line_signals")
    op.drop_table("line_watch")
