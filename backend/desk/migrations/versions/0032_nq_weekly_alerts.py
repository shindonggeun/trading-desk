"""Persist NQ weekly rules and per-channel delivery state (#914)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json = sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    op.create_table(
        "nq_weekly_rules",
        sa.Column("scope", sa.String(96), primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("settings", json, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_week", sa.Date),
        sa.Column("last_match", sa.Boolean),
    )
    op.create_table(
        "nq_weekly_deliveries",
        sa.Column("scope", sa.String(96), primary_key=True),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("week", sa.Date, primary_key=True),
        sa.Column("channel", sa.String(16), primary_key=True),
        sa.Column("payload", json, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("nq_weekly_deliveries")
    op.drop_table("nq_weekly_rules")
