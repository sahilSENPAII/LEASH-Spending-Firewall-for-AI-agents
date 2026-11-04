"""FastAPI entrypoint. Run: uv run uvicorn app.main:app --reload (from backend/)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import get_settings
from app.read_api import router as read_router

app = FastAPI(title="Leash", version="0.1.0")
# The audit console runs on its own origin (Vite dev server, or the hosted frontend).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(read_router)


@app.get("/health")
async def health() -> JSONResponse:
    db_ok = await db.ping()
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unreachable"},
    )
