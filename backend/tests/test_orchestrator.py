import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, mandates, mcp_server, orchestrator
from app.audit import verify_chain
from app.catalog import DEFAULT_CATALOG_PATH, load_catalog
from app.config import Settings
from app.main import app
from app.mandates import MandateTerms, create_mandate, generate_signing_key, get_mandate
from app.models import AuditEvent, BudgetHold, Payment
from app.paypal import Authorization, PayPalError
from app.state import PaymentStatus
from scripts.seed_demo_mandate import DEMO_TERMS

S = PaymentStatus
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
KEY_B64 = generate_signing_key()
SONY = {"sku": "sh-sony-xm5", "quantity": 1}  # $179.00 at SoundHub


class FakePayPal:
    def __init__(self, authorize_error=None, capture_status="COMPLETED", void_error=None):
        self.authorize_error = authorize_error
        self.capture_status = capture_status
        self.void_error = void_error
        self.calls = []

    async def authorize(self, **kwargs):
        self.calls.append(("authorize", kwargs))
        if self.authorize_error:
            raise self.authorize_error
        return Authorization(order_id="O-1", authorization_id="A-1", status="CREATED")

    async def capture(self, authorization_id, request_id):
        self.calls.append(("capture", authorization_id, request_id))
        if isinstance(self.capture_status, Exception):
            raise self.capture_status
        return self.capture_status

    async def void(self, authorization_id, request_id):
        self.calls.append(("void", authorization_id, request_id))
        if self.void_error:
            raise self.void_error


@pytest.fixture
async def sessions(sessions, monkeypatch):
    # The shared SQLite database from conftest.py, with a signing key and m_demo seeded as on Neon.
    settings = Settings(
        mandate_signing_key=KEY_B64, velocity_max_payments=3, velocity_window_seconds=600
    )
    monkeypatch.setattr(mandates, "get_settings", lambda: settings)
    # The shipped catalog, whatever CATALOG_PATH the local .env sets.
    catalog = load_catalog(DEFAULT_CATALOG_PATH)
    monkeypatch.setattr(orchestrator, "get_catalog", lambda: catalog)
    async with sessions() as s:
        await create_mandate(s, MandateTerms(**DEMO_TERMS, created_at=NOW - timedelta(days=1)))
        # Room for many payments, so count and velocity limits can be reached at catalog prices.
        big = DEMO_TERMS | {"mandate_id": "m_big", "total_budget": Decimal("5000.00")}
        await create_mandate(s, MandateTerms(**big, created_at=NOW - timedelta(days=1)))
        await s.commit()
    return sessions


async def _pay(sessions, paypal, vault_id="VAULT", **overrides):
    args = {
        "mandate_id": "m_demo",
        "merchant_id": "soundhub",
        "items": [SONY],
        "now": NOW,
    }
    return await orchestrator.request_payment(sessions, paypal, vault_id, **(args | overrides))


async def _rows(sessions):
    async with sessions() as s:
        payments = (await s.scalars(select(Payment))).all()
        events = (
            await s.scalars(
                select(AuditEvent).where(AuditEvent.payment_id.is_not(None)).order_by(AuditEvent.id)
            )
        ).all()
    return payments, events


async def _usage(sessions, mandate_id="m_demo"):
    async with sessions() as s:
        record = await get_mandate(s, mandate_id)
        holds = (await s.scalars(select(BudgetHold))).all()
    return record.budget_used, record.payment_count, holds


async def _chain_ok(sessions):
    async with sessions() as s:
        return (await verify_chain(s)).ok


def _codes(result):
    return [r["code"] for r in result["reasons"]]


async def test_allow_authorizes_then_captures_with_vault_and_idempotency_key(sessions):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal)

    assert result["decision"] == "ALLOW" and result["reasons"] == []
    assert result["status"] == S.CAPTURED
    (payment,), events = await _rows(sessions)
    assert payment.id == result["payment_id"]
    assert payment.status == S.CAPTURED and payment.decision == "ALLOW"
    assert payment.paypal_order_id == "O-1" and payment.paypal_authorization_id == "A-1"
    assert json.loads(payment.items) == [SONY | {"unit_price": "179.00"}]
    assert [e.event_type for e in events] == [
        "payment.decided",
        "payment.authorized",
        "payment.captured",
    ]
    assert all(e.payment_id == payment.id for e in events)
    assert events[0].payload["amount"] == "179.00"
    assert events[0].payload["items"] == [SONY | {"unit_price": "179.00"}]

    (_, auth), capture = paypal.calls
    assert auth["vault_id"] == "VAULT"
    assert auth["amount"] == Decimal("179.00")
    assert auth["request_id"] == payment.idempotency_key
    assert auth["custom_id"] == "soundhub"
    assert auth["description"] == payment.items
    assert capture == ("capture", "A-1", f"{payment.idempotency_key}-capture")

    budget_used, count, (hold,) = await _usage(sessions)
    assert (budget_used, count) == (Decimal("179.00"), 1)
    assert hold.payment_id == payment.id and hold.released_at is None
    assert await _chain_ok(sessions)


