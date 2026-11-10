"""Step-up approvals (docs/CONTRACTS.md 5.8): a person approves or denies a STEP_UP payment.

GET  /approvals/{payment_id}?token=    the approval page (never changes state)
POST /approvals/{payment_id}/approve   continue to PayPal (5.3 step 4)
POST /approvals/{payment_id}/deny      DENIED, budget released

The link's token is the only credential. Only its SHA-256 is stored, and a wrong or missing token
gets a bare 404. Everything lives in Postgres, so a pending approval survives a restart.
"""

import asyncio
import hmac
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import orchestrator
from app.audit import append_audit
from app.catalog import get_catalog
from app.config import get_settings
from app.db import get_sessionmaker
from app.mandates import get_mandate, release_budget, verify_mandate
from app.mcp_server import get_paypal
from app.models import Payment
from app.paypal import PayPalClient
from app.read_api import _decided
from app.reconcile import REPLAY_WINDOW
from app.state import PaymentStatus
from app.times import as_utc, require_utc

log = logging.getLogger(__name__)
router = APIRouter()

EXPIRY_INTERVAL_SECONDS = 60
MANDATE_INVALID = {
    "code": "mandate_no_longer_valid",
    "message": "The mandate was revoked, expired or changed after this payment was requested,"
    " so it was not paid.",
}
NOT_COMPLETED = {
    "code": "approval_not_completed",
    "message": "The payment was approved but never reached PayPal, so it was not paid.",
}


