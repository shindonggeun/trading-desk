"""Bind new protected account analyses to the recorded owner generation (#775)."""

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_submissions", sa.Column("owner_generation", sa.String(32)))


def downgrade() -> None:
    op.drop_column("run_submissions", "owner_generation")
