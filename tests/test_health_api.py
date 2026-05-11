from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.health_routes as health_routes
from src.api.health_routes import router as health_router


def _build_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(health_router, prefix="/v1")
    return TestClient(app)


def test_health_livez_returns_ok():
    client = _build_test_client()

    r = client.get("/health/livez")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "aegis-agent"


def test_health_readyz_returns_503_on_dependency_failure(monkeypatch):
    client = _build_test_client()

    def _fake_checks():
        return {"supabase": {"status": "fail", "error": "boom"}}

    monkeypatch.setattr(health_routes, "_run_dependency_checks", _fake_checks)

    r = client.get("/health/readyz")

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["supabase"]["status"] == "fail"


def test_health_readyz_returns_200_when_ready(monkeypatch):
    client = _build_test_client()

    def _fake_checks():
        return {"supabase": {"status": "ok", "error": None}}

    monkeypatch.setattr(health_routes, "_run_dependency_checks", _fake_checks)

    r = client.get("/health/readyz")

    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_compat_and_v1_alias(monkeypatch):
    client = _build_test_client()

    def _fake_checks():
        return {"supabase": {"status": "ok", "error": None}}

    monkeypatch.setattr(health_routes, "_run_dependency_checks", _fake_checks)

    r1 = client.get("/health")
    r2 = client.get("/v1/health")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["readiness"] == "ok"
    assert r2.json()["status"] == "ok"
