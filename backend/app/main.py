"""FastAPI entrypoint. Run: uv run uvicorn app.main:app --reload (from backend/)."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db

app = FastAPI(title="Leash", version="0.1.0")


@app.get("/health")
async def health() -> JSONResponse:
    db_ok = await db.ping()
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unreachable"},
    )
