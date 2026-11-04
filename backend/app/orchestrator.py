"""Decision orchestrator: price the cart, policy check, then PayPal authorize + capture, all
recorded in Postgres.

Leash prices every item from its own catalog (app.catalog); the agent sends no price or amount.
After the policy ALLOW, the anomaly model (app.anomaly) can still turn it into STEP_UP or BLOCK.
Every request ends in ALLOW, STEP_UP or BLOCK. Any error is a BLOCK, except an error in the risk
check, which is a STEP_UP (D3).
Call order and release points follow docs/CONTRACTS.md section 5.3. Status changes go through
`app.state.transition`, and each one writes an audit event through `append_audit`.
"""

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.anomaly import AnomalyResult, build_features, score_anomaly
from app.audit import append_audit
from app.catalog import get_catalog, price_cart
from app.config import Settings, get_settings
from app.mandates import check_and_reserve, release_budget
from app.models import Payment
from app.paypal import PayPalClient
from app.policy import PaymentRequest
from app.state import PaymentStatus, transition

log = logging.getLogger(__name__)

AUTHORIZE_FAILED = {"code": "paypal_authorize_failed", "message": "PayPal did not authorize."}
CAPTURE_FAILED = {"code": "paypal_capture_failed", "message": "PayPal did not capture."}
INTERNAL_ERROR = {"code": "internal_error", "message": "Leash could not complete its checks."}
ANOMALY_FAILED = {
    "code": "anomaly_check_failed",
    "message": "The risk check did not complete, so a person must approve this payment.",
}


def _response(
    payment: Payment | None, decision: str, reasons: list[dict], risk_score: str | None = None
) -> dict:
    return {
        "payment_id": payment.id if payment else None,
        "decision": decision,
        "status": payment.status if payment else None,
        "reasons": reasons,
        "risk_score": risk_score,
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
                sessions,
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
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    paypal: PayPalClient,
    vault_id: str,
    *,
    mandate_id: str,
    merchant_id: str,
    items: list[dict],
    now: datetime,
) -> dict:
    settings = get_settings()
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

    # 5.3 step 2, with no transaction open. The intent judge (app/judge.py, not built yet) belongs
    # here, run alongside the model; its result then goes into _combine (5.2).
    risk = await _score_risk(sessions, payment, now, settings.velocity_window_seconds)
    outcome, reasons = _combine(risk, settings)
    risk_score = None if risk is None else f"{risk.score:.6f}"
    payment.decision = outcome
    payment.reasons = reasons
    if outcome == "BLOCK":
        # Release before append_audit: mandate row lock, then chain lock (CONTRACTS section 3).
        await release_budget(session, payment_id=payment.id, now=now)
    await append_audit(
        session,
        event_type="payment.decided",
        payment_id=payment.id,
        payload={
            "decision": outcome,
            "reasons": reasons,
            "risk_score": risk_score,
            "anomaly_reasons": list(risk.reasons) if risk else [],
            "model_version": risk.model_version if risk else None,
        },
        now=now,
    )
    if outcome == "BLOCK":
        await _move(session, payment, PaymentStatus.BLOCKED, now)
        await session.commit()
        return _response(payment, "BLOCK", reasons, risk_score)
    if outcome == "STEP_UP":
        # The hold stays until a person approves or denies (M5).
        await _move(
            session, payment, PaymentStatus.AWAITING_APPROVAL, now, event_type="approval.requested"
        )
        await session.commit()
        return _response(payment, "STEP_UP", reasons, risk_score)
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
        return await _fail(
            session, payment, PaymentStatus.FAILED, AUTHORIZE_FAILED, now, risk_score
        )

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
            return await _fail(session, payment, None, CAPTURE_FAILED, now, risk_score)
        return await _fail(session, payment, PaymentStatus.VOIDED, CAPTURE_FAILED, now, risk_score)

    await _move(session, payment, PaymentStatus.CAPTURED, now, capture_status=status)
    await session.commit()
    return _response(payment, "ALLOW", [], risk_score)


async def _score_risk(
    sessions: async_sessionmaker[AsyncSession], payment: Payment, now: datetime, window: int
) -> AnomalyResult | None:
    """The anomaly result, or None if it could not be computed (the caller then uses STEP_UP)."""
    try:
        # Its own read-only session: nothing is locked while the features are read.
        async with sessions() as read:
            features = await build_features(
                read,
                payment_id=payment.id,
                mandate_id=payment.mandate_id,
                merchant_id=payment.merchant_id,
                amount=payment.amount,
                now=now,
                window_seconds=window,
            )
        risk = score_anomaly(features)
        if not 0.0 <= risk.score <= 1.0:  # also catches NaN
            raise ValueError(f"risk score {risk.score} is outside 0..1")
        return risk
    except Exception:
        log.exception("anomaly check failed for payment %s", payment.id)
        return None


def _combine(risk: AnomalyResult | None, settings: Settings) -> tuple[str, list[dict]]:
    """CONTRACTS 5.2 after a policy ALLOW: the model can only make the outcome stricter (D2)."""
    if risk is None:
        return "STEP_UP", [ANOMALY_FAILED]
    message = f"Risk score {risk.score:.2f}"
    message += f": {'; '.join(risk.reasons)}." if risk.reasons else "."
    if risk.score >= settings.anomaly_block_threshold:
        return "BLOCK", [{"code": "anomaly_block", "message": message}]
    if risk.score >= settings.anomaly_step_up_threshold:
        return "STEP_UP", [{"code": "anomaly_step_up", "message": message}]
    return "ALLOW", []


async def _move(
    session: AsyncSession,
    payment: Payment,
    target: PaymentStatus,
    now: datetime,
    event_type: str | None = None,
    **payload,
) -> None:
    payment.status = transition(PaymentStatus(payment.status), target)
    await append_audit(
        session,
        event_type=event_type or f"payment.{target.lower()}",
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
    risk_score: str | None,
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
    return _response(payment, "BLOCK", [reason], risk_score)
