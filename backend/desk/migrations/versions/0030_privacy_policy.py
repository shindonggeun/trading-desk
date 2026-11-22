"""Add a shared process privacy fence, initially in legacy mode (#775)."""

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    policy = op.create_table(
        "privacy_policy",
        sa.Column("id", sa.Integer(), sa.CheckConstraint("id = 1", name="ck_privacy_policy_singleton"),
                  primary_key=True, autoincrement=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.CheckConstraint("mode IN ('legacy', 'isolated', 'protected')", name="ck_privacy_policy_mode"),
        sa.CheckConstraint("epoch >= 0 AND (mode <> 'legacy' OR epoch = 0)", name="ck_privacy_policy_epoch"),
    )
    op.bulk_insert(policy, [{"id": 1, "mode": "legacy", "epoch": 0}])


def downgrade() -> None:
    policy = sa.table("privacy_policy", sa.column("epoch", sa.Integer()))
    # Losing an active fence could silently reopen legacy reads on the same database.
    if op.get_bind().scalar(sa.select(policy.c.epoch)) != 0:
        raise RuntimeError("privacy policy history must be retained; restore an approved isolated backup")
    op.drop_table("privacy_policy")
