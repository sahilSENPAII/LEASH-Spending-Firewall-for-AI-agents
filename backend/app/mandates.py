"""Mandates: the user's signed spending terms (docs/CONTRACTS.md section 1).

The terms are signed with Ed25519 when a mandate is created; the state (revoked, usage) is not.
`verify_mandate` checks the signature and turns a stored record into a `policy.Mandate`.
Expiry, revocation, budget, count and velocity are checked by `policy.evaluate_payment`.

Part 1 has no database: create/revoke/list and `check_and_reserve` come with migration 0002.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
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

from app.canonical import canonical_json
from app.config import get_settings
from app.policy import Mandate, PolicyReason

TERMS_VERSION = 1
_CENTS = Decimal("0.01")
_EXACT = Context(prec=100, traps=[Inexact, InvalidOperation])

SIGNATURE_INVALID = PolicyReason(
    code="mandate_signature_invalid", message="Mandate signature was not verified as valid."
)
RECORD_INVALID = PolicyReason(
    code="invalid_mandate", message="Spending mandate is missing or malformed."
)


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