async def test_policy_block_never_calls_paypal(sessions):
    paypal = FakePayPal()
    two_sony = [{"sku": "md-sony-xm5", "quantity": 2}]  # $298.00
    result = await _pay(sessions, paypal, merchant_id="megadeals", items=two_sony)

    assert result["decision"] == "BLOCK" and result["status"] == S.BLOCKED
    assert _codes(result) == ["merchant_not_allowed", "amount_exceeds_max_payment_amount"]
    assert paypal.calls == []
    (payment,), events = await _rows(sessions)
    assert payment.status == S.BLOCKED and payment.decision == "BLOCK"
    assert payment.reasons == result["reasons"]
    assert [e.event_type for e in events] == ["payment.decided", "payment.blocked"]
    assert events[0].payload["reasons"] == result["reasons"]
    assert await _usage(sessions) == (Decimal("0.00"), 0, [])
    assert await _chain_ok(sessions)


async def test_unknown_mandate_blocks(sessions):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal, mandate_id="m_other")

    assert result["decision"] == "BLOCK" and _codes(result) == ["mandate_not_found"]
    assert paypal.calls == []


async def test_amount_is_priced_from_the_catalog_not_the_agent(sessions):
    paypal = FakePayPal()
    items = [SONY | {"unit_price": "1.00", "price": "1.00"}, SONY]
    result = await _pay(sessions, paypal, items=items)

    assert result["decision"] == "BLOCK"
    assert _codes(result) == ["amount_exceeds_max_payment_amount"]
    (payment,), _ = await _rows(sessions)
    assert payment.amount == Decimal("358.00")
    assert json.loads(payment.items) == [SONY | {"unit_price": "179.00"}] * 2
    assert paypal.calls == []


@pytest.mark.parametrize(
    ("merchant_id", "items", "codes", "amount"),
    [
        ("soundhub", [], ["empty_cart"], "0.00"),
        ("soundhub", [{"sku": "sh-nope", "quantity": 1}], ["unknown_sku"], "0.00"),
        ("soundhub", [{"quantity": 1}], ["unknown_sku"], "0.00"),
        ("soundhub", [{"sku": 5, "quantity": 1}], ["unknown_sku"], "0.00"),
        (
            "soundhub",
            [SONY, {"sku": "md-sony-xm5", "quantity": 1}],
            ["sku_wrong_merchant"],
            "179.00",
        ),
        ("evilshop", [SONY], ["sku_wrong_merchant"], "0.00"),
        ("soundhub", [{"sku": "sh-sony-xm5", "quantity": 0}], ["invalid_quantity"], "0.00"),
        ("soundhub", [{"sku": "sh-sony-xm5", "quantity": 11}], ["invalid_quantity"], "0.00"),
        ("soundhub", [{"sku": "sh-sony-xm5", "quantity": -1}], ["invalid_quantity"], "0.00"),
        ("soundhub", [{"sku": "sh-sony-xm5", "quantity": 1.5}], ["invalid_quantity"], "0.00"),
        ("soundhub", [{"sku": "sh-sony-xm5", "quantity": "1"}], ["invalid_quantity"], "0.00"),
        ("soundhub", [{"sku": "sh-sony-xm5", "quantity": True}], ["invalid_quantity"], "0.00"),
        ("soundhub", [{"sku": "sh-sony-xm5"}], ["invalid_quantity"], "0.00"),
        (
            "megadeals",
            [{"sku": "md-sony-xm5", "quantity": 1}, {"sku": "md-giftcard-100", "quantity": 3}],
            ["mixed_categories"],
            "449.00",
        ),
        (
            "megadeals",
            [{"sku": "md-nope", "quantity": 1}, {"sku": "md-giftcard-100", "quantity": 99}],
            ["unknown_sku", "invalid_quantity"],
            "0.00",
        ),
    ],
)
async def test_cart_leash_cannot_price_is_blocked_and_recorded(
    sessions, merchant_id, items, codes, amount
):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal, merchant_id=merchant_id, items=items)

    assert result["decision"] == "BLOCK" and result["status"] == S.BLOCKED
    assert _codes(result) == codes
    assert paypal.calls == []
    (payment,), events = await _rows(sessions)
    assert payment.id == result["payment_id"]
    assert payment.status == S.BLOCKED and payment.decision == "BLOCK"
    assert payment.reasons == result["reasons"]
    assert payment.amount == Decimal(amount)
    assert [line["sku"] for line in json.loads(payment.items)] == [i.get("sku") for i in items]
    assert [line.get("quantity") for line in json.loads(payment.items)] == [
        i.get("quantity") if type(i.get("quantity")) is not float else repr(i["quantity"])
        for i in items
    ]
    assert [e.event_type for e in events] == ["payment.decided", "payment.blocked"]
    assert events[0].payload["reasons"] == result["reasons"]
    # Blocked before the mandate check: no hold taken.
    assert await _usage(sessions) == (Decimal("0.00"), 0, [])
    assert await _chain_ok(sessions)


