"""broker_tokens: broker tokens shared by the desk's processes, issued by one of them (#688)

Revision ID: 0024
Revises: 0022
Create Date: 2026-11-20

0023 is #694's (holding_watch). Whichever of the two lands second points its down_revision at the other.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_tokens",
        sa.Column("name", sa.String(length=16), nullable=False),
        sa.Column("sealed", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.String(length=16), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refusals", sa.Integer(), nullable=False),
        sa.Column("refresh_for", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("name", name="pk_broker_tokens"),
    )


def downgrade() -> None:
    op.drop_table("broker_tokens")
