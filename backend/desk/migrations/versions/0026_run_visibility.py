"""Runs: store an explicit nullable shareability decision (#775).

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("is_shareable", sa.Boolean(), sa.CheckConstraint("is_shareable IN (FALSE, TRUE)"), nullable=True),
    )


def downgrade() -> None:
    # Native DROP COLUMN (SQLite 3.35+) leaves events' parent table and foreign key intact.
    op.drop_column("runs", "is_shareable")
