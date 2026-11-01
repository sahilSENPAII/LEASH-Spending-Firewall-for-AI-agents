"""Decision orchestrator: policy check, then PayPal authorize + capture, all recorded in Postgres.

Every request ends in ALLOW or BLOCK. Any error, in a check or in PayPal, is a BLOCK (D3).
Status changes go through `app.state.transition`, and each one writes an audit event.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditEvent, Payment
from app.paypal import PayPalClient
from app.policy import (
    Mandate,
    PaymentRequest,
    UsageSnapshot,
    VelocityPolicy,
    evaluate_payment,
)
from app.state import PaymentStatus, transition

log = logging.getLogger(__name__)

# Stand-in until the mandates module (verify_mandate) exists. It is a server-side constant,
# so there is no signature to check yet.
DEMO_MANDATE = Mandate(
    mandate_id="m_demo",
    category="electronics",
    max_payment_amount=Decimal("200.00"),
    total_budget=Decimal("500.00"),
    max_payment_count=5,
    allowed_merchant_ids=frozenset({"soundhub"}),
    expires_at=datetime(2026, 12, 31, tzinfo=UTC),
    revoked=False,
)
VELOCITY = VelocityPolicy(max_payments=3, window=timedelta(minutes=10))
# Category comes from our own store list, never from the agent.
MERCHANT_CATEGORIES = {"soundhub": "electronics", "megadeals": "electronics"}
# Statuses where money is held or taken, so they count against the mandate.
SPENT = (PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED)

INVALID_AMOUNT = {
    "code": "invalid_request_amount",
    "message": "Request amount must be a decimal string such as 12.50.",
}
AUTHORIZE_FAILED = {"code": "paypal_authorize_failed", "message": "PayPal did not authorize."}
CAPTURE_FAILED = {"code": "paypal_capture_failed", "message": "PayPal did not capture."}
INTERNAL_ERROR = {"code": "internal_error", "message": "Leash could not complete its checks."}


def _response(payment: Payment | None, decision: str, reasons: list[dict]) -> dict:
    return {
        "payment_id": payment.id if payment else None,
        "decision": decision,
        "status": payment.status if payment else None,
        "reasons": reasons,
    }


def internal_error() -> dict:
    return _response(None, "BLOCK", [INTERNAL_ERROR])


async def request_payment(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalClient,
    vault_id: str,
    *,
    mandate_id: str,
    merchant_id: str,
    amount: str,
    items: str,
    now: datetime | None = None,
) -> dict:
    try:
        async with sessions() as session:
            return await _decide_and_pay(
                session,
                paypal,
                vault_id,
                mandate_id=mandate_id,
                merchant_id=merchant_id,
                amount=amount,
                items=items,
                now=now or datetime.now(UTC),
            )
    except Exception:
        log.exception("request_payment failed")
        return internal_error()


async def _decide_and_pay(
    session: AsyncSession,
    paypal: PayPalClient,
    vault_id: str,
    *,
    mandate_id: str,
    merchant_id: str,
    amount: str,
    items: str,
    now: datetime,
) -> dict:
    try:
        value = Decimal(amount)
    except (InvalidOperation, TypeError, ValueError):
        return _response(None, "BLOCK", [INVALID_AMOUNT])

    mandate = DEMO_MANDATE if mandate_id == DEMO_MANDATE.mandate_id else None
    decision = evaluate_payment(
        request=PaymentRequest(
            amount=value,
            currency="USD",
            category=MERCHANT_CATEGORIES.get(merchant_id, ""),
            merchant_id=merchant_id,
        ),
        mandate=mandate,
        usage=await _usage(session, mandate_id, now),
        velocity_policy=VELOCITY,
        mandate_signature_valid=mandate is not None,
        now=now,
    )
    reasons = [{"code": r.code, "message": r.message} for r in decision.reasons]

    payment = Payment(
        mandate_id=mandate_id,
        merchant_id=merchant_id,
        amount=value,
        currency="USD",
        items=items,
        status=PaymentStatus.REQUESTED,
        decision=decision.outcome.value,
        reasons=reasons,
        created_at=now,
    )
    session.add(payment)
    await session.flush()
    session.add(
        AuditEvent(
            event_type="payment.decided",
            payment_id=payment.id,
            payload={
                "decision": decision.outcome.value,
                "reasons": reasons,
                "mandate_id": mandate_id,
                "merchant_id": merchant_id,
                "amount": str(value),
            },
        )
    )

    if not decision.is_allowed:
        _move(session, payment, PaymentStatus.BLOCKED)
        await session.commit()
        return _response(payment, "BLOCK", reasons)

    # Record the ALLOW before any money moves.
    await session.commit()

    try:
        if not vault_id:
            raise ValueError("PAYPAL_VAULT_ID is not set")
        auth = await paypal.authorize(
            vault_id=vault_id,
            amount=value,
            request_id=payment.idempotency_key,
            custom_id=merchant_id,
            description=items,
        )
    except Exception:
        log.exception("authorize failed for payment %s", payment.id)
        return await _fail(session, payment, PaymentStatus.FAILED, AUTHORIZE_FAILED)

    payment.paypal_order_id = auth.order_id
    payment.paypal_authorization_id = auth.authorization_id
    _move(session, payment, PaymentStatus.AUTHORIZED, order_id=auth.order_id)
    await session.commit()

    try:
        status = await paypal.capture(auth.authorization_id, f"{payment.idempotency_key}-capture")
        if status not in ("COMPLETED", "PENDING"):
            raise ValueError(f"capture status {status}")
    except Exception:
        log.exception("capture failed for payment %s", payment.id)
        try:
            await paypal.void(auth.authorization_id, f"{payment.idempotency_key}-void")
        except Exception:
            # The hold stays AUTHORIZED and keeps counting against the budget.
            log.exception("void failed for payment %s", payment.id)
            return await _fail(session, payment, None, CAPTURE_FAILED)
        return await _fail(session, payment, PaymentStatus.VOIDED, CAPTURE_FAILED)

    _move(session, payment, PaymentStatus.CAPTURED, capture_status=status)
    await session.commit()
    return _response(payment, "ALLOW", [])


async def _usage(session: AsyncSession, mandate_id: str, now: datetime) -> UsageSnapshot:
    spent = (Payment.mandate_id == mandate_id) & Payment.status.in_(SPENT)
    budget_used, count = (
        await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0), func.count()).where(spent)
        )
    ).one()
    recent = await session.scalar(
        select(func.count()).where(spent, Payment.created_at > now - VELOCITY.window)
    )
    return UsageSnapshot(
        budget_used=Decimal(budget_used), payment_count=count, recent_payment_count=recent
    )


def _move(session: AsyncSession, payment: Payment, target: PaymentStatus, **payload) -> None:
    payment.status = transition(PaymentStatus(payment.status), target)
    session.add(
        AuditEvent(
            event_type=f"payment.{target.lower()}",
            payment_id=payment.id,
            payload={"status": target.value, **payload},
        )
    )


async def _fail(
    session: AsyncSession, payment: Payment, target: PaymentStatus | None, reason: dict
) -> dict:
    payment.decision = "BLOCK"
    payment.reasons = [reason]
    if target is None:
        session.add(AuditEvent(event_type="payment.error", payment_id=payment.id, payload=reason))
    else:
        _move(session, payment, target, **reason)
    await session.commit()
    return _response(payment, "BLOCK", [reason])
