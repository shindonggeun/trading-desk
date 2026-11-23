"""Persist epoch-bound Toss approvals without recording source credentials (#871)."""

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_toss_attestations",
        sa.Column("generation", sa.String(32), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(32), nullable=False),
        sa.Column("approved_isolation_epoch", sa.Integer(), nullable=False),
        sa.Column("approval_fingerprint", sa.LargeBinary(32), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_owner_toss_attestations_version"),
        sa.CheckConstraint("approved_isolation_epoch > 0", name="ck_owner_toss_attestations_epoch"),
        sa.CheckConstraint("length(fingerprint) = 32 AND length(approval_fingerprint) = 32",
                           name="ck_owner_toss_attestations_fingerprints"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    policy = sa.table("privacy_policy", sa.column("id", sa.Integer()), sa.column("epoch", sa.Integer()))
    # Serialize the emptiness check and drop with an isolated approval on either database.
    conn.execute(policy.update().where(policy.c.id == 1).values(epoch=policy.c.epoch))
    proofs = sa.table("owner_toss_attestations")
    if conn.scalar(sa.select(sa.literal(1)).select_from(proofs).limit(1)) is not None:
        raise RuntimeError("Toss attestation history must be retained; restore an approved isolated backup")
    op.drop_table("owner_toss_attestations")
