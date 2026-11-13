"""Durable scheduler state.

State lives in Supabase (``scheduler_jobs`` + ``scheduler_job_runs``, see
``scripts/supabase_scheduler.sql``) so it survives restarts and free-tier
sleep. ``InMemorySchedulerStore`` mirrors the same contract for tests.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


class SchedulerStore(Protocol):
    def get_job(self, name: str) -> dict[str, Any] | None: ...
    def list_jobs(self) -> list[dict[str, Any]]: ...
    def create_job(self, name: str, cron: str, last_slot: datetime | None) -> None: ...
    def claim(self, name: str, owner: str, lease_seconds: int) -> bool: ...
    def update_job(self, name: str, fields: dict[str, Any]) -> None: ...
    def record_run(self, row: dict[str, Any]) -> None: ...
    def recent_runs(self, limit: int = 20, job: str | None = None) -> list[dict[str, Any]]: ...


class SupabaseSchedulerStore:
    def __init__(self, supabase_url: str, supabase_service_key: str) -> None:
        from supabase import create_client

        self._client = create_client(supabase_url, supabase_service_key)

    def get_job(self, name: str) -> dict[str, Any] | None:
        res = self._client.table("scheduler_jobs").select("*").eq("job_name", name).limit(1).execute()
        return (res.data or [None])[0]

    def list_jobs(self) -> list[dict[str, Any]]:
        res = self._client.table("scheduler_jobs").select("*").order("job_name").execute()
        return list(res.data or [])

    def create_job(self, name: str, cron: str, last_slot: datetime | None) -> None:
        self._client.table("scheduler_jobs").upsert(
            {"job_name": name, "cron": cron, "last_slot": _iso(last_slot)},
            on_conflict="job_name",
            ignore_duplicates=True,
        ).execute()

    def claim(self, name: str, owner: str, lease_seconds: int) -> bool:
        res = self._client.rpc(
            "scheduler_claim_job",
            {"p_job": name, "p_owner": owner, "p_lease_seconds": lease_seconds},
        ).execute()
        return bool(res.data)

    def update_job(self, name: str, fields: dict[str, Any]) -> None:
        payload = {k: (_iso(v) if isinstance(v, datetime) else v) for k, v in fields.items()}
        payload["updated_at"] = _iso(datetime.now(timezone.utc))
        self._client.table("scheduler_jobs").update(payload).eq("job_name", name).execute()

    def record_run(self, row: dict[str, Any]) -> None:
        payload = {k: (_iso(v) if isinstance(v, datetime) else v) for k, v in row.items()}
        self._client.table("scheduler_job_runs").insert(payload).execute()

    def recent_runs(self, limit: int = 20, job: str | None = None) -> list[dict[str, Any]]:
        q = self._client.table("scheduler_job_runs").select("*")
        if job:
            q = q.eq("job_name", job)
        res = q.order("started_at", desc=True).limit(limit).execute()
        return list(res.data or [])


class InMemorySchedulerStore:
    """Process-local store (tests / local experiments). Not durable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []

    def get_job(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.jobs.get(name)
            return dict(row) if row else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(self.jobs[k]) for k in sorted(self.jobs)]

    def create_job(self, name: str, cron: str, last_slot: datetime | None) -> None:
        with self._lock:
            self.jobs.setdefault(
                name,
                {
                    "job_name": name,
                    "cron": cron,
                    "enabled": True,
                    "last_slot": _iso(last_slot),
                    "slot_attempts": 0,
                    "next_attempt_at": None,
                    "locked_until": None,
                    "lock_owner": None,
                    "last_started_at": None,
                    "last_finished_at": None,
                    "last_status": None,
                    "last_error": None,
                    "last_result": None,
                },
            )

    def claim(self, name: str, owner: str, lease_seconds: int) -> bool:
        now = datetime.now(timezone.utc)
        with self._lock:
            row = self.jobs.get(name)
            if not row:
                return False
            locked = parse_ts(row.get("locked_until"))
            if locked and locked > now:
                return False
            row["locked_until"] = _iso(now + timedelta(seconds=lease_seconds))
            row["lock_owner"] = owner
            row["last_started_at"] = _iso(now)
            row["last_status"] = "running"
            return True

    def update_job(self, name: str, fields: dict[str, Any]) -> None:
        with self._lock:
            row = self.jobs[name]
            for k, v in fields.items():
                row[k] = _iso(v) if isinstance(v, datetime) else v

    def record_run(self, row: dict[str, Any]) -> None:
        with self._lock:
            payload = {k: (_iso(v) if isinstance(v, datetime) else v) for k, v in row.items()}
            payload.setdefault("id", str(uuid.uuid4()))
            self.runs.append(payload)

    def recent_runs(self, limit: int = 20, job: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [r for r in self.runs if not job or r["job_name"] == job]
            return list(reversed(rows))[:limit]
