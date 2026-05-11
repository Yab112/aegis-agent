"""
Promote blog_posts from review → published (never touches draft).

Rules:
- status == 'review'
- published_at is null
- If scheduled_publish_at is set, publish only when now >= scheduled_publish_at (UTC)

Requires blog_posts.scheduled_publish_at if you use scheduling (run supabase_blog_ideas.sql).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY

Usage:
  python scripts/blog_publish_reviewed.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("blog_publish_reviewed")

from src.blog.publish_webhook import notify_blog_post_published


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def main() -> None:
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL", "").strip()
    svc = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not svc:
        logger.error("Set SUPABASE_URL and SUPABASE_SERVICE_KEY")
        sys.exit(1)

    client = create_client(url, svc)
    now = datetime.now(timezone.utc)

    cols = "id,slug,title,topic_key,tags,image_url,og_image_url,published_at,scheduled_publish_at"
    try:
        res = (
            client.table("blog_posts")
            .select(cols)
            .eq("status", "review")
            .execute()
        )
    except Exception as e:
        logger.warning("select with scheduled_publish_at failed (%s); retry without column", e)
        res = (
            client.table("blog_posts")
            .select("id,slug,title,topic_key,tags,image_url,og_image_url,published_at")
            .eq("status", "review")
            .execute()
        )
        for row in res.data or []:
            if row.get("published_at"):
                continue
            update_res = client.table("blog_posts").update(
                {"status": "published", "published_at": now.isoformat()}
            ).eq("id", row["id"]).select("id,slug,title,topic_key,tags,image_url,og_image_url,published_at").execute()
            publish_row = (update_res.data or [{}])[0]
            notify_blog_post_published(
                post=publish_row,
                source="review_publish_job",
                pipeline_run_key=None,
            )
            logger.info("published id=%s (no schedule column)", row["id"])
        print(
            json.dumps(
                {
                    "published": sum(
                        1
                        for r in (res.data or [])
                        if not r.get("published_at")
                    ),
                    "mode": "no_scheduled_publish_at_column",
                },
                indent=2,
            )
        )
        sys.exit(0)

    rows = res.data or []
    published = 0
    skipped = 0

    for row in rows:
        if row.get("published_at"):
            skipped += 1
            continue
        sp = row.get("scheduled_publish_at")
        if sp:
            sp_dt = _parse_ts(sp)
            if sp_dt and sp_dt > now:
                skipped += 1
                logger.info("skip id=%s — scheduled_publish_at in future", row["id"])
                continue
        update_res = client.table("blog_posts").update(
            {"status": "published", "published_at": now.isoformat()}
        ).eq("id", row["id"]).select("id,slug,title,topic_key,tags,image_url,og_image_url,published_at").execute()
        publish_row = (update_res.data or [{}])[0]
        notify_blog_post_published(
            post=publish_row,
            source="review_publish_job",
            pipeline_run_key=None,
        )
        published += 1
        logger.info("published id=%s", row["id"])

    print(
        json.dumps(
            {"published": published, "skipped": skipped, "review_rows": len(rows)},
            indent=2,
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
