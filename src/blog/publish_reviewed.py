"""Promote blog_posts from review → published (never touches draft rows).

Rules:
- status == 'review' and published_at is null
- if scheduled_publish_at is set, publish only once now >= scheduled_publish_at (UTC)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from supabase import create_client

from src.blog.publish_webhook import notify_blog_post_published

logger = logging.getLogger("aegis.blog.publish")

_BASE_COLS = "id,slug,title,topic_key,tags,image_url,og_image_url,published_at"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def run_publish_reviewed(supabase_url: str, supabase_service_key: str) -> dict[str, Any]:
    client = create_client(supabase_url, supabase_service_key)
    now = datetime.now(timezone.utc)

    mode = "scheduled"
    try:
        res = (
            client.table("blog_posts")
            .select(f"{_BASE_COLS},scheduled_publish_at")
            .eq("status", "review")
            .execute()
        )
    except Exception as e:
        logger.warning("select with scheduled_publish_at failed (%s); retry without column", e)
        mode = "no_scheduled_publish_at_column"
        res = client.table("blog_posts").select(_BASE_COLS).eq("status", "review").execute()

    rows = res.data or []
    published = 0
    skipped = 0
    slugs: list[str] = []

    for row in rows:
        if row.get("published_at"):
            skipped += 1
            continue
        sp_dt = _parse_ts(row.get("scheduled_publish_at"))
        if sp_dt and sp_dt > now:
            skipped += 1
            logger.info("skip id=%s — scheduled_publish_at in future", row["id"])
            continue
        client.table("blog_posts").update(
            {"status": "published", "published_at": now.isoformat()}
        ).eq("id", row["id"]).execute()
        notify_blog_post_published(
            post={**row, "published_at": now.isoformat()},
            source="review_publish_job",
            pipeline_run_key=None,
        )
        published += 1
        slugs.append(str(row.get("slug") or ""))
        logger.info("published id=%s", row["id"])

    return {
        "published": published,
        "skipped": skipped,
        "review_rows": len(rows),
        "slugs": slugs,
        "mode": mode,
    }
