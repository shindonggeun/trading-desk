"""futures_snapshots: KOSPI200 futures investor flows through the session (#254)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "futures_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trade_date", sa.String(10), nullable=False),
        sa.Column("code", sa.String(12), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("open_interest", sa.Float(), nullable=True),
        sa.Column("market_basis", sa.Float(), nullable=True),
        sa.Column("foreign_contracts", sa.Float(), nullable=True),
        sa.Column("foreign_value_mn", sa.Float(), nullable=True),
        sa.Column("individual_contracts", sa.Float(), nullable=True),
        sa.Column("individual_value_mn", sa.Float(), nullable=True),
        sa.Column("institution_contracts", sa.Float(), nullable=True),
        sa.Column("institution_value_mn", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_futures_snapshots"),
    )
    op.create_index("ix_futures_snapshots_trade_date_at", "futures_snapshots", ["trade_date", "at"])


def downgrade() -> None:
    op.drop_index("ix_futures_snapshots_trade_date_at", table_name="futures_snapshots")
    op.drop_table("futures_snapshots")
