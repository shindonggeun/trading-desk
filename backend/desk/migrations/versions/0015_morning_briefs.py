"""morning_briefs: the morning market brief at 07:00 KST (#571)

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    from sqlalchemy.dialects.postgresql import JSONB

    return sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "morning_briefs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("trigger", sa.String(8), nullable=False),
        sa.Column("body", _json(), nullable=False),
        sa.Column("inputs", _json(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_morning_briefs"),
    )
    op.create_index("ix_morning_briefs_day", "morning_briefs", ["day"])
    op.create_index("ix_morning_briefs_created_at", "morning_briefs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_morning_briefs_created_at", table_name="morning_briefs")
    op.drop_index("ix_morning_briefs_day", table_name="morning_briefs")
    op.drop_table("morning_briefs")
