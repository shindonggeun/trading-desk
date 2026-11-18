"""krx_daily · krx_days: KRX OPEN API end-of-day rows and the days already asked (#601)

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json():
    from sqlalchemy.dialects.postgresql import JSONB

    return sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "krx_daily",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api", sa.String(length=32), nullable=False),
        sa.Column("bas_dd", sa.Date(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_krx_daily"),
        sa.UniqueConstraint("api", "bas_dd", "code", name="uq_krx_daily_row"),
    )
    op.create_index("ix_krx_daily_api_day", "krx_daily", ["api", "bas_dd"])
    op.create_table(
        "krx_days",
        sa.Column("api", sa.String(length=32), nullable=False),
        sa.Column("bas_dd", sa.Date(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("api", "bas_dd", name="pk_krx_days"),
    )


def downgrade() -> None:
    op.drop_table("krx_days")
    op.drop_index("ix_krx_daily_api_day", table_name="krx_daily")
    op.drop_table("krx_daily")
