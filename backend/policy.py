"""Deterministic payment policy evaluator for Leash.

This module implements the first-stage, fail-closed policy firewall for AI agent
payment requests. It is pure, synchronous, and relies solely on the Python 3.12
standard library.

Important operational and trust boundaries:
- This module returns only ALLOW or BLOCK. It never returns STEP-UP and never
  performs LLM/ML evaluation, cryptographic signature verification, persistence,
  or payment processor calls.
- ``mandate_signature_valid`` must be supplied by a trusted upstream signature
  verifier (for example, server-side Ed25519 verification). It must never be
  controlled by the AI agent or defaulted to ``True``.
- ``request.category`` and ``request.merchant_id`` must be derived or validated
  from trusted server-side purchase/merchant context before calling this
  evaluator. The backend must not accept category or merchant claims
  uncritically from the AI agent or untrusted page content.
- ``evaluate_payment`` consumes a read-only ``UsageSnapshot`` and does not
  mutate its inputs. Because it operates on an in-memory snapshot without locks,
  it cannot by itself prevent race conditions across concurrent requests. The
  production Postgres integration must atomically check and reserve budget and
  payment counts inside a serializable transaction or row-level lock before a
  payment is authorized.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, Inexact
from enum import StrEnum
from typing import TypeGuard

SUPPORTED_CURRENCY = "USD"
MAX_DECIMAL_PLACES = 2
_HOURS_24 = timedelta(hours=24)
# Exact arithmetic for budget sums: any rounding raises Inexact instead of
# silently producing a wrong total (the default context keeps 28 digits).
_EXACT_CONTEXT = Context(prec=100, traps=[Inexact])


class PolicyOutcome(StrEnum):
    """Deterministic policy outcomes supported by this evaluator."""

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class PolicyReason:
    """A machine-readable failure code paired with a safe human-readable message."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The result of evaluating a payment request against a mandate."""

    outcome: PolicyOutcome
    reasons: tuple[PolicyReason, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, PolicyOutcome):
            raise TypeError("outcome must be a PolicyOutcome")
        if not isinstance(self.reasons, tuple) or not all(
            isinstance(reason, PolicyReason) for reason in self.reasons
        ):
            raise TypeError("reasons must be a tuple of PolicyReason instances")
        if self.outcome is PolicyOutcome.ALLOW and self.reasons:
            raise ValueError("ALLOW decisions must not contain failure reasons")
        if self.outcome is PolicyOutcome.BLOCK and not self.reasons:
            raise ValueError("BLOCK decisions must contain at least one failure reason")

    @property
    def is_allowed(self) -> bool:
        """Return True only when the decision outcome is ALLOW."""
        return self.outcome is PolicyOutcome.ALLOW


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    """An agent-initiated request to make a payment through Leash."""

    amount: Decimal
    currency: str
    category: str
    merchant_id: str


@dataclass(frozen=True, slots=True)
class Mandate:
    """User-defined spending constraints granted to an AI agent."""

    mandate_id: str
    category: str
    max_payment_amount: Decimal
    total_budget: Decimal
    max_payment_count: int
    allowed_merchant_ids: frozenset[str]
    expires_at: datetime
    revoked: bool


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Read-only point-in-time usage counters for a mandate.

    Attributes:
        budget_used: Total USD amount already spent or reserved against the mandate.
        payment_count: Total prior payments already counted or reserved against
            the mandate.
        recent_payment_count: Number of qualifying prior payments against the
            mandate within the caller-configured velocity window ending at ``now``.
    """

    budget_used: Decimal
    payment_count: int
    recent_payment_count: int


@dataclass(frozen=True, slots=True)
class VelocityPolicy:
    """Caller-supplied rate limit for payments within a sliding window."""

    max_payments: int
    window: timedelta


def _is_strict_int(value: object) -> TypeGuard[int]:
    """Return True if value is an int and not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_empty_token(value: object) -> TypeGuard[str]:
    """Return True if value is a non-empty string with no surrounding whitespace."""
    return (
        isinstance(value, str)
        and len(value) > 0
        and not value.isspace()
        and value == value.strip()
    )


