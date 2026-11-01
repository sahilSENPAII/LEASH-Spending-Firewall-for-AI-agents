"""Unit tests for the deterministic policy evaluator (app/policy.py)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

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

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def make_request(**overrides) -> PaymentRequest:
    fields = dict(
        amount=Decimal("179.00"),
        currency="USD",
        category="electronics",
        merchant_id="soundhub",
    )
    fields.update(overrides)
    return PaymentRequest(**fields)


def make_mandate(**overrides) -> Mandate:
    fields = dict(
        mandate_id="m_demo",
        category="electronics",
        max_payment_amount=Decimal("200.00"),
        total_budget=Decimal("500.00"),
        max_payment_count=3,
        allowed_merchant_ids=frozenset({"soundhub"}),
        expires_at=NOW + timedelta(days=7),
        revoked=False,
    )
    fields.update(overrides)
    return Mandate(**fields)


def make_usage(**overrides) -> UsageSnapshot:
    fields = dict(budget_used=Decimal("0"), payment_count=0, recent_payment_count=0)
    fields.update(overrides)
    return UsageSnapshot(**fields)


def make_velocity(**overrides) -> VelocityPolicy:
    fields = dict(max_payments=5, window=timedelta(hours=1))
    fields.update(overrides)
    return VelocityPolicy(**fields)


def evaluate(
    *,
    request=None,
    mandate=None,
    usage=None,
    velocity=None,
    signature=True,
    now=NOW,
) -> PolicyDecision:
    return evaluate_payment(
        request=make_request() if request is None else request,
        mandate=make_mandate() if mandate is None else mandate,
        usage=make_usage() if usage is None else usage,
        velocity_policy=make_velocity() if velocity is None else velocity,
        mandate_signature_valid=signature,
        now=now,
    )


def codes(decision: PolicyDecision) -> set[str]:
    return {reason.code for reason in decision.reasons}


def assert_blocked_with(decision: PolicyDecision, code: str) -> None:
    assert decision.outcome is PolicyOutcome.BLOCK
    assert not decision.is_allowed
    assert code in codes(decision), codes(decision)


# --- happy path -------------------------------------------------------------


def test_valid_request_is_allowed():
    decision = evaluate()
    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.is_allowed
    assert decision.reasons == ()


def test_inputs_are_not_mutated():
    request, mandate, usage = make_request(), make_mandate(), make_usage()
    before = (dataclasses.asdict(request), dataclasses.asdict(mandate), dataclasses.asdict(usage))
    evaluate(request=request, mandate=mandate, usage=usage)
    after = (dataclasses.asdict(request), dataclasses.asdict(mandate), dataclasses.asdict(usage))
    assert before == after


# --- 1. signature ----------------------------------------------------------


@pytest.mark.parametrize("signature", [False, None, 1, "true", "True"])
def test_signature_must_be_exactly_true(signature):
    assert_blocked_with(evaluate(signature=signature), "mandate_signature_invalid")


# --- 2. revocation ---------------------------------------------------------


def test_revoked_mandate_is_blocked():
    assert_blocked_with(evaluate(mandate=make_mandate(revoked=True)), "mandate_revoked")


@pytest.mark.parametrize("revoked", [0, None, "false"])
def test_non_bool_revoked_flag_is_blocked(revoked):
    assert_blocked_with(
        evaluate(mandate=make_mandate(revoked=revoked)), "invalid_mandate_revocation_state"
    )


# --- 3. expiry and time ----------------------------------------------------


def test_expired_mandate_is_blocked():
    mandate = make_mandate(expires_at=NOW - timedelta(seconds=1))
    assert_blocked_with(evaluate(mandate=mandate), "mandate_expired")


def test_mandate_expiring_exactly_now_is_blocked():
    assert_blocked_with(evaluate(mandate=make_mandate(expires_at=NOW)), "mandate_expired")


def test_expiry_compared_across_timezones():
    ist = timezone(timedelta(hours=5, minutes=30))
    # Same instant as NOW + 1 minute, expressed in IST.
    expires = (NOW + timedelta(minutes=1)).astimezone(ist)
    assert evaluate(mandate=make_mandate(expires_at=expires)).is_allowed
    expired = (NOW - timedelta(minutes=1)).astimezone(ist)
    assert_blocked_with(evaluate(mandate=make_mandate(expires_at=expired)), "mandate_expired")


def test_naive_now_is_blocked():
    assert_blocked_with(evaluate(now=NOW.replace(tzinfo=None)), "invalid_evaluation_time")


def test_naive_expiry_is_blocked():
    mandate = make_mandate(expires_at=(NOW + timedelta(days=1)).replace(tzinfo=None))
    assert_blocked_with(evaluate(mandate=mandate), "invalid_mandate_expiry")


@pytest.mark.parametrize("now", [None, "2026-11-02T12:00:00Z", NOW.timestamp()])
def test_non_datetime_now_is_blocked(now):
    assert_blocked_with(evaluate(now=now), "invalid_evaluation_time")


# --- amount ----------------------------------------------------------------


@pytest.mark.parametrize(
    "amount",
    [
        179.0,  # float
        179,  # int
        "179.00",  # str
        Decimal("0"),
        Decimal("-0"),
        Decimal("-1.00"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("0.001"),
        Decimal("1.005"),
    ],
)
def test_invalid_amount_is_blocked(amount):
    assert_blocked_with(evaluate(request=make_request(amount=amount)), "invalid_request_amount")


@pytest.mark.parametrize(
    "amount", [Decimal("0.01"), Decimal("1.230"), Decimal("1E+1"), Decimal("200")]
)
def test_valid_amount_forms_are_allowed(amount):
    assert evaluate(request=make_request(amount=amount)).is_allowed


# --- 9. per-payment max ----------------------------------------------------


def test_amount_equal_to_max_is_allowed():
    assert evaluate(request=make_request(amount=Decimal("200.00"))).is_allowed


def test_amount_one_cent_over_max_is_blocked():
    decision = evaluate(request=make_request(amount=Decimal("200.01")))
    assert_blocked_with(decision, "amount_exceeds_max_payment_amount")


# --- currency --------------------------------------------------------------


@pytest.mark.parametrize("currency", ["INR", "usd", "EUR"])
def test_non_usd_currency_is_blocked(currency):
    assert_blocked_with(evaluate(request=make_request(currency=currency)), "unsupported_currency")


@pytest.mark.parametrize("currency", ["", " ", " USD", None, 840])
def test_malformed_currency_is_blocked(currency):
    assert_blocked_with(
        evaluate(request=make_request(currency=currency)), "invalid_request_currency"
    )


# --- 7. category -----------------------------------------------------------


def test_category_mismatch_is_blocked():
    assert_blocked_with(
        evaluate(request=make_request(category="gift_cards")), "category_mismatch"
    )


def test_category_match_is_case_sensitive():
    assert_blocked_with(
        evaluate(request=make_request(category="Electronics")), "category_mismatch"
    )


@pytest.mark.parametrize("category", ["", "  ", "electronics ", None])
def test_malformed_request_category_is_blocked(category):
    assert_blocked_with(
        evaluate(request=make_request(category=category)), "invalid_request_category"
    )


def test_malformed_mandate_category_is_blocked():
    assert_blocked_with(
        evaluate(mandate=make_mandate(category="")), "invalid_mandate_category"
    )


# --- 8. merchant allowlist -------------------------------------------------


def test_merchant_not_in_allowlist_is_blocked():
    assert_blocked_with(
        evaluate(request=make_request(merchant_id="megadeals")), "merchant_not_allowed"
    )


def test_empty_allowlist_blocks_everything():
    assert_blocked_with(
        evaluate(mandate=make_mandate(allowed_merchant_ids=frozenset())), "merchant_not_allowed"
    )


@pytest.mark.parametrize(
    "allowlist",
    [{"soundhub"}, ["soundhub"], ("soundhub",), "soundhub", frozenset({"soundhub", ""})],
)
def test_malformed_allowlist_is_blocked(allowlist):
    assert_blocked_with(
        evaluate(mandate=make_mandate(allowed_merchant_ids=allowlist)),
        "invalid_mandate_merchant_allowlist",
    )


@pytest.mark.parametrize("merchant_id", ["", " soundhub", None])
def test_malformed_merchant_id_is_blocked(merchant_id):
    assert_blocked_with(
        evaluate(request=make_request(merchant_id=merchant_id)), "invalid_request_merchant_id"
    )


# --- 10. total budget ------------------------------------------------------


def test_payment_exactly_filling_budget_is_allowed():
    usage = make_usage(budget_used=Decimal("321.00"))
    assert evaluate(usage=usage).is_allowed  # 321 + 179 == 500


def test_payment_exceeding_budget_is_blocked():
    usage = make_usage(budget_used=Decimal("321.01"))
    assert_blocked_with(evaluate(usage=usage), "budget_exceeded")


def test_budget_check_does_not_round_huge_values():
    # The default 28-digit decimal context rounds 1E+30 + 0.01 back to 1E+30,
    # which would let a fully spent mandate pay again. Must stay BLOCK.
    huge = Decimal("1E+30")
    mandate = make_mandate(max_payment_amount=huge, total_budget=huge)
    usage = make_usage(budget_used=huge)
    decision = evaluate(mandate=mandate, usage=usage, request=make_request(amount=Decimal("0.01")))
    assert decision.outcome is PolicyOutcome.BLOCK


def test_budget_check_ignores_caller_decimal_context():
    with localcontext() as ctx:
        ctx.prec = 3  # 321.01 + 179.00 would round to 500 under this context
        decision = evaluate(usage=make_usage(budget_used=Decimal("321.01")))
    assert_blocked_with(decision, "budget_exceeded")


@pytest.mark.parametrize("budget_used", [Decimal("-1"), 0, 0.0, Decimal("0.001"), Decimal("NaN")])
def test_malformed_budget_used_is_blocked(budget_used):
    assert_blocked_with(
        evaluate(usage=make_usage(budget_used=budget_used)), "invalid_usage_budget_used"
    )


@pytest.mark.parametrize("total_budget", [Decimal("0"), Decimal("-5"), 500, Decimal("0.005")])
def test_malformed_total_budget_is_blocked(total_budget):
    assert_blocked_with(
        evaluate(mandate=make_mandate(total_budget=total_budget)), "invalid_mandate_total_budget"
    )


@pytest.mark.parametrize("max_amount", [Decimal("0"), 200.0, Decimal("Infinity")])
def test_malformed_max_payment_amount_is_blocked(max_amount):
    assert_blocked_with(
        evaluate(mandate=make_mandate(max_payment_amount=max_amount)),
        "invalid_mandate_max_payment_amount",
    )


# --- 11. payment count -----------------------------------------------------


def test_last_allowed_payment_is_allowed():
    assert evaluate(usage=make_usage(payment_count=2)).is_allowed  # 2 + 1 == 3


def test_payment_count_exceeded_is_blocked():
    assert_blocked_with(evaluate(usage=make_usage(payment_count=3)), "payment_count_exceeded")


@pytest.mark.parametrize("count", [-1, True, 1.0, "0", None])
def test_malformed_payment_count_is_blocked(count):
    assert_blocked_with(
        evaluate(usage=make_usage(payment_count=count)), "invalid_usage_payment_count"
    )


@pytest.mark.parametrize("max_count", [0, -1, True, 3.0])
def test_malformed_max_payment_count_is_blocked(max_count):
    assert_blocked_with(
        evaluate(mandate=make_mandate(max_payment_count=max_count)),
        "invalid_mandate_max_payment_count",
    )


# --- 12. velocity ----------------------------------------------------------


def test_velocity_at_limit_is_allowed():
    assert evaluate(usage=make_usage(recent_payment_count=4)).is_allowed  # 4 + 1 == 5


def test_velocity_exceeded_is_blocked():
    decision = evaluate(usage=make_usage(recent_payment_count=5))
    assert_blocked_with(decision, "velocity_limit_exceeded")


@pytest.mark.parametrize("recent", [-1, False, 2.0])
def test_malformed_recent_count_is_blocked(recent):
    assert_blocked_with(
        evaluate(usage=make_usage(recent_payment_count=recent)),
        "invalid_usage_recent_payment_count",
    )


@pytest.mark.parametrize("max_payments", [0, -1, True])
def test_malformed_velocity_max_is_blocked(max_payments):
    assert_blocked_with(
        evaluate(velocity=make_velocity(max_payments=max_payments)),
        "invalid_velocity_max_payments",
    )


@pytest.mark.parametrize("window", [timedelta(0), timedelta(seconds=-1), 3600])
def test_malformed_velocity_window_is_blocked(window):
    assert_blocked_with(
        evaluate(velocity=make_velocity(window=window)), "invalid_velocity_window"
    )


# --- structure and fail-closed ---------------------------------------------


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("request", "invalid_request"),
        ("mandate", "invalid_mandate"),
        ("usage", "invalid_usage_snapshot"),
        ("velocity_policy", "invalid_velocity_policy"),
    ],
)
def test_wrong_input_types_are_blocked(field, code):
    kwargs = dict(
        request=make_request(),
        mandate=make_mandate(),
        usage=make_usage(),
        velocity_policy=make_velocity(),
        mandate_signature_valid=True,
        now=NOW,
    )
    kwargs[field] = {"not": "a dataclass"}
    decision = evaluate_payment(**kwargs)
    assert decision.outcome is PolicyOutcome.BLOCK
    assert codes(decision) == {code}


def test_blank_mandate_id_is_blocked():
    assert_blocked_with(evaluate(mandate=make_mandate(mandate_id=" ")), "invalid_mandate_id")


def test_unexpected_error_fails_closed():
    class BadDecimal(Decimal):
        def __gt__(self, other):
            raise RuntimeError("boom")

    request = make_request(amount=BadDecimal("10.00"))
    decision = evaluate(request=request)
    assert decision.outcome is PolicyOutcome.BLOCK
    assert codes(decision) == {"internal_evaluation_error"}


def test_injection_style_request_collects_all_reasons():
    # MegaDeals demo: agent tries to buy gift cards at the wrong store, too big, too often.
    request = make_request(amount=Decimal("300.00"), category="gift_cards", merchant_id="megadeals")
    usage = make_usage(budget_used=Decimal("400.00"), payment_count=3, recent_payment_count=5)
    decision = evaluate(request=request, usage=usage, signature=False)
    assert codes(decision) == {
        "mandate_signature_invalid",
        "category_mismatch",
        "merchant_not_allowed",
        "amount_exceeds_max_payment_amount",
        "budget_exceeded",
        "payment_count_exceeded",
        "velocity_limit_exceeded",
    }


def test_reason_order_is_stable():
    request = make_request(category="gift_cards", merchant_id="megadeals")
    first = evaluate(request=request)
    second = evaluate(request=request)
    assert [r.code for r in first.reasons] == [r.code for r in second.reasons]
    assert [r.code for r in first.reasons] == ["category_mismatch", "merchant_not_allowed"]


# --- PolicyDecision invariants ---------------------------------------------


def test_allow_decision_cannot_carry_reasons():
    with pytest.raises(ValueError):
        PolicyDecision(PolicyOutcome.ALLOW, (PolicyReason("x", "y"),))


def test_block_decision_requires_a_reason():
    with pytest.raises(ValueError):
        PolicyDecision(PolicyOutcome.BLOCK, ())


def test_decision_rejects_bad_types():
    with pytest.raises(TypeError):
        PolicyDecision("ALLOW", ())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PolicyDecision(PolicyOutcome.BLOCK, [PolicyReason("x", "y")])  # type: ignore[arg-type]
