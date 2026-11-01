import base64
import dataclasses
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import mandates
from app.config import Settings
from app.mandates import (
    MandateRecord,
    MandateTerms,
    generate_signing_key,
    load_signing_key,
    public_key_b64,
    sign_terms,
    terms_bytes,
    verify_mandate,
    verify_signature,
)
from app.policy import Mandate

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
KEY = Ed25519PrivateKey.generate()


def make_terms(**overrides) -> MandateTerms:
    fields = dict(
        mandate_id="m_demo",
        intent="Sony or Bose noise-cancelling headphones",
        category="electronics",
        max_payment_amount=Decimal("200.00"),
        total_budget=Decimal("500.00"),
        max_payment_count=3,
        allowed_merchant_ids=frozenset({"soundhub"}),
        expires_at=NOW + timedelta(days=7),
        created_at=NOW,
    )
    fields.update(overrides)
    return MandateTerms(**fields)


def make_record(terms=None, signature=None, **overrides) -> MandateRecord:
    terms = terms or make_terms()
    fields = dict(
        terms=terms,
        signature=sign_terms(terms, KEY) if signature is None else signature,
        revoked_at=None,
        budget_used=Decimal("0"),
        payment_count=0,
    )
    fields.update(overrides)
    return MandateRecord(**fields)


# --- signing ---------------------------------------------------------------


def test_signed_terms_verify():
    terms = make_terms()
    assert verify_signature(terms, sign_terms(terms, KEY), KEY.public_key())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mandate_id", "m_other"),
        ("intent", "3 x $100 gift cards"),
        ("category", "gift_cards"),
        ("max_payment_amount", Decimal("200.01")),
        ("total_budget", Decimal("5000.00")),
        ("max_payment_count", 4),
        ("allowed_merchant_ids", frozenset({"soundhub", "megadeals"})),
        ("expires_at", NOW + timedelta(days=8)),
        ("created_at", NOW + timedelta(seconds=1)),
    ],
)
def test_changing_any_term_breaks_the_signature(field, value):
    signature = sign_terms(make_terms(), KEY)
    tampered = make_terms(**{field: value})
    assert not verify_signature(tampered, signature, KEY.public_key())


def test_wrong_key_fails():
    terms = make_terms()
    other = Ed25519PrivateKey.generate()
    assert not verify_signature(terms, sign_terms(terms, other), KEY.public_key())


@pytest.mark.parametrize("signature", ["", "not base64!", base64.b64encode(b"x" * 64).decode()])
def test_malformed_signature_fails(signature):
    assert not verify_signature(make_terms(), signature, KEY.public_key())


def test_equal_amounts_sign_the_same():
    # 200 and 200.00 are the same money, so they must produce the same signed bytes.
    assert terms_bytes(make_terms(max_payment_amount=Decimal("200"))) == terms_bytes(make_terms())


def test_merchant_order_does_not_matter():
    a = make_terms(allowed_merchant_ids=frozenset(["soundhub", "megadeals"]))
    b = make_terms(allowed_merchant_ids=frozenset(["megadeals", "soundhub"]))
    assert terms_bytes(a) == terms_bytes(b)


def test_same_instant_in_another_timezone_signs_the_same():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert terms_bytes(make_terms(created_at=NOW.astimezone(ist))) == terms_bytes(make_terms())


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_payment_amount": 200.0},
        {"max_payment_amount": Decimal("0.001")},
        {"total_budget": Decimal("NaN")},
        {"total_budget": Decimal("1E+200")},
        {"max_payment_count": True},
        {"max_payment_count": 3.0},
        {"allowed_merchant_ids": {"soundhub"}},
        {"expires_at": datetime(2026, 12, 1)},
    ],
)
def test_unsignable_terms_are_rejected(overrides):
    with pytest.raises((TypeError, ValueError)):
        sign_terms(make_terms(**overrides), KEY)


# --- verify_mandate --------------------------------------------------------


def test_verify_mandate_returns_policy_mandate():
    check = verify_mandate(make_record(), public_key=KEY.public_key())
    assert check.signature_valid
    assert check.reasons == ()
    assert check.mandate == Mandate(
        mandate_id="m_demo",
        category="electronics",
        max_payment_amount=Decimal("200.00"),
        total_budget=Decimal("500.00"),
        max_payment_count=3,
        allowed_merchant_ids=frozenset({"soundhub"}),
        expires_at=NOW + timedelta(days=7),
        revoked=False,
    )


def test_revoked_record_maps_to_revoked_mandate():
    check = verify_mandate(make_record(revoked_at=NOW), public_key=KEY.public_key())
    assert check.signature_valid
    assert check.mandate.revoked is True


def test_usage_state_is_not_signed():
    # Budget and count change after every payment; the signature must still hold.
    record = make_record(budget_used=Decimal("179.00"), payment_count=1, revoked_at=NOW)
    assert verify_mandate(record, public_key=KEY.public_key()).signature_valid


def test_tampered_record_fails_closed():
    record = make_record()
    tampered = dataclasses.replace(
        record, terms=dataclasses.replace(record.terms, total_budget=Decimal("9999.00"))
    )
    check = verify_mandate(tampered, public_key=KEY.public_key())
    assert not check.signature_valid
    assert [r.code for r in check.reasons] == ["mandate_signature_invalid"]


def test_malformed_record_fails_closed():
    check = verify_mandate("not a record", public_key=KEY.public_key())  # type: ignore[arg-type]
    assert not check.signature_valid
    assert check.mandate is None
    assert [r.code for r in check.reasons] == ["invalid_mandate"]


def test_verify_mandate_uses_settings_key(monkeypatch):
    key_b64 = generate_signing_key()
    key = load_signing_key(key_b64)
    terms = make_terms()
    record = make_record(terms=terms, signature=sign_terms(terms, key))
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=key_b64))
    assert verify_mandate(record).signature_valid


def test_missing_settings_key_fails_closed(monkeypatch):
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=""))
    check = verify_mandate(make_record())
    assert not check.signature_valid
    assert check.mandate is None


# --- keys ------------------------------------------------------------------


def test_generated_key_round_trips():
    value = generate_signing_key()
    key = load_signing_key(value)
    assert len(base64.b64decode(value)) == 32
    assert len(base64.b64decode(public_key_b64(key))) == 32


@pytest.mark.parametrize("value", ["", "not base64!", base64.b64encode(b"short").decode()])
def test_bad_signing_key_is_rejected(value):
    with pytest.raises(ValueError):
        load_signing_key(value)
