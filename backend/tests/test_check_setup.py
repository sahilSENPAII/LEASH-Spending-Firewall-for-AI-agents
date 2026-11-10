from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text, update

from app import mandates
from app.audit import append_audit
from app.config import Settings
from app.mandates import MandateTerms, create_mandate, generate_signing_key
from app.models import AuditEvent
from scripts import check_setup
from scripts.check_setup import alembic_head, key_fingerprint, report, run_checks

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
KEY = generate_signing_key()


@pytest.fixture
async def seeded(sessions, monkeypatch):
    monkeypatch.setattr(mandates, "get_settings", lambda: Settings(mandate_signing_key=KEY))
    async with sessions() as s:
        await s.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await s.execute(text("INSERT INTO alembic_version VALUES ('0003')"))
        await create_mandate(
            s,
            MandateTerms(
                mandate_id="m_demo",
                intent="Sony or Bose noise-cancelling headphones",
                category="electronics",
                max_payment_amount=Decimal("200.00"),
                total_budget=Decimal("500.00"),
                max_payment_count=5,
                allowed_merchant_ids=frozenset({"soundhub"}),
                expires_at=NOW + timedelta(days=30),
                created_at=NOW,
            ),
        )
        await s.commit()
    return sessions


async def _run(sessions, key=KEY):
    async with sessions() as s:
        return {c.name: c for c in await run_checks(s, Settings(mandate_signing_key=key))}


async def test_correct_setup_passes_every_check(seeded):
    checks = await _run(seeded)
    assert all(c.ok for c in checks.values()), checks
    assert checks["signing key"].detail == f"loaded, fingerprint {key_fingerprint(KEY)}"
    assert checks["audit chain"].detail == "1 entries, all links valid"


async def test_wrong_key_fails_the_mandate_check_only(seeded):
    checks = await _run(seeded, key=generate_signing_key())
    assert checks["signing key"].ok and checks["migration"].ok and checks["audit chain"].ok
    assert not checks["m_demo"].ok
    assert "mandate_signature_invalid" in checks["m_demo"].detail


async def test_missing_key_fails_key_and_mandate(seeded):
    checks = await _run(seeded, key="")
    assert not checks["signing key"].ok
    assert "MANDATE_SIGNING_KEY is not set" in checks["signing key"].detail
    assert not checks["m_demo"].ok


async def test_old_migration_fails(seeded):
    async with seeded() as s:
        await s.execute(text("UPDATE alembic_version SET version_num = '0001'"))
        await s.commit()
    checks = await _run(seeded)
    assert not checks["migration"].ok and "0001" in checks["migration"].detail


async def test_missing_mandate_and_alembic_table_fail(sessions):
    checks = await _run(sessions)
    assert not checks["migration"].ok
    assert not checks["m_demo"].ok and "not found" in checks["m_demo"].detail
    assert checks["audit chain"].ok  # empty chain is valid


async def test_broken_audit_chain_fails(seeded):
    async with seeded() as s:
        await append_audit(s, event_type="payment.decided", payment_id="p1", payload={}, now=NOW)
        await s.execute(update(AuditEvent).values(event_type="tampered"))
        await s.commit()
    checks = await _run(seeded)
    assert not checks["audit chain"].ok and "broken at entry 1" in checks["audit chain"].detail


async def test_checks_never_write(seeded):
    async with seeded() as s:
        before = (await s.execute(text("SELECT count(*) FROM audit_events"))).scalar()
    await _run(seeded)
    async with seeded() as s:
        assert (await s.execute(text("SELECT count(*) FROM audit_events"))).scalar() == before


def test_report_format():
    from scripts.check_setup import Check

    text_out = report([Check("a", True, "fine"), Check("b", False, "broken")])
    assert text_out.splitlines() == [
        "OK    a            fine",
        "FAIL  b            broken",
        "1/2 checks passed",
    ]


def test_fingerprint_is_stable_and_not_the_key():
    assert key_fingerprint(KEY) == key_fingerprint(KEY)
    assert len(key_fingerprint(KEY)) == 12
    assert key_fingerprint(KEY) not in KEY


async def test_main_without_database_url_reports_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.setattr(check_setup, "get_settings", lambda: Settings(database_url=""))
    assert await check_setup.main() == 1
    assert "FAIL  database     DATABASE_URL is not set in .env" in capsys.readouterr().out


async def test_main_with_unreachable_database_reports(monkeypatch, capsys):
    def broken():
        raise OSError("connection refused")

    monkeypatch.setattr(check_setup, "get_settings", lambda: Settings(database_url="postgres://x"))
    monkeypatch.setattr(check_setup, "get_sessionmaker", broken)
    assert await check_setup.main() == 1
    assert "cannot connect: OSError: connection refused" in capsys.readouterr().out


async def test_main_passes_on_a_good_setup(seeded, monkeypatch, capsys):
    settings = Settings(database_url="sqlite://", mandate_signing_key=KEY)
    monkeypatch.setattr(check_setup, "get_settings", lambda: settings)
    monkeypatch.setattr(check_setup, "get_sessionmaker", lambda: seeded)
    assert await check_setup.main() == 0
    assert "5/5 checks passed" in capsys.readouterr().out


def test_expected_migration_is_the_newest_in_the_repo():
    assert alembic_head() == "0003"


async def test_a_newer_migration_is_expected_once_it_exists(seeded, monkeypatch):
    async with seeded() as s:
        await s.execute(text("UPDATE alembic_version SET version_num = '0003'"))
        await s.commit()
    monkeypatch.setattr(check_setup, "alembic_head", lambda: "0003")
    checks = await _run(seeded)
    assert checks["migration"].ok and checks["migration"].detail.endswith("expected 0003")
