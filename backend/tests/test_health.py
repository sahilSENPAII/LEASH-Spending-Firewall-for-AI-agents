from fastapi.testclient import TestClient

from app import db
from app.main import app


def test_health_ok(monkeypatch):
    async def ok():
        return True

    monkeypatch.setattr(db, "ping", ok)
    r = TestClient(app).get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "db": "ok"}


def test_health_reports_db_down(monkeypatch):
    async def down():
        return False

    monkeypatch.setattr(db, "ping", down)
    r = TestClient(app).get("/health")
    assert r.status_code == 503 and r.json()["db"] == "unreachable"
