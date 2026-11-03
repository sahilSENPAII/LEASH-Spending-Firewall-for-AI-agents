import base64
import dataclasses
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import BigInteger, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app import mandates
from app.config import Settings
from app.mandates import (
    MandateNotFound,
    MandateRecord,
    MandateTerms,
    check_and_reserve,
    create_mandate,
    generate_signing_key,
    get_mandate,
    list_mandates,
    load_signing_key,
    public_key_b64,
    release_budget,
    revoke_mandate,
    sign_terms,
    terms_bytes,
    validate_terms,
    verify_mandate,
    verify_signature,
)
from app.models import Base, BudgetHold, MandateRow, Payment
from app.policy import Mandate, PaymentRequest
from app.state import PaymentStatus

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


# --- database: create / revoke / list ----------------------------------------

KEY_B64 = generate_signing_key()
S = PaymentStatus


# SQLite only auto-increments INTEGER PRIMARY KEY, so audit_events.id needs INTEGER here.
@compiles(BigInteger, "sqlite")
def _sqlite_bigint(type_, compiler, **kw):
    return "INTEGER"


@pytest.fixture
def settings(monkeypatch):
    value = Settings(
        mandate_signing_key=KEY_B64, velocity_max_payments=3, velocity_window_seconds=600
    )
    monkeypatch.setattr(mandates, "get_settings", lambda: value)
    return value


@pytest.fixture
async def sessions(settings):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _create(sessions, **overrides):
    async with sessions() as s:
        record = await create_mandate(s, make_terms(**overrides))
        await s.commit()
    return record


async def test_created_mandate_round_trips_and_verifies(sessions):
    created = await _create(sessions)
    async with sessions() as s:
        loaded = await get_mandate(s, "m_demo")

    # SQLite drops tzinfo on read; the record must still carry UTC datetimes and verify.
    assert loaded == created
    assert loaded.terms.expires_at.tzinfo is not None
    assert loaded.budget_used == Decimal("0") and loaded.payment_count == 0
    check = verify_mandate(loaded)
    assert check.signature_valid and check.reasons == ()


async def test_mandate_created_in_another_timezone_still_verifies(sessions):
    ist = timezone(timedelta(hours=5, minutes=30))
    await _create(
        sessions,
        created_at=NOW.astimezone(ist),
        expires_at=(NOW + timedelta(days=7)).astimezone(ist),
    )
    async with sessions() as s:
        loaded = await get_mandate(s, "m_demo")
    assert loaded.terms.created_at == NOW
    assert verify_mandate(loaded).signature_valid


async def test_revoke_with_naive_time_is_refused(sessions):
    await _create(sessions)
    async with sessions() as s:
        with pytest.raises(ValueError, match="timezone-aware"):
            await revoke_mandate(s, "m_demo", datetime(2026, 11, 3))


async def test_duplicate_mandate_id_is_rejected(sessions):
    await _create(sessions)
    with pytest.raises(ValueError, match="already exists"):
        await _create(sessions)


@pytest.mark.parametrize(
    "overrides",
    [
        {"mandate_id": ""},
        {"mandate_id": "x" * 65},
        {"intent": " padded "},
        {"category": None},
        {"max_payment_amount": Decimal("0")},
        {"total_budget": Decimal("-5.00")},
        {"total_budget": Decimal("10000000000.00")},
        {"max_payment_amount": Decimal("1.005")},
        {"max_payment_count": 0},
        {"allowed_merchant_ids": frozenset()},
        {"allowed_merchant_ids": frozenset({""})},
        {"expires_at": NOW},
        {"created_at": datetime(2026, 11, 2)},
    ],
)
async def test_bad_terms_are_rejected(sessions, overrides):
    assert validate_terms(make_terms(**overrides))
    with pytest.raises(ValueError, match="invalid mandate terms"):
        await _create(sessions, **overrides)


async def test_missing_signing_key_refuses_to_create(sessions, monkeypatch):
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=""))
    with pytest.raises(ValueError, match="MANDATE_SIGNING_KEY"):
        await _create(sessions)


async def test_revoke_keeps_first_time(sessions):
    await _create(sessions)
    async with sessions() as s:
        first = await revoke_mandate(s, "m_demo", NOW)
        second = await revoke_mandate(s, "m_demo", NOW + timedelta(hours=1))
        await s.commit()
    assert first.revoked_at == second.revoked_at == NOW


