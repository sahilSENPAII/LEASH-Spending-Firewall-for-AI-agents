"""FastAPI entrypoint: REST API plus MCP at /mcp. Run: uv run uvicorn app.main:app --reload
(from backend/)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db, mcp_server
from app.config import get_settings
from app.read_api import router as read_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Serves /mcp. The SDK lets a session manager run only once per process.
    async with mcp_server.mcp.session_manager.run():
        yield


app = FastAPI(title="Leash", version="0.1.0", lifespan=lifespan)
# The audit console runs on its own origin (Vite dev server, or the hosted frontend).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(read_router)
# The agent-facing MCP tools over streamable HTTP, on the same service (app/mcp_server.py).
app.add_route("/mcp", mcp_server.http_app(), include_in_schema=False)


@app.get("/health")
async def health() -> JSONResponse:
    db_ok = await db.ping()
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unreachable"},
    )
