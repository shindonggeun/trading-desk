"""TOTP columns on users, recovery_codes

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("totp_secret", sa.Text))
        batch.add_column(sa.Column("totp_pending", sa.Text))
        batch.add_column(sa.Column("totp_pending_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("totp_enabled_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("totp_last_step", sa.Integer))
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_recovery_codes"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_recovery_codes_user_id_users",
                                ondelete="CASCADE"),
    )
    op.create_index("ix_recovery_codes_user_id", "recovery_codes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_recovery_codes_user_id", table_name="recovery_codes")
    op.drop_table("recovery_codes")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("totp_last_step")
        batch.drop_column("totp_enabled_at")
        batch.drop_column("totp_pending_at")
        batch.drop_column("totp_pending")
        batch.drop_column("totp_secret")
