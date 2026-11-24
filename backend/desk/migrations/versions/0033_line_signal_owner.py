"""Bind saved line alerts to their owner at dispatch (#930)."""

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("line_signals", sa.Column("signal_owner_id", sa.Integer()))
    op.add_column("line_signals", sa.Column("signal_owner_generation", sa.String(32)))


def downgrade() -> None:
    op.drop_column("line_signals", "signal_owner_generation")
    op.drop_column("line_signals", "signal_owner_id")