async def test_revoke_unknown_mandate_raises(sessions):
    async with sessions() as s:
        with pytest.raises(MandateNotFound):
            await revoke_mandate(s, "m_missing", NOW)


async def test_list_mandates_oldest_first(sessions):
    await _create(sessions, mandate_id="m_b", created_at=NOW + timedelta(minutes=1))
    await _create(sessions, mandate_id="m_a")
    async with sessions() as s:
        assert [r.terms.mandate_id for r in await list_mandates(s)] == ["m_a", "m_b"]


# --- database: check_and_reserve / release_budget ------------------------------

PAY_NOW = NOW + timedelta(hours=1)


async def _payment(sessions, status=S.REQUESTED, amount="179.00", created_at=PAY_NOW, **kw):
    async with sessions() as s:
        payment = Payment(
            mandate_id=kw.get("mandate_id", "m_demo"),
            merchant_id="soundhub",
            amount=Decimal(amount),
            status=status,
            created_at=created_at,
        )
        s.add(payment)
        await s.commit()
    return payment.id


async def _reserve(sessions, payment_id, amount="179.00", merchant_id="soundhub", **kw):
    async with sessions() as s:
        decision = await check_and_reserve(
            s,
            payment_id=payment_id,
            request=PaymentRequest(
                amount=Decimal(amount),
                currency="USD",
                category="electronics",
                merchant_id=merchant_id,
            ),
            mandate_id=kw.get("mandate_id", "m_demo"),
            now=kw.get("now", PAY_NOW),
        )
        await s.commit()
    return decision


async def _state(sessions):
    async with sessions() as s:
        record = await get_mandate(s, "m_demo")
        holds = (await s.scalars(select(BudgetHold))).all()
    return record, holds


def _codes(decision):
    return [r.code for r in decision.reasons]


async def test_allow_holds_budget_and_count(sessions):
    await _create(sessions)
    pid = await _payment(sessions)

    decision = await _reserve(sessions, pid)

    assert decision.is_allowed
    record, (hold,) = await _state(sessions)
    assert record.budget_used == Decimal("179.00") and record.payment_count == 1
    assert hold.payment_id == pid and hold.amount == Decimal("179.00")
    assert hold.released_at is None
    # Holding budget changes state only, so the mandate still verifies.
    assert verify_mandate(record).signature_valid


async def test_block_holds_nothing(sessions):
    await _create(sessions)
    pid = await _payment(sessions)

    decision = await _reserve(sessions, pid, merchant_id="megadeals")

    assert _codes(decision) == ["merchant_not_allowed"]
    record, holds = await _state(sessions)
    assert record.budget_used == Decimal("0") and record.payment_count == 0 and holds == []


async def test_unknown_mandate_blocks(sessions):
    pid = await _payment(sessions)
    decision = await _reserve(sessions, pid, mandate_id="m_missing")
    assert _codes(decision) == ["mandate_not_found"]


async def test_tampered_terms_in_database_block(sessions):
    await _create(sessions)
    async with sessions() as s:
        await s.execute(
            update(MandateRow).where(MandateRow.id == "m_demo").values(total_budget=99999)
        )
        await s.commit()
    pid = await _payment(sessions)

    decision = await _reserve(sessions, pid)

    assert "mandate_signature_invalid" in _codes(decision)
    _, holds = await _state(sessions)
    assert holds == []


async def test_revoked_mandate_blocks(sessions):
    await _create(sessions)
    async with sessions() as s:
        await revoke_mandate(s, "m_demo", NOW)
        await s.commit()
    decision = await _reserve(sessions, await _payment(sessions))
    assert _codes(decision) == ["mandate_revoked"]


async def test_held_budget_counts_against_the_next_payment(sessions):
    await _create(sessions)
    assert (await _reserve(sessions, await _payment(sessions))).is_allowed
    assert (await _reserve(sessions, await _payment(sessions))).is_allowed

    decision = await _reserve(sessions, await _payment(sessions))  # 3 x 179 > 500

    assert _codes(decision) == ["budget_exceeded"]
    record, holds = await _state(sessions)
    assert record.budget_used == Decimal("358.00") and len(holds) == 2


