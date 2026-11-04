"""Read-only REST routes for the audit console (docs/CONTRACTS.md 5.6). Nothing here writes.

GET /payments        latest payments, newest first, in the decision-response shape (5.4)
GET /payments/{id}   one payment
GET /audit           audit entries, oldest first, paged with ?after_id=
GET /audit/verify    recompute the hash chain

Money is a string ("179.00"), times are UTC ISO-8601 with Z. `risk_score` and `judge` come from the
payment's latest `payment.decided` audit entry, so they appear as soon as the orchestrator writes
them there, and are null until then.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.audit import verify_chain
from app.models import AuditEvent, Payment
from app.times import as_utc

router = APIRouter()
Session = Annotated[AsyncSession, Depends(db.get_session)]


def _time(value: datetime | None) -> str | None:
    value = as_utc(value)
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def payment_view(payment: Payment, decided: dict | None = None) -> dict:
    decided = decided or {}
    return {
        "payment_id": payment.id,
        "decision": payment.decision,
        "status": payment.status,
        "reasons": [{"source": None, **r} for r in payment.reasons or []],
        "risk_score": decided.get("risk_score"),
        "judge": decided.get("judge"),
        "approval_url": None,
        "mandate_id": payment.mandate_id,
        "merchant_id": payment.merchant_id,
        "amount": f"{payment.amount:.2f}",
        "currency": payment.currency,
        "items": payment.items,
        "created_at": _time(payment.created_at),
        "updated_at": _time(payment.updated_at),
    }


async def _decided(session: AsyncSession, payment_ids: list[str]) -> dict[str, dict]:
    """Latest payment.decided payload per payment (later entries carry the judge and model)."""
    if not payment_ids:
        return {}
    rows = await session.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == "payment.decided", AuditEvent.payment_id.in_(payment_ids))
        .order_by(AuditEvent.id)
    )
    return {row.payment_id: row.payload for row in rows}


@router.get("/payments")
async def list_payments(
    session: Session, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[dict]:
    payments = (
        await session.scalars(
            select(Payment).order_by(Payment.created_at.desc(), Payment.id.desc()).limit(limit)
        )
    ).all()
    decided = await _decided(session, [p.id for p in payments])
    return [payment_view(p, decided.get(p.id)) for p in payments]


@router.get("/payments/{payment_id}")
async def get_payment(payment_id: str, session: Session) -> dict:
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="payment not found")
    decided = await _decided(session, [payment_id])
    return payment_view(payment, decided.get(payment_id))


@router.get("/audit")
async def list_audit(
    session: Session,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[dict]:
    rows = await session.scalars(
        select(AuditEvent).where(AuditEvent.id > after_id).order_by(AuditEvent.id).limit(limit)
    )
    return [
        {
            "id": row.id,
            "created_at": _time(row.created_at),
            "event_type": row.event_type,
            "payment_id": row.payment_id,
            "payload": row.payload,
            "prev_hash": row.prev_hash,
            "hash": row.hash,
        }
        for row in rows
    ]


@router.get("/audit/verify")
async def audit_verify(session: Session) -> dict:
    """Recompute the audit hash chain: {ok, checked, first_bad_id}."""
    return asdict(await verify_chain(session))
