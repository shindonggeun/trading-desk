"""notify_channels: Telegram · KakaoTalk alert channels beside web push (#580)

Revision ID: 0018
Revises: 0016
Create Date: 2026-11-18

0017 is #572's; whichever merges second moves its down_revision onto the other (tests/test_db.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    from sqlalchemy.dialects.postgresql import JSONB

    return sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "notify_channels",
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("kinds", _json(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_reconnect", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("channel", name="pk_notify_channels"),
    )


def downgrade() -> None:
    op.drop_table("notify_channels")