class _RedactToken(logging.Filter):
    """Keeps the approval token out of the access log (it travels in the link's query string)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                re.sub(r"(token=)[^&\s]+", r"\1[redacted]", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactToken())


def _now() -> datetime:
    return datetime.now(UTC)


def _token_ok(payment: Payment | None, token: str) -> bool:
    if payment is None or not payment.approval_token_hash or not token:
        return False
    return hmac.compare_digest(
        orchestrator.approval_token_hash(token).encode(), payment.approval_token_hash.encode()
    )


def _pending(payment: Payment) -> bool:
    return payment.status == PaymentStatus.AWAITING_APPROVAL and payment.approval_decided_at is None


def _due(payment: Payment, now: datetime) -> bool:
    # No expiry time: a STEP_UP from before approvals existed. Nobody can answer it.
    return payment.approval_expires_at is None or as_utc(payment.approval_expires_at) <= now


async def _close(
    session: AsyncSession,
    payment: Payment,
    target: PaymentStatus,
    decision: str,
    now: datetime,
    **payload,
) -> None:
    """End a pending approval without paying: DENIED or EXPIRED, and the budget goes back."""
    payment.approval_decided_at = now
    payment.approval_decision = decision
    # Release before the audit write: mandate row lock, then chain lock (CONTRACTS section 3).
    await release_budget(session, payment_id=payment.id, now=now)
    await orchestrator._move(
        session, payment, target, now, event_type=f"approval.{decision}", **payload
    )
    await session.commit()


async def expire_due_approvals(sessions: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """Mark undecided approvals past their expiry EXPIRED and release their budget.

    Each payment gets its own transaction; one failure does not stop the others. Returns how many
    were expired.
    """
    now = require_utc(now)
    async with sessions() as session:
        due = (
            await session.scalars(
                select(Payment.id).where(
                    Payment.status == PaymentStatus.AWAITING_APPROVAL,
                    Payment.approval_decided_at.is_(None),
                    or_(Payment.approval_expires_at <= now, Payment.approval_expires_at.is_(None)),
                )
            )
        ).all()
    expired = 0
    for payment_id in due:
        try:
            async with sessions() as session:
                payment = await session.get(Payment, payment_id, with_for_update=True)
                # Checked again under the lock: an approve or deny may have won.
                if _pending(payment) and _due(payment, now):
                    await _close(session, payment, PaymentStatus.EXPIRED, "expired", now)
                    expired += 1
        except Exception:
            log.exception("could not expire approval for payment %s", payment_id)
    return expired


def _stuck(payment: Payment, cutoff: datetime) -> bool:
    return (
        payment.status == PaymentStatus.AWAITING_APPROVAL
        and payment.approval_decision == "approved"
        and as_utc(payment.approval_decided_at) <= cutoff
    )


async def settle_stuck_approvals(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalClient, vault_id: str, now: datetime
) -> int:
    """Finish approved payments whose approve request died before PayPal's answer was recorded.

    PayPal is asked for the order by replaying the create call with the payment's idempotency
    key (as app/reconcile.py does); `paypal.authorize` is never called again. An order that was
    already there is captured. Otherwise the payment is FAILED and its budget released. Anything
    unclear raises and leaves the payment for the next run. Returns how many were settled.
    """
    now = require_utc(now)
    cutoff = now - timedelta(seconds=get_settings().approval_stuck_seconds)
    async with sessions() as session:
        stuck = (
            await session.scalars(
                select(Payment.id).where(
                    Payment.status == PaymentStatus.AWAITING_APPROVAL,
                    Payment.approval_decision == "approved",
                    Payment.approval_decided_at <= cutoff,
                )
            )
        ).all()
    settled = 0
    for payment_id in stuck:
        try:
            async with sessions() as session:
                # The payment row stays locked during the lookup, so no second run can settle it
                # too. No mandate or audit-chain lock is held across the PayPal call.
                payment = await session.get(Payment, payment_id, with_for_update=True)
                if not _stuck(payment, cutoff):
                    continue
                if not vault_id:
                    raise ValueError("PAYPAL_VAULT_ID is not set")
                if now - as_utc(payment.approval_decided_at) >= REPLAY_WINDOW:
                    raise ValueError("too old to look up by idempotency key; check by hand")
                lookup = await paypal.find_order(
                    vault_id=vault_id,
                    amount=payment.amount,
                    request_id=payment.idempotency_key,
                    currency=payment.currency,
                    custom_id=payment.merchant_id,
                    description=payment.items,
                )
                if lookup.authorization_id is not None and not lookup.created:
                    await orchestrator.capture_authorized(
                        session, payment, paypal, lookup.order_id, lookup.authorization_id, now
                    )
                else:
                    if lookup.authorization_id is not None:
                        # The lookup itself placed this hold; the payment never had one.
                        # shortcut: if this void fails, the next run finds the hold as an existing
                        # order and captures it; save the ids first (as reconcile does) to fix.
                        await paypal.void(
                            lookup.authorization_id, f"{payment.idempotency_key}-void"
                        )
                    await orchestrator._fail(
                        session, payment, PaymentStatus.FAILED, NOT_COMPLETED, now, None, None
                    )
                settled += 1
        except Exception:
            log.exception("could not settle approved payment %s", payment_id)
    return settled


async def expiry_loop() -> None:
    """Runs for the life of the app (app.main lifespan). The first pass is at startup, which
    covers approvals that ran out while the server was down."""
    while True:
        try:
            await expire_due_approvals(get_sessionmaker(), _now())
        except Exception:
            log.exception("approval expiry run failed")
        try:
            await settle_stuck_approvals(
                get_sessionmaker(), get_paypal(), get_settings().paypal_vault_id, _now()
            )
        except Exception:
            log.exception("stuck approval run failed")
        await asyncio.sleep(EXPIRY_INTERVAL_SECONDS)


async def _mandate_problem(session: AsyncSession, payment: Payment, now: datetime) -> dict | None:
    record = await get_mandate(session, payment.mandate_id)
    if (
        record is None
        or not verify_mandate(record).signature_valid
        or record.revoked_at is not None
        or as_utc(record.terms.expires_at) <= now
    ):
        return MANDATE_INVALID
    return None


async def decide(
    sessions: async_sessionmaker[AsyncSession],
    paypal: PayPalClient,
    vault_id: str,
    *,
    payment_id: str,
    token: str,
    approve: bool,
    now: datetime,
) -> Payment | None:
    """Apply a person's answer once. None means unknown payment or wrong token.

    A payment that is already decided or expired is returned unchanged, so a second click or a
    concurrent request does nothing.
    """
    now = require_utc(now)
    await expire_due_approvals(sessions, now)
    async with sessions() as session:
        payment = await session.get(Payment, payment_id, with_for_update=True)
        if not _token_ok(payment, token):
            return None
        if not _pending(payment):
            return payment
        if _due(payment, now):
            await _close(session, payment, PaymentStatus.EXPIRED, "expired", now)
            return payment
        problem = await _mandate_problem(session, payment, now) if approve else None
        if problem:
            payment.reasons = [*payment.reasons, problem]
        if not approve or problem:
            await _close(session, payment, PaymentStatus.DENIED, "denied", now, **(problem or {}))
            return payment
        payment.approval_decided_at = now
        payment.approval_decision = "approved"
        await append_audit(
            session,
            event_type="approval.approved",
            payment_id=payment.id,
            payload={"decision": "approved"},
            now=now,
        )
        # The decision is committed before any money moves, like the ALLOW in 5.3 step 3.
        await session.commit()
        await orchestrator.authorize_and_capture(session, payment, paypal, vault_id, now)
        return payment


async def _page(session: AsyncSession, payment: Payment, token: str, now: datetime) -> str:
    decided = (await _decided(session, [payment.id])).get(payment.id) or {}
    record = await get_mandate(session, payment.mandate_id)
    return render_page(
        payment, decided, record.terms.intent if record else "", get_catalog(), token, now
    )


def render_page(
    payment: Payment, decided: dict, intent: str, products: dict, token: str, now: datetime
) -> str:
    """The whole page as one HTML string. Every value passes through `escape`."""
    e = escape
    rows = ""
    for line in json.loads(payment.items or "[]"):
        product = products.get(line.get("sku"))
        price = line.get("unit_price")
        cost = f"${Decimal(price) * line['quantity']:.2f}" if price else ""
        rows += (
            f"<tr><td>{e(str(line.get('quantity')))} &times; "
            f"{e(product.name if product else str(line.get('sku')))}</td>"
            f"<td class=n>{e(cost)}</td></tr>"
        )
    reasons = "".join(f"<li>{e(str(r.get('message', '')))}</li>" for r in payment.reasons or [])
    checks = ""
    judge = decided.get("judge")
    if judge:
        checks += f"<li>Intent check: {e(str(judge.get('reason', '')))}</li>"
    if decided.get("risk_score") is not None:
        checks += f"<li>Risk score: {e(str(decided['risk_score']))}</li>"
    for reason in decided.get("anomaly_reasons") or []:
        checks += f"<li>Risk signal: {e(str(reason))}</li>"

    expires = as_utc(payment.approval_expires_at)
    if _pending(payment) and not _due(payment, now):
        minutes = max(1, int((expires - now).total_seconds() // 60))
        action = f"/approvals/{e(payment.id)}"
        hidden = f'<input type="hidden" name="token" value="{e(token)}">'
        footer = (
            f"<p>Expires at {e(expires.strftime('%H:%M'))} UTC (about {minutes} min).</p>"
            f'<form method="post" action="{action}/approve">{hidden}'
            '<button class="ok">Approve</button></form>'
            f'<form method="post" action="{action}/deny">{hidden}'
            '<button class="no">Deny</button></form>'
        )
    else:
        # A page read never writes: a pending payment past its expiry is only shown as expired.
        status = "EXPIRED" if _pending(payment) else payment.status
        if payment.status == PaymentStatus.AWAITING_APPROVAL and not _pending(payment):
            status = "APPROVED, payment in progress"
        footer = f'<p class="status">{e(str(status))}</p>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Leash approval</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#f4f4f5;color:#18181b}}
main{{max-width:480px;margin:0 auto;background:#fff;border-radius:12px;padding:20px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:20px 0 6px}}
table{{width:100%;border-collapse:collapse}} td{{padding:6px 0;border-bottom:1px solid #e4e4e7}}
.n{{text-align:right;white-space:nowrap}} .total td{{font-weight:700;border:0}}
ul{{margin:0;padding-left:20px}} li{{margin:4px 0}}
button{{width:100%;padding:14px;margin-top:10px;font-size:17px;border:0;border-radius:8px;
color:#fff}}
.ok{{background:#15803d}} .no{{background:#b91c1c}}
.status{{font-size:22px;font-weight:700;text-align:center;margin:20px 0 0}}
</style></head><body><main>
<h1>Approve this payment?</h1>
<p>Your AI agent wants to pay <strong>{e(payment.merchant_id)}</strong>.</p>
<table>{rows}
<tr class="total"><td>Total</td><td class=n>${payment.amount:.2f} {e(payment.currency)}</td></tr>
</table>
<h2>What you asked for</h2><p>{e(intent)}</p>
<h2>Why Leash stopped it</h2><ul>{reasons}</ul>
<h2>Checks</h2><ul>{checks}</ul>
{footer}
</main></body></html>"""


