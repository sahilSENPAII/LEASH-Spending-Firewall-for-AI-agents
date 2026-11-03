"""FastAPI entrypoint. Run: uv run uvicorn app.main:app --reload (from backend/)."""

from dataclasses import asdict
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.audit import verify_chain

app = FastAPI(title="Leash", version="0.1.0")


@app.get("/health")
async def health() -> JSONResponse:
    db_ok = await db.ping()
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unreachable"},
    )


@app.get("/audit/verify")
async def audit_verify(session: Annotated[AsyncSession, Depends(db.get_session)]) -> dict:
    """Recompute the audit hash chain: {ok, checked, first_bad_id} (docs/CONTRACTS.md 5.6)."""
    return asdict(await verify_chain(session))
