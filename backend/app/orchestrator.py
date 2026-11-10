"""Decision orchestrator: price the cart, policy check, then PayPal authorize + capture, all
recorded in Postgres.

Leash prices every item from its own catalog (app.catalog); the agent sends no price or amount.
After the policy ALLOW, the intent judge (app.judge) and the anomaly model (app.anomaly) run in
parallel and can still turn it into STEP_UP or BLOCK. Every request ends in ALLOW, STEP_UP or BLOCK.
Any error is a BLOCK, except an error in the judge or the risk check, which is a STEP_UP (D3).
Call order and release points follow docs/CONTRACTS.md section 5.3. Status changes go through
`app.state.transition`, and each one writes an audit event through `append_audit`.
"""

import asyncio
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.anomaly import AnomalyResult, build_features, score_anomaly
from app.audit import append_audit
from app.catalog import Cart, Product, get_catalog, price_cart
from app.config import Settings, get_settings
from app.judge import Cart as JudgeCart
from app.judge import CartItem, JudgeResult, judge_intent
from app.mandates import (
    MandateRecord,
    check_and_reserve,
    get_mandate,
    release_budget,
    verify_mandate,
)
from app.models import Payment
from app.paypal import PayPalClient
from app.policy import PaymentRequest
from app.read_api import _time
from app.state import PaymentStatus, transition

log = logging.getLogger(__name__)

AUTHORIZE_FAILED = {"code": "paypal_authorize_failed", "message": "PayPal did not authorize."}
CAPTURE_FAILED = {"code": "paypal_capture_failed", "message": "PayPal did not capture."}
INTERNAL_ERROR = {"code": "internal_error", "message": "Leash could not complete its checks."}
ANOMALY_FAILED = {
    "code": "anomaly_check_failed",
    "message": "The risk check did not complete, so a person must approve this payment.",
}
JUDGE_FAILED = {
    "code": "intent_check_failed",
    "message": "The intent check did not complete, so a person must approve this payment.",
}


def _response(
    payment: Payment | None,
    decision: str,
    reasons: list[dict],
    risk_score: str | None = None,
    judge: dict | None = None,
    approval_url: str | None = None,
    approval_expires_at: str | None = None,
) -> dict:
    return {
        "payment_id": payment.id if payment else None,
        "decision": decision,
        "status": payment.status if payment else None,
        "reasons": reasons,
        "risk_score": risk_score,
        "judge": judge,
        "approval_url": approval_url,
        "approval_expires_at": approval_expires_at,
    }


def approval_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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
    products = get_catalog()
    cart = price_cart(products, merchant_id, items)
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

    # check_and_reserve loaded this row, so this reads no database; the judge needs the intent.
    record = await get_mandate(session, mandate_id)
    # Record the ALLOW and its budget hold before any money moves.
    await session.commit()

    # 5.3 step 2, with no transaction open: the judge and the model run at the same time.
    judged, risk = await asyncio.gather(
        _judge(payment, record, products, cart),
        _score_risk(sessions, payment, now, settings.velocity_window_seconds),
    )
    outcome, reasons = _combine(judged, risk, settings)
    risk_score = None if risk is None else f"{risk.score:.6f}"
    judge = None if judged is None else _judge_view(judged)
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
            "judge": judge,
        },
        now=now,
    )
    if outcome == "BLOCK":
        await _move(session, payment, PaymentStatus.BLOCKED, now)
        await session.commit()
        return _response(payment, "BLOCK", reasons, risk_score, judge)
    if outcome == "STEP_UP":
        # The hold stays until a person approves or denies, or the request expires
        # (app/approvals.py). The raw token goes only into this response.
        token = secrets.token_urlsafe(32)
        payment.approval_token_hash = approval_token_hash(token)
        payment.approval_expires_at = now + timedelta(seconds=settings.approval_ttl_seconds)
        expires_at = _time(payment.approval_expires_at)
        await _move(
            session,
            payment,
            PaymentStatus.AWAITING_APPROVAL,
            now,
            event_type="approval.requested",
            approval_expires_at=expires_at,
        )
        await session.commit()
        base = settings.public_base_url.rstrip("/")
        return _response(
            payment,
            "STEP_UP",
            reasons,
            risk_score,
            judge,
            approval_url=f"{base}/approvals/{payment.id}?token={token}",
            approval_expires_at=expires_at,
        )
    await session.commit()
    return await authorize_and_capture(session, payment, paypal, vault_id, now, risk_score, judge)


