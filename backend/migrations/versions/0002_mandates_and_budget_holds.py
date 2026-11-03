"""mandates and budget_holds; one-off wipe of audit_events test data

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mandates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("intent", sa.String(500), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("max_payment_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_budget", sa.Numeric(12, 2), nullable=False),
        sa.Column("max_payment_count", sa.Integer, nullable=False),
        sa.Column("allowed_merchant_ids", sa.JSON, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature", sa.String(128), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("budget_used", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("payment_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "budget_holds",
        sa.Column("payment_id", sa.String(36), sa.ForeignKey("payments.id"), primary_key=True),
        sa.Column(
            "mandate_id", sa.String(64), sa.ForeignKey("mandates.id"), nullable=False, index=True
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
    )
    delete_all_audit_events_one_off()


def delete_all_audit_events_one_off() -> None:
    """ONE-OFF DATA DELETION: removes every row in audit_events.

    Everything there before 0002 is end-to-end test data with no hashes (agreed with Sahil,
    Nov 2), so the hash chain starts clean. `payments` is not touched. Cannot be undone.
    """
    op.execute("DELETE FROM audit_events")


def downgrade() -> None:
    # The audit_events rows deleted by upgrade() are not restored.
    op.drop_table("budget_holds")
    op.drop_table("mandates")
