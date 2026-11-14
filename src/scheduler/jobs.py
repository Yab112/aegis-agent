"""Job registry: what the scheduler can run and how."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger("aegis.scheduler.jobs")


@dataclass(frozen=True)
class JobContext:
    settings: Any
    trigger: str  # "schedule" | "manual"
    slot: datetime | None
    run_key: str


@dataclass(frozen=True)
class JobDef:
    name: str
    title: str
    description: str
    # 5-field cron (UTC). None for change-driven jobs, which use `fingerprint` instead.
    cron: str | None
    run: Callable[[JobContext], dict[str, Any]]
    lease_seconds: int = 30 * 60
    # Change-driven job: runs whenever this value differs from the `fingerprint`
    # stored in its last successful result (e.g. after a deploy changed its input).
    fingerprint: Callable[[], str] | None = None


def _is_quota_error(exc: BaseException) -> bool:
    msg = str(exc)
    return "ResourceExhausted" in msg or "429" in msg or "Quota exceeded" in msg


def _run_blog_ideas(ctx: JobContext) -> dict[str, Any]:
    from src.blog.idea_ingest import run_idea_ingest

    s = ctx.settings
    return run_idea_ingest(
        gemini_api_key=s.gemini_api_key,
        gemini_model=s.gemini_model,
        supabase_url=s.supabase_url,
        supabase_service_key=s.supabase_service_key,
        owner_name=s.owner_name,
        portfolio_url=s.portfolio_url,
        github_token=(os.environ.get("BLOG_GITHUB_TOKEN") or "").strip() or None,
    )


def _run_blog_draft(ctx: JobContext, *, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    from src.blog.pipeline import BlogPipelineConfig, run_blog_draft_once

    cfg = BlogPipelineConfig.from_full_settings(ctx.settings)
    # One post per slot: a retried or duplicated run for the same slot is a no-op.
    cfg.pipeline_run_key = ctx.run_key
    if ctx.trigger == "schedule":
        cfg.post_status_override = ctx.settings.scheduler_blog_draft_status

    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        try:
            return run_blog_draft_once(cfg)
        except Exception as exc:
            if not _is_quota_error(exc) or attempt >= max_attempts:
                raise
            wait = 15 * attempt
            logger.warning(
                "blog_draft: Gemini quota hit (attempt %s/%s); sleeping %ss",
                attempt,
                max_attempts,
                wait,
            )
            sleep(wait)
    raise RuntimeError("unreachable")


def _run_blog_publish(ctx: JobContext) -> dict[str, Any]:
    from src.blog.publish_reviewed import run_publish_reviewed

    return run_publish_reviewed(ctx.settings.supabase_url, ctx.settings.supabase_service_key)


def _run_kb_ingest(ctx: JobContext) -> dict[str, Any]:
    from src.rag.ingest import run_kb_ingest

    return run_kb_ingest(ctx.settings.supabase_url, ctx.settings.supabase_service_key)


def _kb_fingerprint() -> str:
    from src.rag.ingest import _embedding_tag, kb_fingerprint

    tag = _embedding_tag()
    return kb_fingerprint() + (f"+{tag}" if tag else "")


def build_jobs(settings: Any) -> dict[str, JobDef]:
    jobs = [
        JobDef(
            name="blog_ideas",
            title="Idea radar",
            description="Scans HN, GitHub, Dev.to, Reddit and Stack Overflow, scores signals with Gemini and queues blog ideas.",
            cron=settings.scheduler_blog_ideas_cron,
            run=_run_blog_ideas,
        ),
        JobDef(
            name="blog_draft",
            title="Writer",
            description="Picks the best queued idea, writes the post, generates a cover image and saves it.",
            cron=settings.scheduler_blog_draft_cron,
            run=_run_blog_draft,
        ),
        JobDef(
            name="blog_publish",
            title="Publisher",
            description="Promotes reviewed posts to published once their scheduled time has passed.",
            cron=settings.scheduler_blog_publish_cron,
            run=_run_blog_publish,
            lease_seconds=10 * 60,
        ),
    ]
    if getattr(settings, "scheduler_kb_ingest_on_change", True):
        jobs.append(
            JobDef(
                name="kb_ingest",
                title="Knowledge sync",
                description="Re-embeds the knowledge base whenever a deploy changes the docs the chat agent answers from.",
                cron=None,
                run=_run_kb_ingest,
                fingerprint=_kb_fingerprint,
            )
        )
    return {j.name: j for j in jobs}
