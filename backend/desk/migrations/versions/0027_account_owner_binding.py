"""Add an explicit account owner binding without selecting an owner (#775).

Revision ID: 0027
Revises: 0026
"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    binding = op.create_table(
        "account_owner_binding",
        sa.Column("id", sa.Integer(), sa.CheckConstraint("id = 1"), nullable=False, autoincrement=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_account_owner_binding"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_account_owner_binding_user_id_users", ondelete="SET NULL",
        ),
    )
    op.bulk_insert(binding, [{"id": 1, "user_id": None, "generation": uuid4().hex}])


def downgrade() -> None:
    op.drop_table("account_owner_binding")
