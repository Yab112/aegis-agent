"""Supabase reads and writes the blog pipeline needs (dedup, slugs, idea queue, failure log)."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from src.blog.config import BlogPipelineConfig

logger = logging.getLogger("aegis.blog")


def _record_pipeline_failure(
    client: Client,
    *,
    stage: str,
    error: BaseException,
    payload: dict[str, Any],
    run_key: str | None,
    topic_key: str | None,
    slug: str | None,
    idea_id: str | None,
) -> None:
    row = {
        "stage": stage,
        "error_message": str(error)[:4000],
        "payload": payload,
        "pipeline_run_key": run_key,
        "topic_key": topic_key,
        "slug": slug,
        "blog_idea_id": idea_id,
    }
    try:
        client.table("blog_pipeline_failures").insert(row).execute()
    except Exception as log_exc:
        logger.warning("failed to write dead-letter row stage=%s err=%s", stage, log_exc)


def normalize_topic_key(raw: str) -> str:
    topic_key = str(raw).strip().lower().replace(" ", "_")
    topic_key = re.sub(r"[^a-z0-9_]+", "", topic_key)
    return topic_key or "topic"


def _slugify_kebab(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "post"


def _get_sb_client(cfg: BlogPipelineConfig) -> Client:
    return create_client(cfg.supabase_url, cfg.supabase_service_key)


def _topic_exists(client: Client, topic_key: str) -> bool:
    r = (
        client.table("blog_posts")
        .select("id")
        .eq("topic_key", topic_key)
        .limit(1)
        .execute()
    )
    return bool(r.data)


def _slug_taken(client: Client, slug: str) -> bool:
    r = (
        client.table("blog_posts")
        .select("id")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    return bool(r.data)


def _blog_posts_column_missing(exc: BaseException, column: str) -> bool:
    """PostgREST / Postgres: column absent on blog_posts (schema not migrated)."""
    raw = str(exc).lower()
    col = column.lower()
    if col not in raw:
        return False
    return "does not exist" in raw or "pgrst204" in raw or "42703" in raw


def _run_key_exists(client: Client, run_key: str | None) -> bool:
    if not run_key:
        return False
    try:
        r = (
            client.table("blog_posts")
            .select("id")
            .eq("pipeline_run_key", run_key)
            .limit(1)
            .execute()
        )
        return bool(r.data)
    except Exception as e:
        if _blog_posts_column_missing(e, "pipeline_run_key"):
            logger.warning(
                "blog_posts.pipeline_run_key missing — skipping CI run-key dedup. "
                "Run scripts/supabase_blog_posts_pipeline_run_key.sql in Supabase."
            )
            return False
        raise


def _unique_slug(client: Client, base_slug: str) -> str:
    if not _slug_taken(client, base_slug):
        return base_slug
    for i in range(2, 50):
        candidate = f"{base_slug}-{i}"
        if not _slug_taken(client, candidate):
            return candidate
    raise RuntimeError("Could not allocate unique slug")


def _post_status_after_write(override: str | None = None) -> tuple[str, str | None]:
    """
    BLOG_POST_STATUS_AFTER_WRITE: draft | review | published (default draft).

    - draft: insert as before; you must promote manually or via publish flow.
    - review: ready for blog-publish.yml (review → published) with no human edit.
    - published: live immediately (sets published_at); public API shows the post.
    """
    raw = (override or os.environ.get("BLOG_POST_STATUS_AFTER_WRITE", "draft")).strip().lower()
    if raw in ("published", "publish", "live"):
        return "published", datetime.now(timezone.utc).isoformat()
    if raw == "review":
        return "review", None
    if raw == "draft":
        return "draft", None
    logger.warning(
        "Invalid BLOG_POST_STATUS_AFTER_WRITE=%r; using draft", raw
    )
    return "draft", None


def _pick_pending_blog_idea(client: Client) -> dict[str, Any] | None:
    """Oldest pending first within same skill tier (high before medium before low)."""
    r = client.table("blog_ideas").select("*").eq("status", "pending").execute()
    rows = r.data or []
    if not rows:
        return None
    rank = {"high": 0, "medium": 1, "low": 2}
    rows.sort(
        key=lambda x: (
            rank.get(str(x.get("skill_fit") or "medium").lower(), 2),
            x.get("created_at") or "",
        )
    )
    return rows[0]
