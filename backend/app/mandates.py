"""Mandates: the user's signed spending terms (docs/CONTRACTS.md section 1).

The terms are signed with Ed25519 when a mandate is created; the state (revoked, usage) is not.
`verify_mandate` checks the signature and turns a stored record into a `policy.Mandate`.
Expiry, revocation, budget, count and velocity are checked by `policy.evaluate_payment`.

`check_and_reserve` and `release_budget` hold and release budget atomically (section 2). Every
function here takes the caller's session and never commits; the orchestrator owns the transaction.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, Inexact, InvalidOperation

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import append_audit
from app.canonical import canonical_json
from app.config import get_settings
from app.models import BudgetHold, MandateRow, Payment
from app.policy import (
    Mandate,
    PaymentRequest,
    PolicyDecision,
    PolicyOutcome,
    PolicyReason,
    UsageSnapshot,
    VelocityPolicy,
    evaluate_payment,
)
from app.state import PaymentStatus

TERMS_VERSION = 1
_CENTS = Decimal("0.01")
_EXACT = Context(prec=100, traps=[Inexact, InvalidOperation])

SIGNATURE_INVALID = PolicyReason(
    code="mandate_signature_invalid", message="Mandate signature was not verified as valid."
)
RECORD_INVALID = PolicyReason(
    code="invalid_mandate", message="Spending mandate is missing or malformed."
)
NOT_FOUND = PolicyReason(code="mandate_not_found", message="No mandate with this ID exists.")

# Payments that no longer hold or take money; every other status counts towards velocity.
_ENDED_WITHOUT_MONEY = (
    PaymentStatus.BLOCKED,
    PaymentStatus.DENIED,
    PaymentStatus.EXPIRED,
    PaymentStatus.FAILED,
    PaymentStatus.VOIDED,
)
# Column limits in migration 0002: ids and category String(64), intent String(500), Numeric(12, 2).
_MAX_ID = 64
_MAX_INTENT = 500
_MAX_AMOUNT = Decimal("9999999999.99")


class MandateNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class MandateTerms:
    """What the user approved. Every field is covered by the signature."""

    mandate_id: str
    intent: str  # the user's own words, for the intent judge
    category: str
    max_payment_amount: Decimal
    total_budget: Decimal
    max_payment_count: int
    allowed_merchant_ids: frozenset[str]
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MandateRecord:
    """A stored mandate: signed terms plus unsigned state."""

    terms: MandateTerms
    signature: str  # base64 Ed25519 signature over `terms_bytes(terms)`
    revoked_at: datetime | None
    budget_used: Decimal
    payment_count: int


@dataclass(frozen=True, slots=True)
class MandateCheck:
    signature_valid: bool
    mandate: Mandate | None
    reasons: tuple[PolicyReason, ...]


def terms_bytes(terms: MandateTerms) -> bytes:
    """The exact bytes that are signed. Raises TypeError or ValueError on malformed terms."""
    if not isinstance(terms, MandateTerms):
        raise TypeError("terms must be MandateTerms")
    if not isinstance(terms.allowed_merchant_ids, frozenset):
        raise TypeError("allowed_merchant_ids must be a frozenset")
    if not isinstance(terms.max_payment_count, int) or isinstance(terms.max_payment_count, bool):
        raise TypeError("max_payment_count must be an int")
    return canonical_json(
        {
            "version": TERMS_VERSION,
            "mandate_id": terms.mandate_id,
            "intent": terms.intent,
            "category": terms.category,
            "max_payment_amount": _cents(terms.max_payment_amount),
            "total_budget": _cents(terms.total_budget),
            "max_payment_count": terms.max_payment_count,
            "allowed_merchant_ids": sorted(terms.allowed_merchant_ids),
            "expires_at": terms.expires_at,
            "created_at": terms.created_at,
        }
    )


def sign_terms(terms: MandateTerms, private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.sign(terms_bytes(terms))).decode("ascii")


def verify_signature(terms: MandateTerms, signature: str, public_key: Ed25519PublicKey) -> bool:
    """True only if `signature` is a valid Ed25519 signature over these exact terms."""
    try:
        raw = base64.b64decode(signature, validate=True)
        public_key.verify(raw, terms_bytes(terms))
    except (InvalidSignature, binascii.Error, TypeError, ValueError):
        return False
    return True


def verify_mandate(
    record: MandateRecord, *, public_key: Ed25519PublicKey | None = None
) -> MandateCheck:
    """Check the signature and parse the record. Never raises; any problem fails closed.

    `public_key` defaults to the one derived from MANDATE_SIGNING_KEY.
    """
    try:
        key = public_key or load_signing_key(get_settings().mandate_signing_key).public_key()
        signature_valid = verify_signature(record.terms, record.signature, key)
        mandate = _to_policy_mandate(record)
    except Exception:
        return MandateCheck(signature_valid=False, mandate=None, reasons=(RECORD_INVALID,))
    reasons = () if signature_valid else (SIGNATURE_INVALID,)
    return MandateCheck(signature_valid=signature_valid, mandate=mandate, reasons=reasons)


def generate_signing_key() -> str:
    """A new base64 Ed25519 private key for MANDATE_SIGNING_KEY."""
    raw = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    return base64.b64encode(raw).decode("ascii")


def load_signing_key(value: str) -> Ed25519PrivateKey:
    """Parse MANDATE_SIGNING_KEY. Raises ValueError if it is missing or malformed."""
    if not value:
        raise ValueError("MANDATE_SIGNING_KEY is not set")
    try:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("MANDATE_SIGNING_KEY is not a base64 Ed25519 private key") from exc


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def _cents(value: Decimal) -> str:
    # Amounts are signed as exact cents ("200.00"); anything finer cannot be signed.
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError("amounts must be finite Decimals")
    try:
        return str(value.quantize(_CENTS, context=_EXACT))
    except ArithmeticError as exc:
        raise ValueError("amounts must be exact cents") from exc


def _to_policy_mandate(record: MandateRecord) -> Mandate:
    terms = record.terms
    return Mandate(
        mandate_id=terms.mandate_id,
        category=terms.category,
        max_payment_amount=terms.max_payment_amount,
        total_budget=terms.total_budget,
        max_payment_count=terms.max_payment_count,
        allowed_merchant_ids=terms.allowed_merchant_ids,
        expires_at=terms.expires_at,
        revoked=record.revoked_at is not None,
    )


# --- database (migration 0002) ---------------------------------------------


async def create_mandate(session: AsyncSession, terms: MandateTerms) -> MandateRecord:
    """Validate, sign with MANDATE_SIGNING_KEY, store a new mandate and audit `mandate.created`.

    Raises ValueError on bad terms, an existing mandate_id or a missing signing key.
    """
    problems = validate_terms(terms)
    if problems:
        raise ValueError("invalid mandate terms: " + "; ".join(problems))
    if await session.get(MandateRow, terms.mandate_id) is not None:
        raise ValueError(f"mandate {terms.mandate_id!r} already exists")
    key = load_signing_key(get_settings().mandate_signing_key)
    row = MandateRow(
        id=terms.mandate_id,
        intent=terms.intent,
        category=terms.category,
        max_payment_amount=terms.max_payment_amount,
        total_budget=terms.total_budget,
        max_payment_count=terms.max_payment_count,
        allowed_merchant_ids=sorted(terms.allowed_merchant_ids),
        # Stored in UTC: SQLite would otherwise keep the local wall time and drop the offset.
        expires_at=terms.expires_at.astimezone(UTC),
        created_at=terms.created_at.astimezone(UTC),
        signature=sign_terms(terms, key),
        revoked_at=None,
        budget_used=Decimal("0"),
        payment_count=0,
    )
    session.add(row)
    await session.flush()
    await append_audit(
        session,
        event_type="mandate.created",
        payment_id=None,
        payload={
            "mandate_id": terms.mandate_id,
            "intent": terms.intent,
            "category": terms.category,
            "max_payment_amount": terms.max_payment_amount,
            "total_budget": terms.total_budget,
            "max_payment_count": terms.max_payment_count,
            "allowed_merchant_ids": sorted(terms.allowed_merchant_ids),
            "expires_at": terms.expires_at,
            "signature": row.signature,
        },
        now=terms.created_at,
    )
    return _to_record(row)


async def revoke_mandate(session: AsyncSession, mandate_id: str, now: datetime) -> MandateRecord:
    """Mark a mandate revoked and audit `mandate.revoked`. Revoking twice keeps the first time
    and writes no second entry. Raises MandateNotFound."""
    row = await _locked(session, mandate_id)
    if row is None:
        raise MandateNotFound(mandate_id)
    if row.revoked_at is None:
        row.revoked_at = _to_utc(now)
        await session.flush()
        await append_audit(
            session,
            event_type="mandate.revoked",
            payment_id=None,
            payload={"mandate_id": mandate_id},
            now=now,
        )
    return _to_record(row)


async def get_mandate(session: AsyncSession, mandate_id: str) -> MandateRecord | None:
    row = await session.get(MandateRow, mandate_id)
    return None if row is None else _to_record(row)


async def list_mandates(session: AsyncSession) -> list[MandateRecord]:
    rows = await session.scalars(select(MandateRow).order_by(MandateRow.created_at, MandateRow.id))
    return [_to_record(row) for row in rows]


async def check_and_reserve(
    session: AsyncSession,
    *,
    payment_id: str,
    request: PaymentRequest,
    mandate_id: str,
    now: datetime,
) -> PolicyDecision:
    """Check the payment against its mandate and, on ALLOW only, hold the budget.

    The mandate row is locked first, so two payments on one mandate are checked one at a time.
    Raises if `payment_id` already has a hold (a retry must not hold twice).
    """
    row = await _locked(session, mandate_id)
    if row is None:
        return PolicyDecision(outcome=PolicyOutcome.BLOCK, reasons=(NOT_FOUND,))
    check = verify_mandate(_to_record(row))
    if check.mandate is None:
        return PolicyDecision(outcome=PolicyOutcome.BLOCK, reasons=check.reasons)

    settings = get_settings()
    window = timedelta(seconds=settings.velocity_window_seconds)
    recent = await session.scalar(
        select(func.count()).where(
            Payment.mandate_id == mandate_id,
            Payment.id != payment_id,
            Payment.created_at >= now - window,
            Payment.status.not_in(_ENDED_WITHOUT_MONEY),
        )
    )
    decision = evaluate_payment(
        request=request,
        mandate=check.mandate,
        usage=UsageSnapshot(
            budget_used=row.budget_used,
            payment_count=row.payment_count,
            recent_payment_count=recent,
        ),
        velocity_policy=VelocityPolicy(max_payments=settings.velocity_max_payments, window=window),
        mandate_signature_valid=check.signature_valid,
        now=now,
    )
    if not decision.is_allowed:
        return decision

    if await session.get(BudgetHold, payment_id) is not None:
        raise ValueError(f"payment {payment_id!r} already has a budget hold")
    row.budget_used = row.budget_used + request.amount
    row.payment_count = row.payment_count + 1
    session.add(
        BudgetHold(
            payment_id=payment_id,
            mandate_id=mandate_id,
            amount=request.amount,
            created_at=now.astimezone(UTC),
            released_at=None,
        )
    )
    await session.flush()
    return decision


async def release_budget(session: AsyncSession, *, payment_id: str, now: datetime) -> bool:
    """Give a held payment's budget back. False if there is no hold or it was already released."""
    hold = await session.get(BudgetHold, payment_id)
    if hold is None or hold.released_at is not None:
        return False
    # Lock in the same order as check_and_reserve (mandate first), then re-read the hold.
    row = await _locked(session, hold.mandate_id)
    hold = await session.get(BudgetHold, payment_id, with_for_update=True, populate_existing=True)
    if row is None or hold is None or hold.released_at is not None:
        return False
    if row.budget_used < hold.amount or row.payment_count < 1:
        raise ValueError(f"mandate {row.id!r} usage is lower than the hold being released")
    row.budget_used = row.budget_used - hold.amount
    row.payment_count = row.payment_count - 1
    hold.released_at = _to_utc(now)
    await session.flush()
    return True


