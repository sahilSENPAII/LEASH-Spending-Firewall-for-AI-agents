"""payments and audit_events

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mandate_id", sa.String(64), nullable=False, index=True),
        sa.Column("merchant_id", sa.String(64), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("items", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(16)),
        sa.Column("reasons", sa.JSON, nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("paypal_order_id", sa.String(64)),
        sa.Column("paypal_authorization_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payment_id", sa.String(36), index=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("prev_hash", sa.String(64)),
        sa.Column("hash", sa.String(64)),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("payments")
