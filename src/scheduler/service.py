"""In-process, sleep-tolerant job scheduler.

Why not a plain in-memory cron loop: the API runs on free-tier hosting that
restarts and sleeps. Each job's last handled slot is persisted in Supabase, so
on every tick (and right after boot) the scheduler asks "has the most recent
scheduled slot been handled?" — if not, it runs it now. A missed schedule is
therefore caught up as soon as the process is alive again, and a Postgres-side
lease guarantees a slot never runs twice, even with several instances.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from src.scheduler.cron import CronSpec
from src.scheduler.jobs import JobContext, JobDef, build_jobs
from src.scheduler.store import SchedulerStore, SupabaseSchedulerStore, parse_ts

logger = logging.getLogger("aegis.scheduler")

Notifier = Callable[[str], None]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Scheduler:
    def __init__(
        self,
        store: SchedulerStore,
        jobs: dict[str, JobDef],
        settings: Any,
        *,
        now_fn: Callable[[], datetime] = _utcnow,
        notifier: Notifier | None = None,
    ) -> None:
        self.store = store
        self.jobs = jobs
        self.settings = settings
        self._now = now_fn
        self._notify = notifier
        self._owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._crons = {name: CronSpec.parse(job.cron) for name, job in jobs.items()}
        self._tasks: list[asyncio.Task[Any]] = []
        self.storage_error: str | None = None
        self.started_at: datetime | None = None
        self.last_tick_at: datetime | None = None

    # ── scheduling decisions ────────────────────────────────────────────────

    def _ensure_row(self, name: str, now: datetime) -> dict[str, Any]:
        row = self.store.get_job(name)
        if row:
            return row
        slot = self._crons[name].prev_fire(now)
        seed = None if self.settings.scheduler_run_missed_on_first_boot else slot
        self.store.create_job(name, self.jobs[name].cron, seed)
        logger.info("scheduler: registered job=%s cron=%r last_slot=%s", name, self.jobs[name].cron, seed)
        return self.store.get_job(name) or {"job_name": name, "last_slot": seed, "enabled": True}

    def due_slot(self, name: str, now: datetime | None = None) -> datetime | None:
        """Slot that should run now, or None when the job is up to date / paused / backing off."""
        now = now or self._now()
        row = self._ensure_row(name, now)
        if row.get("enabled") is False:
            return None
        slot = self._crons[name].prev_fire(now)
        last = parse_ts(row.get("last_slot"))
        if last and last >= slot:
            return None
        retry_at = parse_ts(row.get("next_attempt_at"))
        if retry_at and retry_at > now:
            return None
        locked = parse_ts(row.get("locked_until"))
        if locked and locked > now:
            return None
        return slot

    def tick(self) -> list[dict[str, Any]]:
        """Run every due job once (sequentially). Safe to call from anywhere, any time."""
        now = self._now()
        self.last_tick_at = now
        out: list[dict[str, Any]] = []
        for name in self.jobs:
            try:
                slot = self.due_slot(name, now)
                self.storage_error = None
            except Exception as exc:
                if self.storage_error != str(exc):
                    logger.error(
                        "scheduler: cannot read state for job=%s (%s). "
                        "Run scripts/supabase_scheduler.sql in Supabase.",
                        name,
                        exc,
                    )
                self.storage_error = str(exc)
                continue
            if slot is not None:
                out.append(self.run_job(name, trigger="schedule", slot=slot))
        return out

    # ── execution ───────────────────────────────────────────────────────────

    def run_job(self, name: str, *, trigger: str = "manual", slot: datetime | None = None) -> dict[str, Any]:
        job = self.jobs[name]
        now = self._now()
        self._ensure_row(name, now)

        if not self.store.claim(name, self._owner, job.lease_seconds):
            logger.info("scheduler: job=%s already running elsewhere; skipping", name)
            return {"job": name, "status": "locked"}

        row = self.store.get_job(name) or {}
        if trigger == "schedule" and slot is not None:
            last = parse_ts(row.get("last_slot"))
            if last and last >= slot:  # another instance finished this slot first
                self.store.update_job(name, {"locked_until": None, "lock_owner": None, "last_status": "success"})
                return {"job": name, "status": "skipped"}

        run_key = (
            f"sched-{name}-{slot.strftime('%Y%m%dT%H%MZ')}"
            if trigger == "schedule" and slot is not None
            else f"manual-{name}-{now.strftime('%Y%m%dT%H%M%SZ')}"
        )
        ctx = JobContext(settings=self.settings, trigger=trigger, slot=slot, run_key=run_key)

        logger.info("scheduler: start job=%s trigger=%s slot=%s", name, trigger, slot)
        t0 = time.monotonic()
        started = self._now()
        result: dict[str, Any] | None = None
        error: str | None = None
        try:
            result = job.run(ctx)
            status = "success"
        except Exception as exc:
            logger.exception("scheduler: job=%s failed", name)
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"[:4000]
        duration_ms = int((time.monotonic() - t0) * 1000)
        finished = self._now()

        fields: dict[str, Any] = {
            "locked_until": None,
            "lock_owner": None,
            "last_finished_at": finished,
            "last_status": status,
            "last_error": error,
            "last_result": result,
        }
        gave_up = False
        if trigger == "schedule" and slot is not None:
            if status == "success":
                fields.update(last_slot=slot, slot_attempts=0, next_attempt_at=None)
            else:
                attempts = int(row.get("slot_attempts") or 0) + 1
                if attempts >= max(1, self.settings.scheduler_max_attempts):
                    gave_up = True
                    fields.update(last_slot=slot, slot_attempts=0, next_attempt_at=None)
                else:
                    backoff = self.settings.scheduler_retry_backoff_minutes * 2 ** (attempts - 1)
                    fields.update(
                        slot_attempts=attempts,
                        next_attempt_at=finished + timedelta(minutes=backoff),
                    )

        try:
            self.store.update_job(name, fields)
            self.store.record_run(
                {
                    "job_name": name,
                    "trigger": trigger,
                    "slot": slot,
                    "started_at": started,
                    "finished_at": finished,
                    "duration_ms": duration_ms,
                    "status": status,
                    "result": result,
                    "error": error,
                }
            )
        except Exception:
            logger.exception("scheduler: failed to persist outcome for job=%s", name)

        logger.info("scheduler: done job=%s status=%s duration_ms=%s", name, status, duration_ms)
        self._announce(job, trigger=trigger, status=status, result=result, error=error, gave_up=gave_up)
        return {
            "job": name,
            "trigger": trigger,
            "status": status,
            "slot": slot.isoformat() if slot else None,
            "duration_ms": duration_ms,
            "result": result,
            "error": error,
        }

    def _announce(
        self,
        job: JobDef,
        *,
        trigger: str,
        status: str,
        result: dict[str, Any] | None,
        error: str | None,
        gave_up: bool,
    ) -> None:
        if not self._notify:
            return
        msg: str | None = None
        if status == "failed" and (gave_up or trigger == "manual"):
            msg = f"⚠️ Aegis scheduler: {job.name} failed ({trigger}).\n{error}"
        elif status == "success" and result:
            base = str(self.settings.portfolio_url).rstrip("/")
            if result.get("status") == "inserted" and result.get("post_status") == "published":
                msg = f"📝 New blog post published: {base}/blog/{result.get('slug')}"
            elif result.get("status") == "inserted":
                msg = f"📝 New blog post saved as {result.get('post_status')}: {result.get('slug')}"
            elif job.name == "blog_publish" and result.get("published"):
                links = "\n".join(f"{base}/blog/{s}" for s in result.get("slugs") or [])
                msg = f"📝 Published {result['published']} reviewed post(s):\n{links}"
        if not msg:
            return
        try:
            self._notify(msg)
        except Exception as exc:
            logger.warning("scheduler: notify failed: %s", exc)

    def set_enabled(self, name: str, enabled: bool) -> None:
        self._ensure_row(name, self._now())
        self.store.update_job(name, {"enabled": enabled})

    # ── introspection ───────────────────────────────────────────────────────

    def status(self, *, public: bool = False) -> dict[str, Any]:
        now = self._now()
        rows: dict[str, dict[str, Any]] = {}
        storage = "ok"
        try:
            rows = {r["job_name"]: r for r in self.store.list_jobs()}
        except Exception as exc:
            storage = "unavailable"
            self.storage_error = str(exc)
        jobs: list[dict[str, Any]] = []
        for name, job in self.jobs.items():
            row = rows.get(name, {})
            item: dict[str, Any] = {
                "name": name,
                "title": job.title,
                "description": job.description,
                "cron": job.cron,
                "enabled": row.get("enabled", True),
                "last_status": row.get("last_status"),
                "last_run_at": row.get("last_finished_at"),
                "next_run_at": self._crons[name].next_fire(now).isoformat(),
            }
            if not public:
                item.update(
                    last_slot=row.get("last_slot"),
                    last_started_at=row.get("last_started_at"),
                    last_error=row.get("last_error"),
                    last_result=row.get("last_result"),
                    slot_attempts=row.get("slot_attempts"),
                    next_attempt_at=row.get("next_attempt_at"),
                    locked_until=row.get("locked_until"),
                )
            jobs.append(item)
        out: dict[str, Any] = {
            "enabled": bool(self.settings.scheduler_enabled),
            "running": bool(self._tasks),
            "storage": storage,
            "now": now.isoformat(),
            "jobs": jobs,
        }
        if not public:
            out.update(
                owner=self._owner,
                started_at=self.started_at.isoformat() if self.started_at else None,
                last_tick_at=self.last_tick_at.isoformat() if self.last_tick_at else None,
                storage_error=self.storage_error if storage != "ok" else None,
                keep_awake_url=self.keep_awake_url(),
            )
        return out

    # ── background loops ────────────────────────────────────────────────────

    def keep_awake_url(self) -> str | None:
        if not self.settings.scheduler_keep_awake:
            return None
        base = (self.settings.public_base_url or os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
        if not base:
            domain = (os.environ.get("KOYEB_PUBLIC_DOMAIN") or "").strip()
            base = f"https://{domain}" if domain else ""
        return f"{base.rstrip('/')}/health/livez" if base else None

    async def _tick_loop(self) -> None:
        await asyncio.sleep(5)  # let the server finish booting before the catch-up pass
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.exception("scheduler: tick crashed")
            await asyncio.sleep(max(15, self.settings.scheduler_tick_seconds))

    async def _keep_awake_loop(self, url: str) -> None:
        interval = max(1, self.settings.scheduler_keep_awake_minutes) * 60
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(requests.get, url, timeout=20)
            except Exception as exc:
                logger.debug("scheduler: keep-awake ping failed: %s", exc)

    def start(self) -> None:
        """Start background loops on the running event loop (call from app lifespan)."""
        if self._tasks:
            return
        self.started_at = self._now()
        self._tasks.append(asyncio.create_task(self._tick_loop(), name="aegis-scheduler-tick"))
        url = self.keep_awake_url()
        if url:
            self._tasks.append(asyncio.create_task(self._keep_awake_loop(url), name="aegis-keep-awake"))
        logger.info(
            "scheduler: started jobs=%s tick=%ss keep_awake=%s",
            ", ".join(f"{n}[{j.cron}]" for n, j in self.jobs.items()),
            self.settings.scheduler_tick_seconds,
            url or "off (no public URL known)",
        )

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _telegram_notifier(settings: Any) -> Notifier | None:
    if not settings.scheduler_notify_telegram:
        return None
    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.telegram_chat_id or "").strip()
    if not token or not chat_id:
        return None

    def send(text: str) -> None:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": False},
            timeout=15,
        ).raise_for_status()

    return send


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        from config.settings import get_settings

        settings = get_settings()
        _scheduler = Scheduler(
            SupabaseSchedulerStore(settings.supabase_url, settings.supabase_service_key),
            build_jobs(settings),
            settings,
            notifier=_telegram_notifier(settings),
        )
    return _scheduler
