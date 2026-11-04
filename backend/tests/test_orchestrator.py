import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import select

from app import anomaly, db, mandates, mcp_server, orchestrator
from app.anomaly import AnomalyResult
from app.audit import verify_chain
from app.catalog import DEFAULT_CATALOG_PATH, load_catalog
from app.config import Settings
from app.main import app
from app.mandates import (
    MandateTerms,
    create_mandate,
    generate_signing_key,
    get_mandate,
    revoke_mandate,
)
from app.models import AuditEvent, BudgetHold, Payment
from app.paypal import Authorization, PayPalError
from app.state import PaymentStatus
from scripts.seed_demo_mandate import DEMO_TERMS

S = PaymentStatus
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
KEY_B64 = generate_signing_key()
SONY = {"sku": "sh-sony-xm5", "quantity": 1}  # $179.00 at SoundHub


class FakeModel:
    """Stands in for score_anomaly; records the features it was given."""

    def __init__(self, score=0.05, error=None):
        self.score = score
        self.error = error
        self.features = []

    def __call__(self, features):
        self.features.append(features)
        if self.error:
            raise self.error
        return AnomalyResult(score=self.score, reasons=("amount $179.00",), model_version="test-v1")


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
        mandate_signing_key=KEY_B64,
        velocity_max_payments=3,
        velocity_window_seconds=600,
        anomaly_step_up_threshold=0.5,
        anomaly_block_threshold=0.9,
    )
    monkeypatch.setattr(mandates, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "get_settings", lambda: settings)
    # A low risk score unless a test says otherwise; one test below uses the real model.
    monkeypatch.setattr(orchestrator, "score_anomaly", FakeModel())
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
    assert result["status"] == S.CAPTURED and result["risk_score"] == "0.050000"
    (payment,), events = await _rows(sessions)
    assert payment.id == result["payment_id"]
    assert payment.status == S.CAPTURED and payment.decision == "ALLOW"
    assert payment.paypal_order_id == "O-1" and payment.paypal_authorization_id == "A-1"
    assert json.loads(payment.items) == [SONY | {"unit_price": "179.00"}]
    assert [e.event_type for e in events] == [
        "payment.decided",
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


async def test_policy_block_never_calls_paypal_or_the_model(sessions, monkeypatch):
    paypal, model = FakePayPal(), FakeModel()
    monkeypatch.setattr(orchestrator, "score_anomaly", model)
    two_sony = [{"sku": "md-sony-xm5", "quantity": 2}]  # $298.00
    result = await _pay(sessions, paypal, merchant_id="megadeals", items=two_sony)

    assert result["decision"] == "BLOCK" and result["status"] == S.BLOCKED
    assert result["risk_score"] is None and model.features == []
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
    assert result["risk_score"] is None
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


@pytest.mark.parametrize(
    ("model", "decision", "status", "codes", "last_event"),
    [
        (FakeModel(0.4999), "ALLOW", S.CAPTURED, [], "payment.captured"),
        (FakeModel(0.5), "STEP_UP", S.AWAITING_APPROVAL, ["anomaly_step_up"], "approval.requested"),
        (
            FakeModel(0.8999),
            "STEP_UP",
            S.AWAITING_APPROVAL,
            ["anomaly_step_up"],
            "approval.requested",
        ),
        (FakeModel(0.9), "BLOCK", S.BLOCKED, ["anomaly_block"], "payment.blocked"),
        (FakeModel(1.0), "BLOCK", S.BLOCKED, ["anomaly_block"], "payment.blocked"),
        (
            FakeModel(error=ValueError("bad features")),
            "STEP_UP",
            S.AWAITING_APPROVAL,
            ["anomaly_check_failed"],
            "approval.requested",
        ),
        (
            FakeModel(float("nan")),
            "STEP_UP",
            S.AWAITING_APPROVAL,
            ["anomaly_check_failed"],
            "approval.requested",
        ),
    ],
)
async def test_risk_score_can_only_make_an_allowed_payment_stricter(
    sessions, monkeypatch, model, decision, status, codes, last_event
):
    model.features.clear()
    monkeypatch.setattr(orchestrator, "score_anomaly", model)
    paypal = FakePayPal()
    result = await _pay(sessions, paypal)

    assert (result["decision"], result["status"], _codes(result)) == (decision, status, codes)
    (payment,), events = await _rows(sessions)
    assert (payment.decision, payment.status, payment.reasons) == (
        decision,
        status,
        result["reasons"],
    )
    # Features exclude this payment's own hold: m_demo had nothing before it.
    (features,) = model.features
    assert (features.amount, features.payment_count, features.budget_used_ratio) == (
        Decimal("179.00"),
        0,
        0.0,
    )

    t1, t2 = events[:2]
    assert t1.event_type == t2.event_type == "payment.decided"
    assert t1.payload["decision"] == "ALLOW"
    if model.error or model.score != model.score:  # raised, or NaN
        assert result["risk_score"] is None
        assert t2.payload == {
            "decision": "STEP_UP",
            "reasons": result["reasons"],
            "risk_score": None,
            "anomaly_reasons": [],
            "model_version": None,
        }
    else:
        assert result["risk_score"] == f"{model.score:.6f}"
        assert t2.payload == {
            "decision": decision,
            "reasons": result["reasons"],
            "risk_score": f"{model.score:.6f}",
            "anomaly_reasons": ["amount $179.00"],
            "model_version": "test-v1",
        }
    assert events[-1].event_type == last_event

    budget_used, count, (hold,) = await _usage(sessions)
    if decision == "BLOCK":
        assert (budget_used, count) == (Decimal("0.00"), 0) and hold.released_at is not None
    else:
        assert (budget_used, count) == (Decimal("179.00"), 1) and hold.released_at is None
    assert [c[0] for c in paypal.calls] == (["authorize", "capture"] if decision == "ALLOW" else [])
    assert await _chain_ok(sessions)


async def test_feature_read_error_steps_up(sessions, monkeypatch):
    async def broken(session, **kwargs):
        raise LookupError("mandate 'm_demo' not found")

    monkeypatch.setattr(orchestrator, "build_features", broken)
    paypal = FakePayPal()
    result = await _pay(sessions, paypal)

    assert result["decision"] == "STEP_UP" and result["status"] == S.AWAITING_APPROVAL
    assert _codes(result) == ["anomaly_check_failed"] and paypal.calls == []
    assert (await _usage(sessions))[:2] == (Decimal("179.00"), 1)
    assert await _chain_ok(sessions)


async def test_real_model_allows_the_normal_soundhub_purchase(sessions, monkeypatch):
    monkeypatch.setattr(orchestrator, "score_anomaly", anomaly.score_anomaly)
    # 12:00 UTC. The model scores this same purchase >= 0.5 from 19:00 to 01:59 UTC.
    result = await _pay(sessions, FakePayPal())

    assert result["decision"] == "ALLOW" and result["status"] == S.CAPTURED
    assert float(result["risk_score"]) < 0.1
    _, events = await _rows(sessions)
    assert events[1].payload["model_version"].startswith("synthetic-")
    assert await _chain_ok(sessions)


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
    assert [e.event_type for e in events] == [
        "payment.decided",
        "payment.decided",
        "payment.failed",
    ]
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
        # Two mandate.created, four events for the ALLOW, two for the BLOCK.
        assert TestClient(app).get("/audit/verify").json() == {
            "ok": True,
            "checked": 8,
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
    assert set(tools) == {"request_payment", "list_mandates", "get_payment_status"}
    assert tools["list_mandates"].inputSchema.get("required", []) == []
    assert tools["get_payment_status"].inputSchema["required"] == ["payment_id"]
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


@pytest.fixture
def mcp_env(sessions, monkeypatch):
    """The MCP tools on the test database, with fake PayPal and a known bearer token."""
    paypal = FakePayPal()
    settings = Settings(_env_file=None, paypal_vault_id="VAULT", mcp_auth_token="s3cret-token")
    monkeypatch.setattr(mcp_server, "get_sessionmaker", lambda: sessions)
    monkeypatch.setattr(mcp_server, "get_paypal", lambda: paypal)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    return paypal, settings


async def test_list_mandates_shows_what_is_left_with_money_as_strings(sessions, mcp_env):
    assert (await _pay(sessions, FakePayPal()))["decision"] == "ALLOW"
    async with sessions() as s:
        await revoke_mandate(s, "m_big", NOW)
        await s.commit()

    result = await mcp_server.list_mandates()

    # Same created_at, so Anurag's list_mandates orders them by id.
    assert result == {
        "mandates": [
            {
                "mandate_id": "m_big",
                "intent": DEMO_TERMS["intent"],
                "category": "electronics",
                "max_payment_amount": "200.00",
                "remaining_budget": "5000.00",
                "remaining_count": 5,
                "expires_at": "2026-12-31T00:00:00Z",
                "revoked": True,
            },
            {
                "mandate_id": "m_demo",
                "intent": DEMO_TERMS["intent"],
                "category": "electronics",
                "max_payment_amount": "200.00",
                "remaining_budget": "321.00",
                "remaining_count": 4,
                "expires_at": "2026-12-31T00:00:00Z",
                "revoked": False,
            },
        ]
    }


async def test_get_payment_status_returns_the_decision_response(sessions, mcp_env):
    paid = await _pay(sessions, FakePayPal())
    blocked = await _pay(sessions, FakePayPal(), merchant_id="megadeals", items=[])

    status = await mcp_server.get_payment_status(paid["payment_id"])
    assert {k: status[k] for k in paid} == paid
    assert status["amount"] == "179.00" and status["approval_url"] is None
    assert "paypal_order_id" not in status and "paypal_authorization_id" not in status

    status = await mcp_server.get_payment_status(blocked["payment_id"])
    assert (status["decision"], status["status"]) == ("BLOCK", S.BLOCKED)
    assert [r["code"] for r in status["reasons"]] == ["empty_cart"]
    assert status["risk_score"] is None


async def test_get_payment_status_unknown_id_is_a_clear_tool_error(mcp_env):
    with pytest.raises(ToolError, match="No payment with id 'p-nope'"):
        await mcp_server.get_payment_status("p-nope")


async def test_read_tools_hide_database_errors(monkeypatch):
    def broken():
        raise RuntimeError("password=hunter2 host=db.internal")

    monkeypatch.setattr(mcp_server, "get_sessionmaker", broken)
    with pytest.raises(ToolError) as caught:
        await mcp_server.list_mandates()
    assert str(caught.value) == "Leash could not read the mandates."
    with pytest.raises(ToolError) as caught:
        await mcp_server.get_payment_status("p-1")
    assert str(caught.value) == "Leash could not read the payment."


MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer wrong",
        "bearer s3cret-token",
        "s3cret-token",
        "Bearer s3cret-tokenX",
    ],
)
def test_mcp_over_http_needs_the_bearer_token(mcp_env, authorization):
    headers = MCP_HEADERS | ({"Authorization": authorization} if authorization is not None else {})
    r = TestClient(app).post("/mcp", headers=headers, json=_rpc("tools/list"))

    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json() == {"detail": "Missing or wrong bearer token."}