async def authorize_and_capture(
    session: AsyncSession,
    payment: Payment,
    paypal: PayPalClient,
    vault_id: str,
    now: datetime,
    risk_score: str | None = None,
    judge: dict | None = None,
) -> dict:
    """CONTRACTS 5.3 steps 4 and 5, for an allowed payment and for an approved STEP_UP."""
    try:
        if not vault_id:
            raise ValueError("PAYPAL_VAULT_ID is not set")
        auth = await paypal.authorize(
            vault_id=vault_id,
            amount=payment.amount,
            request_id=payment.idempotency_key,
            custom_id=payment.merchant_id,
            # Same description as reconcile's replay of this call (app/reconcile.py).
            description=payment.items,
        )
    except Exception:
        log.exception("authorize failed for payment %s", payment.id)
        return await _fail(
            session, payment, PaymentStatus.FAILED, AUTHORIZE_FAILED, now, risk_score, judge
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
            return await _fail(session, payment, None, CAPTURE_FAILED, now, risk_score, judge)
        return await _fail(
            session, payment, PaymentStatus.VOIDED, CAPTURE_FAILED, now, risk_score, judge
        )

    await _move(session, payment, PaymentStatus.CAPTURED, now, capture_status=status)
    await session.commit()
    return _response(payment, "ALLOW", [], risk_score, judge)


def _judge_cart(products: dict[str, Product], merchant_id: str, cart: Cart) -> JudgeCart:
    # Names come from the catalog, so the judge never reads text the agent wrote.
    return JudgeCart(
        merchant_id=merchant_id,
        items=tuple(
            CartItem(
                name=products[line["sku"]].name,
                quantity=line["quantity"],
                unit_price=products[line["sku"]].price,
            )
            for line in cart.lines
        ),
        total=cart.amount,
    )


async def _judge(
    payment: Payment, record: MandateRecord | None, products: dict[str, Product], cart: Cart
) -> JudgeResult | None:
    """The judge's verdict, or None if it could not be reached (the caller then uses STEP_UP)."""
    try:
        mandate = None if record is None else verify_mandate(record).mandate
        if mandate is None:
            raise ValueError(f"mandate {payment.mandate_id!r} did not verify")
        judged = await judge_intent(
            intent=record.terms.intent,
            mandate=mandate,
            cart=_judge_cart(products, payment.merchant_id, cart),
        )
        if not 0.0 <= judged.score <= 1.0:  # also catches NaN
            raise ValueError(f"judge score {judged.score} is outside 0..1")
        return judged
    except Exception:
        log.exception("intent judge failed for payment %s", payment.id)
        return None


def _judge_view(judged: JudgeResult) -> dict:
    # A string score, like risk_score: canonical JSON (the audit chain) refuses floats.
    return {
        "match": judged.match,
        "score": f"{judged.score:.6f}",
        "reason": judged.reason,
        "model": judged.model,
    }


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


def _combine(
    judged: JudgeResult | None, risk: AnomalyResult | None, settings: Settings
) -> tuple[str, list[dict]]:
    """CONTRACTS 5.2 after a policy ALLOW: the judge and the model only make it stricter (D2).

    Anomaly BLOCK wins over any STEP_UP. Every STEP_UP reason is kept, so the approver sees all.
    """
    if risk is not None:
        message = f"Risk score {risk.score:.2f}"
        message += f": {'; '.join(risk.reasons)}." if risk.reasons else "."
        if risk.score >= settings.anomaly_block_threshold:
            return "BLOCK", [{"code": "anomaly_block", "message": message}]
    reasons = []
    if judged is None:
        reasons.append(JUDGE_FAILED)
    elif not judged.match:
        reasons.append({"code": "intent_mismatch", "message": f"Intent check: {judged.reason}"})
    if risk is None:
        reasons.append(ANOMALY_FAILED)
    elif risk.score >= settings.anomaly_step_up_threshold:
        reasons.append({"code": "anomaly_step_up", "message": message})
    return ("STEP_UP" if reasons else "ALLOW"), reasons


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
    judge: dict | None,
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
    return _response(payment, "BLOCK", [reason], risk_score, judge)
