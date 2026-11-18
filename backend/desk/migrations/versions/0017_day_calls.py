"""day_calls: today's direction calls and how they scored (#572)

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json():
    from sqlalchemy.dialects.postgresql import JSONB

    return sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "day_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=4), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("call", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=24), nullable=False),
        sa.Column("model_version", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("verdict", sa.String(length=8), nullable=False),
        sa.Column("probs", _json(), nullable=False),
        sa.Column("reasons", _json(), nullable=False),
        sa.Column("edges_pct", _json(), nullable=False),
        sa.Column("features", _json(), nullable=False),
        sa.Column("outcome", _json(), nullable=True),
        sa.Column("hit_direction", sa.Boolean(), nullable=True),
        sa.Column("hit_stage", sa.Boolean(), nullable=True),
        sa.Column("brier", sa.Float(), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_day_calls"),
        sa.UniqueConstraint("market", "trade_date", "round", "call", name="uq_day_calls_call"),
    )
    op.create_index("ix_day_calls_trade_date", "day_calls", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_day_calls_trade_date", table_name="day_calls")
    op.drop_table("day_calls")