def test_mcp_over_http_is_off_without_a_token(monkeypatch):
    settings = Settings(_env_file=None, mcp_auth_token="")
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    for headers in (MCP_HEADERS, MCP_HEADERS | {"Authorization": "Bearer "}):
        r = TestClient(app).post("/mcp", headers=headers, json=_rpc("tools/list"))
        assert r.status_code == 503
        assert "MCP_AUTH_TOKEN" in r.json()["detail"]


async def test_rest_routes_stay_open_without_the_token(sessions, mcp_env):
    async def session_override():
        async with sessions() as s:
            yield s

    app.dependency_overrides[db.get_session] = session_override
    try:
        assert TestClient(app).get("/audit/verify").status_code == 200
        assert TestClient(app).get("/payments").json() == []
    finally:
        app.dependency_overrides.clear()


async def test_request_payment_end_to_end_over_http(sessions, mcp_env):
    paypal, settings = mcp_env
    headers = MCP_HEADERS | {"Authorization": f"Bearer {settings.mcp_auth_token}"}
    # The only test that starts the app's lifespan: the SDK runs its session manager once per
    # process.
    with TestClient(app) as client:
        call = _rpc(
            "tools/call",
            {
                "name": "request_payment",
                "arguments": {"mandate_id": "m_demo", "merchant_id": "soundhub", "items": [SONY]},
            },
        )
        r = client.post("/mcp", headers=headers, json=call)
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["isError"] is False
        # The decision is one JSON text block, which is what the agent reads.
        (content,) = result["content"]
        response = json.loads(content["text"])
        assert response["decision"] == "ALLOW" and response["status"] == S.CAPTURED
        assert [c[0] for c in paypal.calls] == ["authorize", "capture"]

        status_call = _rpc(
            "tools/call",
            {"name": "get_payment_status", "arguments": {"payment_id": response["payment_id"]}},
            id=2,
        )
        status = client.post("/mcp", headers=headers, json=status_call).json()["result"]
        assert json.loads(status["content"][0]["text"])["status"] == S.CAPTURED

        unknown = _rpc(
            "tools/call", {"name": "get_payment_status", "arguments": {"payment_id": "nope"}}, id=3
        )
        error = client.post("/mcp", headers=headers, json=unknown).json()["result"]
        assert error["isError"] is True
        assert "No payment with id 'nope'" in error["content"][0]["text"]

    assert await _chain_ok(sessions)
