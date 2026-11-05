"""Plan outcomes on runs: how each plan played out (desk.outcome, #64)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

Json = sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("outcome", Json))
        batch.add_column(sa.Column("outcome_status", sa.String(16)))
        batch.add_column(sa.Column("scored_at", sa.DateTime(timezone=True)))
    op.create_index("ix_runs_outcome_status", "runs", ["outcome_status"])


def downgrade() -> None:
    op.drop_index("ix_runs_outcome_status", table_name="runs")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("scored_at")
        batch.drop_column("outcome_status")
        batch.drop_column("outcome")
