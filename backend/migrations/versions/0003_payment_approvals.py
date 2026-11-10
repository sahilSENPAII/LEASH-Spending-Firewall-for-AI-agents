"""step-up approval columns on payments

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

COLUMNS = (
    "approval_token_hash",
    "approval_expires_at",
    "approval_decided_at",
    "approval_decision",
)


def upgrade() -> None:
    # SHA-256 hex of the approval token; the raw token is never stored.
    op.add_column("payments", sa.Column("approval_token_hash", sa.String(64)))
    op.add_column("payments", sa.Column("approval_expires_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("approval_decided_at", sa.DateTime(timezone=True)))
    # approved | denied | expired
    op.add_column("payments", sa.Column("approval_decision", sa.String(16)))


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_column("payments", name)
