"""Scheduler control plane (secret-protected) + a sanitized public status feed."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from src.api.blog_internal import verify_internal_secret
from src.scheduler.service import Scheduler, get_scheduler

internal_router = APIRouter(
    prefix="/internal/scheduler",
    tags=["internal"],
    dependencies=[Depends(verify_internal_secret)],
)
public_router = APIRouter(tags=["automation"])


def _job_or_404(scheduler: Scheduler, name: str) -> None:
    if name not in scheduler.jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown job {name!r}. Known: {', '.join(scheduler.jobs)}",
        )


@internal_router.get("/status", summary="Full scheduler state + recent runs")
def scheduler_status(scheduler: Scheduler = Depends(get_scheduler)) -> dict[str, Any]:
    out = scheduler.status()
    try:
        out["recent_runs"] = scheduler.store.recent_runs(limit=20)
    except Exception as exc:
        out["recent_runs"] = []
        out["recent_runs_error"] = str(exc)
    return out


@internal_router.post(
    "/jobs/{name}/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run a job now in the background (does not consume its schedule slot)",
)
def scheduler_run_job(
    name: str,
    background: BackgroundTasks,
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    _job_or_404(scheduler, name)
    background.add_task(scheduler.run_job, name, trigger="manual")
    return {"job": name, "status": "accepted", "watch": "GET /internal/scheduler/status"}


@internal_router.post("/jobs/{name}/pause", summary="Pause a job's schedule")
def scheduler_pause_job(name: str, scheduler: Scheduler = Depends(get_scheduler)) -> dict[str, Any]:
    _job_or_404(scheduler, name)
    scheduler.set_enabled(name, False)
    return {"job": name, "enabled": False}


@internal_router.post("/jobs/{name}/resume", summary="Resume a paused job")
def scheduler_resume_job(name: str, scheduler: Scheduler = Depends(get_scheduler)) -> dict[str, Any]:
    _job_or_404(scheduler, name)
    scheduler.set_enabled(name, True)
    return {"job": name, "enabled": True}


@internal_router.post(
    "/tick",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Evaluate schedules now and run anything due (background)",
)
def scheduler_tick(background: BackgroundTasks, scheduler: Scheduler = Depends(get_scheduler)) -> dict[str, str]:
    background.add_task(scheduler.tick)
    return {"status": "accepted"}


@public_router.get("/automation/status", summary="Public, sanitized view of the content pipeline")
def automation_status(scheduler: Scheduler = Depends(get_scheduler)) -> dict[str, Any]:
    return scheduler.status(public=True)
