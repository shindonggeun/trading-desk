"""futures_snapshots: the KOSPI / KOSDAQ spot investor flows at the same moment (#359)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [f"{m}_{c}_100m" for m in ("kospi", "kosdaq") for c in ("foreign", "institution", "individual")]


def upgrade() -> None:
    with op.batch_alter_table("futures_snapshots") as batch:
        for name in COLUMNS:
            batch.add_column(sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("futures_snapshots") as batch:
        for name in reversed(COLUMNS):
            batch.drop_column(name)
