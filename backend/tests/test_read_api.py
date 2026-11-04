from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import db
from app.audit import append_audit
from app.main import app
from app.models import AuditEvent, Payment

NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


@pytest.fixture
async def client(sessions):
    async def session_override():
        async with sessions() as s:
            yield s

    app.dependency_overrides[db.get_session] = session_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


async def _seed(sessions):
    async with sessions() as s:
        older = Payment(id="p-old", mandate_id="m_demo", merchant_id="soundhub",
                        amount=Decimal("179"), items="1 x sh-sony-xm5", status="CAPTURED",
                        decision="ALLOW", reasons=[], created_at=NOW - timedelta(minutes=5),
                        updated_at=NOW - timedelta(minutes=5))
        newer = Payment(id="p-new", mandate_id="m_demo", merchant_id="megadeals",
                        amount=Decimal("449.00"), items="1 x md-sony-xm5, 3 x md-giftcard-100",
                        status="BLOCKED", decision="BLOCK",
                        reasons=[{"code": "merchant_not_allowed", "message": "no"}],
                        created_at=NOW, updated_at=NOW)
        s.add_all([older, newer])
        await s.flush()
        await append_audit(s, event_type="payment.decided", payment_id="p-old",
                           payload={"decision": "ALLOW"}, now=NOW - timedelta(minutes=5))
        # A later decided entry (after the judge and model ran) wins.
        await append_audit(s, event_type="payment.decided", payment_id="p-old",
                           payload={"decision": "ALLOW", "risk_score": "0.0457",
                                    "judge": {"match": True, "score": "0.05", "reason": "ok"}},
                           now=NOW - timedelta(minutes=5))
        await append_audit(s, event_type="payment.decided", payment_id="p-new",
                           payload={"decision": "BLOCK"}, now=NOW)
        await append_audit(s, event_type="payment.blocked", payment_id="p-new",
                           payload={"status": "BLOCKED"}, now=NOW)
        await s.commit()


async def test_payments_newest_first_in_the_decision_shape(client, sessions):
    await _seed(sessions)
    body = client.get("/payments").json()
    assert [p["payment_id"] for p in body] == ["p-new", "p-old"]
    new, old = body
    assert new == {
        "payment_id": "p-new",
        "decision": "BLOCK",
        "status": "BLOCKED",
        "reasons": [{"source": None, "code": "merchant_not_allowed", "message": "no"}],
        "risk_score": None,
        "judge": None,
        "approval_url": None,
        "mandate_id": "m_demo",
        "merchant_id": "megadeals",
        "amount": "449.00",
        "currency": "USD",
        "items": "1 x md-sony-xm5, 3 x md-giftcard-100",
        "created_at": "2026-11-05T10:00:00Z",
        "updated_at": "2026-11-05T10:00:00Z",
    }
    assert old["amount"] == "179.00"  # money is always a 2-decimal string
    assert old["risk_score"] == "0.0457"
    assert old["judge"] == {"match": True, "score": "0.05", "reason": "ok"}


async def test_payments_limit(client, sessions):
    await _seed(sessions)
    assert [p["payment_id"] for p in client.get("/payments?limit=1").json()] == ["p-new"]
    assert client.get("/payments?limit=0").status_code == 422
    assert client.get("/payments?limit=501").status_code == 422


async def test_one_payment_and_unknown_payment(client, sessions):
    await _seed(sessions)
    assert client.get("/payments/p-old").json()["risk_score"] == "0.0457"
    missing = client.get("/payments/nope")
    assert missing.status_code == 404 and missing.json() == {"detail": "payment not found"}


async def test_audit_oldest_first_with_paging(client, sessions):
    await _seed(sessions)
    first = client.get("/audit?limit=2").json()
    assert [e["id"] for e in first] == [1, 2]
    assert set(first[0]) == {"id", "created_at", "event_type", "payment_id", "payload",
                             "prev_hash", "hash"}
    assert first[0]["created_at"] == "2026-11-05T09:55:00Z"
    assert first[1]["prev_hash"] == first[0]["hash"]
    rest = client.get(f"/audit?after_id={first[-1]['id']}").json()
    assert [e["event_type"] for e in rest] == ["payment.decided", "payment.blocked"]
    assert client.get("/audit?after_id=99").json() == []
    assert client.get("/audit?limit=0").status_code == 422
    assert client.get("/audit?after_id=-1").status_code == 422


async def test_audit_verify_still_works(client, sessions):
    await _seed(sessions)
    assert client.get("/audit/verify").json() == {"ok": True, "checked": 4, "first_bad_id": None}


async def test_reads_never_write(client, sessions):
    await _seed(sessions)
    for path in ("/payments", "/payments/p-old", "/audit", "/audit/verify"):
        assert client.get(path).status_code == 200
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(AuditEvent)) == 4
        assert await s.scalar(select(func.count()).select_from(Payment)) == 2


def test_cors_allows_the_console_origin_only():
    client = TestClient(app)
    ok = client.options("/payments", headers={"Origin": "http://localhost:5173",
                                              "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = client.options("/payments", headers={"Origin": "https://evil.test",
                                                 "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in other.headers
