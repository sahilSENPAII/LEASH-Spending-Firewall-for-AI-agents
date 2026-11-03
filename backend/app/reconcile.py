"""Saga cleanup: settle payments stuck in REQUESTED or AUTHORIZED, with PayPal as source of truth.

A DB write can fail after PayPal authorized or captured, leaving a row that disagrees with PayPal.
No transaction stays open across a PayPal call (CONTRACTS section 5.3). Anything unclear leaves the
payment and its hold unchanged for the next run or a person (D3).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit import append_audit
from app.mandates import release_budget
from app.models import Payment
from app.paypal import PayPalClient
from app.state import PaymentStatus, transition
from app.times import as_utc, require_utc

log = logging.getLogger(__name__)

# Longer than one request can spend in flight, so a live payment is never voided under it.
MIN_AGE = timedelta(minutes=5)
# PayPal keeps idempotency keys for 6 hours. Later replays make a new order and miss the old hold.
REPLAY_WINDOW = timedelta(hours=5)

HELD_AT_PAYPAL = {"CREATED", "PENDING"}
NOT_HELD_AT_PAYPAL = {"VOIDED", "DENIED"}

RECONCILED = {"code": "reconciled", "message": "Leash settled this payment from PayPal's records."}


@dataclass(frozen=True)
class ReconcileResult:
    payment_id: str
    before: str
    after: str | None  # None: nothing changed
    detail: str


async def reconcile_payments(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalClient,
    vault_id: str,
    *,
    older_than: timedelta,
    now: datetime | None = None,
) -> list[ReconcileResult]:
    """Settle every payment unchanged for `older_than`. One failure does not stop the others."""
    if older_than < MIN_AGE:
        raise ValueError(f"older_than must be at least {MIN_AGE}")
    now = require_utc(now or datetime.now(UTC))
    async with sessions() as session:
        stuck = await session.scalars(
            select(Payment)
            .where(
                Payment.status.in_([PaymentStatus.REQUESTED, PaymentStatus.AUTHORIZED]),
                Payment.updated_at < now - older_than,
            )
            .order_by(Payment.created_at)
        )
        payments = stuck.all()

    results = []
    for payment in payments:
        try:
            results.append(await _reconcile_one(sessions, paypal, vault_id, payment, now))
        except Exception as exc:
            log.exception("reconcile failed for payment %s", payment.id)
            results.append(_unchanged(payment, f"{type(exc).__name__}: {exc}"))
    return results


async def _reconcile_one(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalClient,
    vault_id: str,
    payment: Payment,
    now: datetime,
) -> ReconcileResult:
    order_id = payment.paypal_order_id
    authorization_id = payment.paypal_authorization_id
    created = False

    if authorization_id is None:
        if payment.status == PaymentStatus.AUTHORIZED:
            return _unchanged(payment, "AUTHORIZED without an authorization id; check by hand")
        if not vault_id:
            return _unchanged(payment, "PAYPAL_VAULT_ID is not set")
        if now - as_utc(payment.created_at) >= REPLAY_WINDOW:
            return _unchanged(payment, "too old to look up by idempotency key; check by hand")
        lookup = await paypal.find_order(
            vault_id=vault_id,
            amount=payment.amount,
            request_id=payment.idempotency_key,
            currency=payment.currency,
            custom_id=payment.merchant_id,
            description=payment.items,
        )
        order_id, authorization_id, created = (
            lookup.order_id,
            lookup.authorization_id,
            lookup.created,
        )
        if authorization_id is not None:
            # Saved before the void: once the key expires, this id is the only way back to the hold.
            async with sessions() as session:
                row = await session.get(Payment, payment.id, with_for_update=True)
                if row.status != payment.status:
                    return _unchanged(payment, f"status moved to {row.status} during reconcile")
                row.paypal_order_id = order_id
                row.paypal_authorization_id = authorization_id
                await append_audit(
                    session,
                    event_type="payment.reconciled_lookup",
                    payment_id=row.id,
                    payload={
                        "order_id": order_id,
                        "authorization_id": authorization_id,
                        "order_created_by_lookup": created,
                    },
                    now=now,
                )
                await session.commit()

    paypal_status = None
    void_sent = False
    if authorization_id is None:
        target = PaymentStatus.FAILED
    else:
        paypal_status = await paypal.authorization_status(authorization_id)
        if paypal_status == "CAPTURED":
            target = PaymentStatus.CAPTURED
        elif paypal_status in HELD_AT_PAYPAL or paypal_status in NOT_HELD_AT_PAYPAL:
            if paypal_status in HELD_AT_PAYPAL:
                await paypal.void(authorization_id, f"{payment.idempotency_key}-void")
                void_sent = True
            # A new order's hold came from this lookup, so the payment itself never held money.
            target = PaymentStatus.FAILED if created else PaymentStatus.VOIDED
        else:
            return _unchanged(payment, f"PayPal status {paypal_status}; check by hand")

    async with sessions() as session:
        row = await session.get(Payment, payment.id, with_for_update=True)
        if row.status != payment.status:
            return _unchanged(payment, f"status moved to {row.status} during reconcile")

        row.paypal_order_id = order_id
        row.paypal_authorization_id = authorization_id
        if target == PaymentStatus.CAPTURED:
            row.decision, row.reasons = "ALLOW", []
        else:
            # Release before append_audit: mandate row lock, then chain lock (CONTRACTS section 3).
            await release_budget(session, payment_id=row.id, now=now)
            row.decision, row.reasons = "BLOCK", [RECONCILED]

        steps = [target]
        if row.status == PaymentStatus.REQUESTED and target != PaymentStatus.FAILED:
            steps.insert(0, PaymentStatus.AUTHORIZED)
        for step in steps:
            row.status = transition(PaymentStatus(row.status), step)
            await append_audit(
                session,
                event_type=f"payment.reconciled_{step.lower()}",
                payment_id=row.id,
                payload={
                    "status": step.value,
                    "paypal_status": paypal_status,
                    "order_id": order_id,
                    "authorization_id": authorization_id,
                    "order_created_by_lookup": created,
                    "void_sent": void_sent,
                },
                now=now,
            )
        await session.commit()
    return ReconcileResult(payment.id, payment.status, target.value, f"PayPal: {paypal_status}")


def _unchanged(payment: Payment, detail: str) -> ReconcileResult:
    log.error("payment %s left %s: %s", payment.id, payment.status, detail)
    return ReconcileResult(payment.id, payment.status, None, detail)
