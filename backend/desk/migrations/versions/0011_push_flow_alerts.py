"""push_subscriptions · flow_alerts_sent: web push and the foreign-flow turns already pushed (#361)

Revision ID: 0011
Revises: 0010 (#359 intraday flow) - merge #361 after #359.
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    from sqlalchemy.dialects.postgresql import JSONB

    return sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.String(1024), nullable=False),
        sa.Column("keys", _json(), nullable=False),
        sa.Column("device", sa.String(80), nullable=False),
        sa.Column("kinds", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_push_subscriptions_user_id_users",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_push_subscriptions"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])
    op.create_table(
        "flow_alerts_sent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.String(10), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("turned_at", sa.String(32), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("text", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_flow_alerts_sent"),
        sa.UniqueConstraint("trade_date", "market", "kind", "turned_at", name="uq_flow_alerts_sent_turn"),
    )
    op.create_index("ix_flow_alerts_sent_trade_date", "flow_alerts_sent", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_flow_alerts_sent_trade_date", table_name="flow_alerts_sent")
    op.drop_table("flow_alerts_sent")
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
