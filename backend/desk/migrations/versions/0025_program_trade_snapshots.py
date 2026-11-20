"""futures_snapshots: KOSPI / KOSDAQ program trading net buy at the same moment (#677)

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [f"{m}_program_{k}_100m" for m in ("kospi", "kosdaq") for k in ("arbitrage", "non_arbitrage", "total")]


def upgrade() -> None:
    with op.batch_alter_table("futures_snapshots") as batch:
        for name in COLUMNS:
            batch.add_column(sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("futures_snapshots") as batch:
        for name in reversed(COLUMNS):
            batch.drop_column(name)
