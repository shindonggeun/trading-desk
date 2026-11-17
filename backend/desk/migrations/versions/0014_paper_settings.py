"""paper_settings: the paper account's one settings row (#527)

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    from sqlalchemy.dialects.postgresql import JSONB

    return sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "paper_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("body", _json(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_paper_settings"),
    )


def downgrade() -> None:
    op.drop_table("paper_settings")
