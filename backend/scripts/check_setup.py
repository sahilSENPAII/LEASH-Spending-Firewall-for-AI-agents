"""Read-only check that .env matches the database: key, migration, m_demo and the audit chain.

Never writes. Safe to run against Neon at any time (Sahil, Anurag, Render shell):
cd backend && uv run python -m scripts.check_setup

The key fingerprint is a hash of the PUBLIC key, so two people can compare it to confirm they use
the same MANDATE_SIGNING_KEY without sharing the key itself.
"""

import asyncio
import base64
import hashlib
import sys
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import verify_chain
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.mandates import get_mandate, load_signing_key, public_key_b64, verify_mandate

EXPECTED_REVISION = "0002"
DEMO_MANDATE_ID = "m_demo"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def key_fingerprint(key_b64: str) -> str:
    """First 12 hex characters of sha256(public key). Safe to share."""
    public = base64.b64decode(public_key_b64(load_signing_key(key_b64)))
    return hashlib.sha256(public).hexdigest()[:12]


async def run_checks(session: AsyncSession, settings: Settings) -> list[Check]:
    checks = []
    public_key = None
    try:
        public_key = load_signing_key(settings.mandate_signing_key).public_key()
        fingerprint = key_fingerprint(settings.mandate_signing_key)
        checks.append(Check("signing key", True, f"loaded, fingerprint {fingerprint}"))
    except ValueError as exc:
        checks.append(Check("signing key", False, str(exc)))

    try:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        checks.append(
            Check(
                "migration",
                revision == EXPECTED_REVISION,
                f"database is at {revision}, expected {EXPECTED_REVISION}",
            )
        )
    except Exception as exc:
        await session.rollback()
        checks.append(Check("migration", False, f"cannot read alembic_version: {exc}"))

    try:
        record = await get_mandate(session, DEMO_MANDATE_ID)
        if record is None:
            checks.append(Check(DEMO_MANDATE_ID, False, "not found; run the seed once"))
        elif public_key is None:
            checks.append(Check(DEMO_MANDATE_ID, False, "found, but no valid key to verify it"))
        else:
            check = verify_mandate(record, public_key=public_key)
            detail = (
                "signature valid with this key"
                if check.signature_valid
                else "signature INVALID: " + ", ".join(r.code for r in check.reasons)
            )
            checks.append(Check(DEMO_MANDATE_ID, check.signature_valid, detail))
    except Exception as exc:
        await session.rollback()
        checks.append(Check(DEMO_MANDATE_ID, False, f"cannot read mandates: {exc}"))

    try:
        chain = await verify_chain(session)
        detail = (
            f"{chain.checked} entries, all links valid"
            if chain.ok
            else f"broken at entry {chain.first_bad_id} after {chain.checked} good entries"
        )
        checks.append(Check("audit chain", chain.ok, detail))
    except Exception as exc:
        checks.append(Check("audit chain", False, f"cannot read audit_events: {exc}"))

    await session.rollback()  # read-only: never commit anything
    return checks


def report(checks: list[Check]) -> str:
    lines = [f"{'OK  ' if c.ok else 'FAIL'}  {c.name:12s} {c.detail}" for c in checks]
    passed = sum(c.ok for c in checks)
    lines.append(f"{passed}/{len(checks)} checks passed")
    return "\n".join(lines)


async def main() -> int:
    settings = get_settings()
    if not settings.database_url:
        checks = [Check("database", False, "DATABASE_URL is not set in .env")]
    else:
        try:
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
                checks = [Check("database", True, "reachable")]
                checks += await run_checks(session, settings)
        except Exception as exc:
            checks = [Check("database", False, f"cannot connect: {type(exc).__name__}: {exc}")]
    print(report(checks))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
