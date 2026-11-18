"""futures_oi_daily: KOSPI200 futures open interest, basis and options put/call per day from KRX (#603)

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "futures_oi_daily",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("settle", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("basis", sa.Float(), nullable=True),
        sa.Column("oi", sa.Float(), nullable=True),
        sa.Column("oi_change", sa.Float(), nullable=True),
        sa.Column("oi_total", sa.Float(), nullable=True),
        sa.Column("oi_total_change", sa.Float(), nullable=True),
        sa.Column("rolling", sa.Boolean(), nullable=False),
        sa.Column("pc_oi_ratio", sa.Float(), nullable=True),
        sa.Column("atm_iv", sa.Float(), nullable=True),
        sa.Column("atm_iv_expiry", sa.Date(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trade_date", name="pk_futures_oi_daily"),
    )


def downgrade() -> None:
    op.drop_table("futures_oi_daily")
