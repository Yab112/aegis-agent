from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from supabase import create_client

from config.settings import get_settings

router = APIRouter(tags=["health"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_supabase() -> tuple[str, str | None]:
    settings = get_settings()
    try:
        client = create_client(settings.supabase_url, settings.supabase_service_key)
        client.table("blog_posts").select("id").limit(1).execute()
        return "ok", None
    except Exception as e:
        return "fail", str(e)


def _run_dependency_checks() -> dict[str, Any]:
    supabase_status, supabase_error = _check_supabase()
    checks: dict[str, Any] = {
        "supabase": {
            "status": supabase_status,
            "error": supabase_error,
        },
    }
    return checks


def _ready_payload() -> tuple[dict[str, Any], int]:
    checks = _run_dependency_checks()
    failures = [name for name, val in checks.items() if val.get("status") != "ok"]
    ready = not failures
    payload = {
        "status": "ok" if ready else "degraded",
        "service": "aegis-agent",
        "version": "1.0.0",
        "timestamp": _utc_now_iso(),
        "checks": checks,
    }
    return payload, (200 if ready else 503)


@router.get("/health/livez")
def health_livez() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "aegis-agent",
        "version": "1.0.0",
        "timestamp": _utc_now_iso(),
    }


@router.get("/health/readyz")
def health_readyz() -> JSONResponse:
    payload, code = _ready_payload()
    return JSONResponse(status_code=code, content=payload)


@router.get("/health")
def health_compat() -> JSONResponse:
    payload, code = _ready_payload()
    payload["liveness"] = "ok"
    payload["readiness"] = payload["status"]
    return JSONResponse(status_code=code, content=payload)