async def test_injected_gift_cards_are_blocked_even_when_the_merchant_is_allowed(sessions):
    async with sessions() as s:
        terms = DEMO_TERMS | {
            "mandate_id": "m_any_store",
            "allowed_merchant_ids": frozenset({"soundhub", "megadeals"}),
            "max_payment_amount": Decimal("500.00"),
        }
        await create_mandate(s, MandateTerms(**terms, created_at=NOW - timedelta(days=1)))
        await s.commit()
    cart = [{"sku": "md-sony-xm5", "quantity": 1}, {"sku": "md-giftcard-100", "quantity": 3}]

    attack = await _pay(
        sessions, FakePayPal(), mandate_id="m_any_store", merchant_id="megadeals", items=cart
    )
    assert attack["decision"] == "BLOCK" and _codes(attack) == ["mixed_categories"]
    honest = await _pay(
        sessions, FakePayPal(), mandate_id="m_any_store", merchant_id="megadeals", items=cart[:1]
    )
    assert honest["decision"] == "ALLOW"
    assert (await _usage(sessions, "m_any_store"))[:2] == (Decimal("149.00"), 1)


async def test_catalog_error_blocks_without_saving(sessions, monkeypatch):
    def broken():
        raise ValueError("catalog has sh-sony-xm5 twice")

    monkeypatch.setattr(orchestrator, "get_catalog", broken)
    result = await _pay(sessions, FakePayPal())

    assert result == orchestrator.internal_error()
    assert await _rows(sessions) == ([], [])


async def test_budget_uses_the_mandate_holds(sessions):
    for minutes in (30, 20):
        paid = await _pay(sessions, FakePayPal(), now=NOW - timedelta(minutes=minutes))
        assert paid["decision"] == "ALLOW"

    # $358.00 used of $500.00: another $179.00 does not fit.
    result = await _pay(sessions, FakePayPal())
    assert result["decision"] == "BLOCK" and _codes(result) == ["budget_exceeded"]
    assert (await _usage(sessions))[:2] == (Decimal("358.00"), 2)


async def test_payment_count_limit_uses_the_mandate_count(sessions):
    for hours in range(5, 0, -1):
        paid = await _pay(
            sessions, FakePayPal(), mandate_id="m_big", now=NOW - timedelta(hours=hours)
        )
        assert paid["decision"] == "ALLOW"

    result = await _pay(sessions, FakePayPal(), mandate_id="m_big")
    assert result["decision"] == "BLOCK" and _codes(result) == ["payment_count_exceeded"]


async def test_velocity_counts_payments_inside_window_only(sessions):
    for minutes in (11, 5, 1, 0):
        paid = await _pay(
            sessions, FakePayPal(), mandate_id="m_big", now=NOW - timedelta(minutes=minutes)
        )
        assert paid["decision"] == "ALLOW"

    result = await _pay(sessions, FakePayPal(), mandate_id="m_big")
    assert result["decision"] == "BLOCK" and _codes(result) == ["velocity_limit_exceeded"]


