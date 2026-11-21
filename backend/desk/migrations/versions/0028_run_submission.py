"""Runs: record authenticated submission provenance (#775).

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A companion table allows both foreign keys without rebuilding runs on SQLite.
    op.create_table(
        "run_submissions",
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "non_account_submission", sa.Boolean(),
            sa.CheckConstraint("non_account_submission IN (FALSE, TRUE)"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_run_submissions"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], ondelete="CASCADE", name="fk_run_submissions_run_id_runs",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="fk_run_submissions_submitted_by_user_id_users",
        ),
    )
    op.create_index("ix_run_submissions_submitted_by_user_id", "run_submissions", ["submitted_by_user_id"])


def downgrade() -> None:
    op.drop_table("run_submissions")