async def _answer(payment_id: str, request: Request, approve: bool) -> HTMLResponse:
    # Read by hand: FastAPI's Form() would need the python-multipart package.
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    token = (form.get("token") or [""])[0]
    sessions = get_sessionmaker()
    payment = await decide(
        sessions,
        get_paypal(),
        get_settings().paypal_vault_id,
        payment_id=payment_id,
        token=token,
        approve=approve,
        now=_now(),
    )
    if payment is None:
        raise HTTPException(status_code=404)
    async with sessions() as session:
        return HTMLResponse(await _page(session, payment, token, _now()))


@router.get("/approvals/{payment_id}", response_class=HTMLResponse)
async def approval_page(payment_id: str, token: str = "") -> HTMLResponse:
    async with get_sessionmaker()() as session:
        payment = await session.get(Payment, payment_id)
        if not _token_ok(payment, token):
            raise HTTPException(status_code=404)
        return HTMLResponse(await _page(session, payment, token, _now()))


@router.post("/approvals/{payment_id}/approve", response_class=HTMLResponse)
async def approve(payment_id: str, request: Request) -> HTMLResponse:
    return await _answer(payment_id, request, approve=True)


@router.post("/approvals/{payment_id}/deny", response_class=HTMLResponse)
async def deny(payment_id: str, request: Request) -> HTMLResponse:
    return await _answer(payment_id, request, approve=False)