def _is_valid_usd_decimal(
    value: object,
    *,
    allow_zero: bool,
) -> TypeGuard[Decimal]:
    """Return True if value is a finite USD Decimal representable in exact cents.

    Rejects floats, non-finite Decimals, negative numbers, signed zero, and any
    value with a non-zero fractional component smaller than one cent (0.01),
    while allowing harmless trailing zeros beyond the hundredths place (such as
    ``Decimal("1.230")`` or ``Decimal("0.000")`` when ``allow_zero=True``).
    """
    if not isinstance(value, Decimal) or not value.is_finite():
        return False
    if value.is_signed():
        return False
    if value == Decimal("0"):
        return allow_zero

    _, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        return False
    if exponent < -MAX_DECIMAL_PLACES:
        sub_cent_digits = -MAX_DECIMAL_PLACES - exponent
        if len(digits) <= sub_cent_digits or any(
            digit != 0 for digit in digits[-sub_cent_digits:]
        ):
            return False
    return True


def _exact_add(left: Decimal, right: Decimal) -> Decimal:
    """Add two Decimals exactly, independent of the caller's decimal context.

    Raises ``decimal.Inexact`` if the sum cannot be represented exactly; the
    public entry point turns that into a fail-closed BLOCK.
    """
    return _EXACT_CONTEXT.add(left, right)