def validate_terms(terms: MandateTerms) -> list[str]:
    """Problems that would make a mandate unusable or unsignable; empty when the terms are fine."""
    if not isinstance(terms, MandateTerms):
        return ["terms must be MandateTerms"]
    problems = []
    for name, value, limit in (
        ("mandate_id", terms.mandate_id, _MAX_ID),
        ("category", terms.category, _MAX_ID),
        ("intent", terms.intent, _MAX_INTENT),
    ):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            problems.append(f"{name} must be a non-empty string without surrounding spaces")
        elif len(value) > limit:
            problems.append(f"{name} must be at most {limit} characters")
    for name in ("max_payment_amount", "total_budget"):
        value = getattr(terms, name)
        try:
            _cents(value)
        except (TypeError, ValueError):
            problems.append(f"{name} must be a Decimal with at most 2 decimal places")
            continue
        if not Decimal("0") < value <= _MAX_AMOUNT:
            problems.append(f"{name} must be more than 0 and at most {_MAX_AMOUNT}")
    count = terms.max_payment_count
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        problems.append("max_payment_count must be a positive integer")
    merchants = terms.allowed_merchant_ids
    if not isinstance(merchants, frozenset) or not merchants:
        problems.append("allowed_merchant_ids must be a non-empty frozenset")
    elif not all(
        isinstance(m, str) and m.strip() == m and 0 < len(m) <= _MAX_ID for m in merchants
    ):
        problems.append(f"allowed_merchant_ids must be non-empty strings of at most {_MAX_ID}")
    dates_ok = True
    for name in ("expires_at", "created_at"):
        value = getattr(terms, name)
        if not isinstance(value, datetime) or value.utcoffset() is None:
            problems.append(f"{name} must be a timezone-aware datetime")
            dates_ok = False
    if dates_ok and terms.expires_at <= terms.created_at:
        problems.append("expires_at must be after created_at")
    return problems


async def _locked(session: AsyncSession, mandate_id: str) -> MandateRow | None:
    # SELECT ... FOR UPDATE on Postgres; SQLite (tests) has no row locks and ignores it.
    return await session.get(MandateRow, mandate_id, with_for_update=True, populate_existing=True)


def _to_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _utc(value: datetime | None) -> datetime | None:
    # SQLite returns DateTime(timezone=True) columns without tzinfo; everything is stored in UTC.
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _to_record(row: MandateRow) -> MandateRecord:
    return MandateRecord(
        terms=MandateTerms(
            mandate_id=row.id,
            intent=row.intent,
            category=row.category,
            max_payment_amount=row.max_payment_amount,
            total_budget=row.total_budget,
            max_payment_count=row.max_payment_count,
            allowed_merchant_ids=frozenset(row.allowed_merchant_ids),
            expires_at=_utc(row.expires_at),
            created_at=_utc(row.created_at),
        ),
        signature=row.signature,
        revoked_at=_utc(row.revoked_at),
        budget_used=row.budget_used,
        payment_count=row.payment_count,
    )