async def test_velocity_ignores_ended_old_and_other_mandate_payments(sessions):
    await _create(sessions, max_payment_count=20, total_budget=Decimal("5000.00"))
    await _payment(sessions, status=S.AUTHORIZED, amount="1.00")
    await _payment(sessions, status=S.AWAITING_APPROVAL, amount="1.00")
    for status in (S.BLOCKED, S.DENIED, S.EXPIRED, S.FAILED, S.VOIDED):
        await _payment(sessions, status=status, amount="1.00")
    await _payment(sessions, status=S.CAPTURED, amount="1.00", mandate_id="m_other")
    await _payment(
        sessions, status=S.CAPTURED, amount="1.00", created_at=PAY_NOW - timedelta(seconds=601)
    )

    # Only the 2 live payments count: 2 + this one = 3, exactly at the limit.
    decision = await _reserve(sessions, await _payment(sessions, amount="1.00"), amount="1.00")
    assert decision.is_allowed


async def test_velocity_counts_a_payment_on_the_window_edge(sessions):
    await _create(sessions, max_payment_count=20, total_budget=Decimal("5000.00"))
    await _payment(sessions, status=S.CAPTURED, amount="1.00")
    await _payment(sessions, status=S.CAPTURED, amount="1.00")
    await _payment(
        sessions, status=S.CAPTURED, amount="1.00", created_at=PAY_NOW - timedelta(seconds=600)
    )

    # created_at >= now - window: the edge payment counts, so 3 + this one exceeds 3.
    decision = await _reserve(sessions, await _payment(sessions, amount="1.00"), amount="1.00")
    assert _codes(decision) == ["velocity_limit_exceeded"]


async def test_velocity_ignores_the_payment_being_checked(sessions):
    await _create(sessions)
    await _payment(sessions, status=S.CAPTURED, amount="1.00")
    await _payment(sessions, status=S.CAPTURED, amount="1.00")
    # Two live payments + this one = 3, exactly at the limit.
    assert (await _reserve(sessions, await _payment(sessions))).is_allowed


async def test_second_hold_for_same_payment_raises(sessions):
    await _create(sessions)
    pid = await _payment(sessions)
    assert (await _reserve(sessions, pid)).is_allowed
    with pytest.raises(ValueError, match="already has a budget hold"):
        await _reserve(sessions, pid, amount="1.00")
    record, holds = await _state(sessions)
    assert record.budget_used == Decimal("179.00") and len(holds) == 1


async def test_release_gives_budget_back_once(sessions):
    await _create(sessions)
    pid = await _payment(sessions)
    await _reserve(sessions, pid)

    async with sessions() as s:
        first = await release_budget(s, payment_id=pid, now=PAY_NOW)
        second = await release_budget(s, payment_id=pid, now=PAY_NOW)
        await s.commit()

    assert first is True and second is False
    record, (hold,) = await _state(sessions)
    assert record.budget_used == Decimal("0") and record.payment_count == 0
    assert hold.released_at is not None


async def test_release_with_naive_time_is_refused(sessions):
    await _create(sessions)
    pid = await _payment(sessions)
    await _reserve(sessions, pid)
    async with sessions() as s:
        with pytest.raises(ValueError, match="timezone-aware"):
            await release_budget(s, payment_id=pid, now=datetime(2026, 11, 3))


async def test_release_without_hold_is_false(sessions):
    await _create(sessions)
    async with sessions() as s:
        assert await release_budget(s, payment_id="no-such-payment", now=PAY_NOW) is False


async def test_released_budget_can_be_spent_again(sessions):
    await _create(sessions, total_budget=Decimal("200.00"))
    first = await _payment(sessions)
    await _reserve(sessions, first)
    assert _codes(await _reserve(sessions, await _payment(sessions))) == ["budget_exceeded"]

    async with sessions() as s:
        await release_budget(s, payment_id=first, now=PAY_NOW)
        await s.commit()

    assert (await _reserve(sessions, await _payment(sessions))).is_allowed


async def test_release_refuses_to_go_below_zero(sessions):
    await _create(sessions)
    pid = await _payment(sessions)
    await _reserve(sessions, pid)
    async with sessions() as s:
        await s.execute(
            update(MandateRow).where(MandateRow.id == "m_demo").values(budget_used=0)
        )
        await s.commit()
    async with sessions() as s:
        with pytest.raises(ValueError, match="lower than the hold"):
            await release_budget(s, payment_id=pid, now=PAY_NOW)
