"""Database tables owned by the money path (payments) plus the shared audit table.

Mandates and the hash-chain logic for audit_events are owned by Anurag (see task-split.md);
the audit columns for the chain exist here so the schema is ready.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, DateTime, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.state import PaymentStatus


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mandate_id: Mapped[str] = mapped_column(String(64), index=True)
    merchant_id: Mapped[str] = mapped_column(String(64), index=True)  # our store id (D10)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    items: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(32), default=PaymentStatus.REQUESTED)
    decision: Mapped[str | None] = mapped_column(String(16))  # ALLOW / STEP_UP / BLOCK
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    # One key per logical payment, reused on every retry so PayPal never double-charges.
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, default=_uuid)
    paypal_order_id: Mapped[str | None] = mapped_column(String(64))
    paypal_authorization_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_type: Mapped[str] = mapped_column(String(64))  # e.g. payment.decided, payment.captured
    payment_id: Mapped[str | None] = mapped_column(String(36), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str | None] = mapped_column(String(64))  # filled by the hash-chain module
    hash: Mapped[str | None] = mapped_column(String(64))