async def test_authorize_error_marks_failed_and_blocks(sessions):
    paypal = FakePayPal(authorize_error=PayPalError("create_order", 422, {}))
    result = await _pay(sessions, paypal)

    assert result["decision"] == "BLOCK" and result["status"] == S.FAILED
    assert _codes(result) == ["paypal_authorize_failed"]
    assert [c[0] for c in paypal.calls] == ["authorize"]
    (payment,), events = await _rows(sessions)
    assert payment.status == S.FAILED and payment.decision == "BLOCK"
    assert [e.event_type for e in events] == ["payment.decided", "payment.failed"]
    budget_used, count, (hold,) = await _usage(sessions)
    assert (budget_used, count) == (Decimal("0.00"), 0) and hold.released_at is not None
    assert await _chain_ok(sessions)


async def test_missing_vault_id_blocks_without_calling_paypal(sessions):
    paypal = FakePayPal()
    result = await _pay(sessions, paypal, vault_id="")

    assert result["decision"] == "BLOCK" and result["status"] == S.FAILED
    assert paypal.calls == []
    assert (await _usage(sessions))[:2] == (Decimal("0.00"), 0)


@pytest.mark.parametrize("capture_status", [PayPalError("capture", 500, {}), "DECLINED"])
async def test_capture_failure_voids_the_hold_and_blocks(sessions, capture_status):
    paypal = FakePayPal(capture_status=capture_status)
    result = await _pay(sessions, paypal)

    assert result["decision"] == "BLOCK" and result["status"] == S.VOIDED
    assert _codes(result) == ["paypal_capture_failed"]
    (payment,), events = await _rows(sessions)
    assert paypal.calls[-1] == ("void", "A-1", f"{payment.idempotency_key}-void")
    assert [e.event_type for e in events] == [
        "payment.decided",
        "payment.authorized",
        "payment.voided",
    ]
    budget_used, count, (hold,) = await _usage(sessions)
    assert (budget_used, count) == (Decimal("0.00"), 0) and hold.released_at is not None
    assert await _chain_ok(sessions)


async def test_void_failure_leaves_hold_authorized_and_blocks(sessions):
    paypal = FakePayPal(
        capture_status=PayPalError("capture", 500, {}), void_error=PayPalError("void", 500, {})
    )
    result = await _pay(sessions, paypal)

    assert result["decision"] == "BLOCK" and result["status"] == S.AUTHORIZED
    (payment,), events = await _rows(sessions)
    assert payment.status == S.AUTHORIZED and payment.decision == "BLOCK"
    assert events[-1].event_type == "payment.failed"
    assert events[-1].payload == {"void_failed": True, **orchestrator.CAPTURE_FAILED}
    budget_used, count, (hold,) = await _usage(sessions)
    assert (budget_used, count) == (Decimal("179.00"), 1) and hold.released_at is None
    assert await _chain_ok(sessions)


async def test_audit_verify_endpoint_passes_after_payments(sessions):
    assert (await _pay(sessions, FakePayPal()))["decision"] == "ALLOW"
    megadeals = {"merchant_id": "megadeals", "items": [{"sku": "md-sony-xm5", "quantity": 1}]}
    assert (await _pay(sessions, FakePayPal(), **megadeals))["decision"] == "BLOCK"

    async def session_override():
        async with sessions() as s:
            yield s

    app.dependency_overrides[db.get_session] = session_override
    try:
        # Two mandate.created, three events for the ALLOW, two for the BLOCK.
        assert TestClient(app).get("/audit/verify").json() == {
            "ok": True,
            "checked": 7,
            "first_bad_id": None,
        }
    finally:
        app.dependency_overrides.clear()


async def test_database_error_blocks():
    def broken():
        raise RuntimeError("db down")

    result = await _pay(broken, FakePayPal())
    assert result == orchestrator.internal_error()


async def test_mcp_tool_is_registered():
    tools = {t.name: t for t in await mcp_server.mcp.list_tools()}
    schema = tools["request_payment"].inputSchema
    assert set(schema["required"]) == {"mandate_id", "merchant_id", "items"}
    assert schema["properties"]["items"]["type"] == "array"
    assert "amount" not in schema["properties"]


async def test_mcp_tool_blocks_when_setup_fails(monkeypatch):
    def broken():
        raise RuntimeError("no DATABASE_URL")

    monkeypatch.setattr(mcp_server, "get_sessionmaker", broken)
    result = await mcp_server.request_payment("m_demo", "soundhub", [SONY])
    assert result == orchestrator.internal_error()
