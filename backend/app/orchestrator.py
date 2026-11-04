"""Decision orchestrator: price the cart, policy check, then PayPal authorize + capture, all
recorded in Postgres.

Leash prices every item from its own catalog (app.catalog); the agent sends no price or amount.
Every request ends in ALLOW or BLOCK. Any error, in a check or in PayPal, is a BLOCK (D3).
Call order and release points follow docs/CONTRACTS.md section 5.3. Status changes go through
`app.state.transition`, and each one writes an audit event through `append_audit`.
"""

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit import append_audit
from app.catalog import get_catalog, price_cart
from app.mandates import check_and_reserve, release_budget
from app.models import Payment
from app.paypal import PayPalClient
from app.policy import PaymentRequest
from app.state import PaymentStatus, transition

log = logging.getLogger(__name__)

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
    items: list[dict],
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
    items: list[dict],
    now: datetime,
) -> dict:
    cart = price_cart(get_catalog(), merchant_id, items)
    payment = Payment(
        mandate_id=mandate_id,
        merchant_id=merchant_id,
        amount=cart.amount,
        currency="USD",
        items=json.dumps(cart.lines),
        status=PaymentStatus.REQUESTED,
        created_at=now,
    )
    session.add(payment)
    # The budget hold references payments.id, so the row must exist first.
    await session.flush()
    if cart.reasons:
        # A cart Leash cannot price is blocked before the mandate check, so no hold is taken.
        outcome, allowed, reasons = "BLOCK", False, cart.reasons
    else:
        decision = await check_and_reserve(
            session,
            payment_id=payment.id,
            request=PaymentRequest(
                amount=cart.amount,
                currency="USD",
                category=cart.category,
                merchant_id=merchant_id,
            ),
            mandate_id=mandate_id,
            now=now,
        )
        outcome, allowed = decision.outcome.value, decision.is_allowed
        reasons = [{"code": r.code, "message": r.message} for r in decision.reasons]
    payment.decision = outcome
    payment.reasons = reasons
    await append_audit(
        session,
        event_type="payment.decided",
        payment_id=payment.id,
        payload={
            "decision": outcome,
            "reasons": reasons,
            "mandate_id": mandate_id,
            "merchant_id": merchant_id,
            "amount": str(cart.amount),
            "items": cart.lines,
        },
        now=now,
    )

    if not allowed:
        await _move(session, payment, PaymentStatus.BLOCKED, now)
        await session.commit()
        return _response(payment, "BLOCK", reasons)

    # Record the ALLOW and its budget hold before any money moves.
    await session.commit()

    try:
        if not vault_id:
            raise ValueError("PAYPAL_VAULT_ID is not set")
        auth = await paypal.authorize(
            vault_id=vault_id,
            amount=cart.amount,
            request_id=payment.idempotency_key,
            custom_id=merchant_id,
            # Same description as reconcile's replay of this call (app/reconcile.py).
            description=payment.items,
        )
    except Exception:
        log.exception("authorize failed for payment %s", payment.id)
        return await _fail(session, payment, PaymentStatus.FAILED, AUTHORIZE_FAILED, now)

    payment.paypal_order_id = auth.order_id
    payment.paypal_authorization_id = auth.authorization_id
    await _move(session, payment, PaymentStatus.AUTHORIZED, now, order_id=auth.order_id)
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
            return await _fail(session, payment, None, CAPTURE_FAILED, now)
        return await _fail(session, payment, PaymentStatus.VOIDED, CAPTURE_FAILED, now)

    await _move(session, payment, PaymentStatus.CAPTURED, now, capture_status=status)
    await session.commit()
    return _response(payment, "ALLOW", [])


async def _move(
    session: AsyncSession, payment: Payment, target: PaymentStatus, now: datetime, **payload
) -> None:
    payment.status = transition(PaymentStatus(payment.status), target)
    await append_audit(
        session,
        event_type=f"payment.{target.lower()}",
        payment_id=payment.id,
        payload={"status": target.value, **payload},
        now=now,
    )


async def _fail(
    session: AsyncSession,
    payment: Payment,
    target: PaymentStatus | None,
    reason: dict,
    now: datetime,
) -> dict:
    payment.decision = "BLOCK"
    payment.reasons = [reason]
    if target is None:
        await append_audit(
            session,
            event_type="payment.failed",
            payment_id=payment.id,
            payload={"void_failed": True, **reason},
            now=now,
        )
    else:
        # Release before append_audit: mandate row lock, then chain lock (CONTRACTS section 3).
        await release_budget(session, payment_id=payment.id, now=now)
        await _move(session, payment, target, now, **reason)
    await session.commit()
    return _response(payment, "BLOCK", [reason])
