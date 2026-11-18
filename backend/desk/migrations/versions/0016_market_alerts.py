"""market_alerts: investor flow shifts and NQ=F alarms (#579)

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("rule", sa.String(length=24), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("who", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("at", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("prev", sa.Float(), nullable=True),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("pushed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_market_alerts"),
        sa.UniqueConstraint("trade_date", "kind", "rule", "market", "who", "at", name="uq_market_alerts_moment"),
    )
    op.create_index("ix_market_alerts_trade_date", "market_alerts", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_market_alerts_trade_date", table_name="market_alerts")
    op.drop_table("market_alerts")