def _to_utc_datetime(value: object) -> datetime | None:
    """Validate a timezone-aware datetime and normalize it to UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        offset = value.tzinfo.utcoffset(value)
    except Exception:
        return None
    if (
        not isinstance(offset, timedelta)
        or offset <= -_HOURS_24
        or offset >= _HOURS_24
    ):
        return None
    try:
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def _is_valid_positive_timedelta(value: object) -> TypeGuard[timedelta]:
    """Return True if value is a strictly positive timedelta."""
    return isinstance(value, timedelta) and value > timedelta(0)


def evaluate_payment(
    *,
    request: PaymentRequest,
    mandate: Mandate,
    usage: UsageSnapshot,
    velocity_policy: VelocityPolicy,
    mandate_signature_valid: bool | None,
    now: datetime,
) -> PolicyDecision:
    """Evaluate a payment request deterministically against a spending mandate.

    All applicable rules are checked in a stable order and every failure reason
    is collected before returning ``BLOCK``. ``ALLOW`` is returned only when
    every input is well-formed and all policy checks pass.

    Trust and provenance requirements:
    - ``mandate_signature_valid`` must come from a trusted upstream signature
      verifier and must be ``True`` for evaluation to succeed.
    - ``request.category`` and ``request.merchant_id`` must be derived or
      validated from trusted server-side purchase information; the backend must
      never accept those fields uncritically from the AI agent.

    Notes on concurrency and velocity:
    - ``usage.recent_payment_count`` must already represent the number of
      qualifying earlier payments recorded in the configured
      ``velocity_policy.window`` ending at ``now``. This evaluator checks
      whether adding the current payment (``recent_payment_count + 1``)
      exceeds ``velocity_policy.max_payments``.
    - ``usage`` is a read-only snapshot. This pure function does not mutate
      state and cannot prevent concurrent requests from racing; atomic
      reservation in Postgres is required in the persistence layer.
    """
    try:
        return _evaluate_payment_inner(
            request=request,
            mandate=mandate,
            usage=usage,
            velocity_policy=velocity_policy,
            mandate_signature_valid=mandate_signature_valid,
            now=now,
        )
    except Exception:
        return PolicyDecision(
            outcome=PolicyOutcome.BLOCK,
            reasons=(
                PolicyReason(
                    code="internal_evaluation_error",
                    message="Payment policy evaluation failed due to an internal error.",
                ),
            ),
        )


def _evaluate_payment_inner(
    *,
    request: PaymentRequest,
    mandate: Mandate,
    usage: UsageSnapshot,
    velocity_policy: VelocityPolicy,
    mandate_signature_valid: bool | None,
    now: datetime,
) -> PolicyDecision:
    reasons: list[PolicyReason] = []

    if not isinstance(request, PaymentRequest):
        reasons.append(
            PolicyReason(
                code="invalid_request",
                message="Payment request is missing or malformed.",
            )
        )
    if not isinstance(mandate, Mandate):
        reasons.append(
            PolicyReason(
                code="invalid_mandate",
                message="Spending mandate is missing or malformed.",
            )
        )
    if not isinstance(usage, UsageSnapshot):
        reasons.append(
            PolicyReason(
                code="invalid_usage_snapshot",
                message="Usage snapshot is missing or malformed.",
            )
        )
    if not isinstance(velocity_policy, VelocityPolicy):
        reasons.append(
            PolicyReason(
                code="invalid_velocity_policy",
                message="Velocity policy configuration is missing or malformed.",
            )
        )

    if reasons:
        return PolicyDecision(outcome=PolicyOutcome.BLOCK, reasons=tuple(reasons))

    # 1. Signature validity must be confirmed as exactly True.
    if mandate_signature_valid is not True:
        reasons.append(
            PolicyReason(
                code="mandate_signature_invalid",
                message="Mandate signature was not verified as valid.",
            )
        )

    # Validate mandate fields and structural integrity.
    if not _is_non_empty_token(mandate.mandate_id):
        reasons.append(
            PolicyReason(
                code="invalid_mandate_id",
                message="Mandate ID is missing or invalid.",
            )
        )

    mandate_category_valid = _is_non_empty_token(mandate.category)
    if not mandate_category_valid:
        reasons.append(
            PolicyReason(
                code="invalid_mandate_category",
                message="Mandate category is missing or invalid.",
            )
        )

    max_payment_amount_valid = _is_valid_usd_decimal(
        mandate.max_payment_amount, allow_zero=False
    )
    if not max_payment_amount_valid:
        reasons.append(
            PolicyReason(
                code="invalid_mandate_max_payment_amount",
                message="Mandate maximum payment amount must be a positive USD Decimal with at most 2 decimal places.",
            )
        )

    total_budget_valid = _is_valid_usd_decimal(
        mandate.total_budget, allow_zero=False
    )
    if not total_budget_valid:
        reasons.append(
            PolicyReason(
                code="invalid_mandate_total_budget",
                message="Mandate total budget must be a positive USD Decimal with at most 2 decimal places.",
            )
        )

    max_payment_count_valid = (
        _is_strict_int(mandate.max_payment_count) and mandate.max_payment_count > 0
    )
    if not max_payment_count_valid:
        reasons.append(
            PolicyReason(
                code="invalid_mandate_max_payment_count",
                message="Mandate maximum payment count must be a positive integer.",
            )
        )

    allowlist_type_valid = isinstance(mandate.allowed_merchant_ids, frozenset) and all(
        _is_non_empty_token(merchant_id)
        for merchant_id in mandate.allowed_merchant_ids
    )
    if not allowlist_type_valid:
        reasons.append(
            PolicyReason(
                code="invalid_mandate_merchant_allowlist",
                message="Mandate merchant allowlist must be a frozenset of valid merchant IDs.",
            )
        )

    # 2. Mandate revocation state.
    if not isinstance(mandate.revoked, bool):
        reasons.append(
            PolicyReason(
                code="invalid_mandate_revocation_state",
                message="Mandate revocation flag must be a boolean.",
            )
        )
    elif mandate.revoked:
        reasons.append(
            PolicyReason(
                code="mandate_revoked",
                message="Mandate has been revoked.",
            )
        )

    # 3. Mandate expiry and evaluation timestamp checks (compared in UTC).
    now_utc = _to_utc_datetime(now)
    if now_utc is None:
        reasons.append(
            PolicyReason(
                code="invalid_evaluation_time",
                message="Current evaluation timestamp must be a timezone-aware datetime.",
            )
        )

    expires_at_utc = _to_utc_datetime(mandate.expires_at)
    if expires_at_utc is None:
        reasons.append(
            PolicyReason(
                code="invalid_mandate_expiry",
                message="Mandate expiration timestamp must be a timezone-aware datetime.",
            )
        )

    if now_utc is not None and expires_at_utc is not None and expires_at_utc <= now_utc:
        reasons.append(
            PolicyReason(
                code="mandate_expired",
                message="Mandate has expired.",
            )
        )

    # 4 & 5. Request amount validation.
    request_amount_valid = _is_valid_usd_decimal(request.amount, allow_zero=False)
    if not request_amount_valid:
        reasons.append(
            PolicyReason(
                code="invalid_request_amount",
                message="Request amount must be a finite, positive Decimal with at most 2 decimal places.",
            )
        )

    # 4 & 6. Request currency validation.
    if not _is_non_empty_token(request.currency):
        reasons.append(
            PolicyReason(
                code="invalid_request_currency",
                message="Request currency is missing or invalid.",
            )
        )
    elif request.currency != SUPPORTED_CURRENCY:
        reasons.append(
            PolicyReason(
                code="unsupported_currency",
                message="Request currency must be exactly USD.",
            )
        )

    # 4 & 7. Request category validation and exact mandate category match.
    request_category_valid = _is_non_empty_token(request.category)
    if not request_category_valid:
        reasons.append(
            PolicyReason(
                code="invalid_request_category",
                message="Request category is missing or invalid.",
            )
        )
    elif mandate_category_valid and request.category != mandate.category:
        reasons.append(
            PolicyReason(
                code="category_mismatch",
                message="Request category does not match the mandate category.",
            )
        )

    # 4 & 8. Request merchant ID validation and merchant allowlist membership.
    request_merchant_valid = _is_non_empty_token(request.merchant_id)
    if not request_merchant_valid:
        reasons.append(
            PolicyReason(
                code="invalid_request_merchant_id",
                message="Request merchant ID is missing or invalid.",
            )
        )

    if allowlist_type_valid:
        if len(mandate.allowed_merchant_ids) == 0:
            reasons.append(
                PolicyReason(
                    code="merchant_not_allowed",
                    message="Mandate merchant allowlist is empty.",
                )
            )
        elif (
            request_merchant_valid
            and request.merchant_id not in mandate.allowed_merchant_ids
        ):
            reasons.append(
                PolicyReason(
                    code="merchant_not_allowed",
                    message="Request merchant ID is not in the mandate allowlist.",
                )
            )

    # 9. Per-payment maximum amount check.
    if (
        request_amount_valid
        and max_payment_amount_valid
        and request.amount > mandate.max_payment_amount
    ):
        reasons.append(
            PolicyReason(
                code="amount_exceeds_max_payment_amount",
                message="Request amount exceeds the mandate maximum per-payment amount.",
            )
        )

    # 10. Usage budget validation and total budget check.
    budget_used_valid = _is_valid_usd_decimal(usage.budget_used, allow_zero=True)
    if not budget_used_valid:
        reasons.append(
            PolicyReason(
                code="invalid_usage_budget_used",
                message="Usage budget_used must be a nonnegative USD Decimal with at most 2 decimal places.",
            )
        )
    elif (
        request_amount_valid
        and total_budget_valid
        and _exact_add(usage.budget_used, request.amount) > mandate.total_budget
    ):
        reasons.append(
            PolicyReason(
                code="budget_exceeded",
                message="Payment would exceed the mandate total budget.",
            )
        )

    # 11. Usage payment count validation and mandate payment count limit check.
    payment_count_valid = (
        _is_strict_int(usage.payment_count) and usage.payment_count >= 0
    )
    if not payment_count_valid:
        reasons.append(
            PolicyReason(
                code="invalid_usage_payment_count",
                message="Usage payment_count must be a nonnegative integer.",
            )
        )
    elif (
        max_payment_count_valid
        and usage.payment_count + 1 > mandate.max_payment_count
    ):
        reasons.append(
            PolicyReason(
                code="payment_count_exceeded",
                message="Payment would exceed the mandate maximum payment count.",
            )
        )

    # 12. Velocity policy configuration, recent usage validation, and velocity check.
    velocity_max_valid = (
        _is_strict_int(velocity_policy.max_payments)
        and velocity_policy.max_payments > 0
    )
    if not velocity_max_valid:
        reasons.append(
            PolicyReason(
                code="invalid_velocity_max_payments",
                message="Velocity policy max_payments must be a positive integer.",
            )
        )

    velocity_window_valid = _is_valid_positive_timedelta(velocity_policy.window)
    if not velocity_window_valid:
        reasons.append(
            PolicyReason(
                code="invalid_velocity_window",
                message="Velocity policy window must be a positive timedelta.",
            )
        )

    recent_count_valid = (
        _is_strict_int(usage.recent_payment_count)
        and usage.recent_payment_count >= 0
    )
    if not recent_count_valid:
        reasons.append(
            PolicyReason(
                code="invalid_usage_recent_payment_count",
                message="Usage recent_payment_count must be a nonnegative integer.",
            )
        )

    if (
        velocity_max_valid
        and velocity_window_valid
        and recent_count_valid
        and usage.recent_payment_count + 1 > velocity_policy.max_payments
    ):
        reasons.append(
            PolicyReason(
                code="velocity_limit_exceeded",
                message="Payment would exceed the configured velocity limit.",
            )
        )

    if reasons:
        return PolicyDecision(
            outcome=PolicyOutcome.BLOCK,
            reasons=tuple(reasons),
        )

    return PolicyDecision(outcome=PolicyOutcome.ALLOW, reasons=())


__all__ = [
    "Mandate",
    "PaymentRequest",
    "PolicyDecision",
    "PolicyOutcome",
    "PolicyReason",
    "UsageSnapshot",
    "VelocityPolicy",
    "evaluate_payment",
]
