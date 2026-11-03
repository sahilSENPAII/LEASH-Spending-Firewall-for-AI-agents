"""Database tables: payments (money path), mandates and budget holds (trust path), audit events.

Mandates, budget holds and the hash-chain logic for audit_events are owned by Anurag
(see task-split.md); their logic lives in app/mandates.py and app/audit.py.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, Numeric, String, func
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


class MandateRow(Base):
    """A signed mandate. Terms columns are covered by `signature`; the last three are state."""

    __tablename__ = "mandates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    intent: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(64))
    max_payment_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_budget: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    max_payment_count: Mapped[int] = mapped_column(Integer)
    allowed_merchant_ids: Mapped[list] = mapped_column(JSON)  # sorted list of merchant ids
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signature: Mapped[str] = mapped_column(String(128))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget_used: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    payment_count: Mapped[int] = mapped_column(Integer, default=0)


class BudgetHold(Base):
    """Budget held for one payment between ALLOW and capture; released on any other end."""

    __tablename__ = "budget_holds"

    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), primary_key=True)
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
