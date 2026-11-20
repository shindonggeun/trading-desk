"""holding_watch: the holding stop / take-profit watch setting (#694)

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "holding_watch",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("body", JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_holding_watch"),
    )


def downgrade() -> None:
    op.drop_table("holding_watch")
