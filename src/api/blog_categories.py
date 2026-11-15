"""Post categories on the public blog API. Works before and after scripts/supabase_newsroom.sql is run."""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from supabase import create_client

from config.settings import Settings, get_settings
from src.newsroom.categories import category_labels

logger = logging.getLogger("aegis.blog.categories")

router = APIRouter(prefix="/blog", tags=["blog"])


def post_extras(client: Any, slugs: list[str]) -> dict[str, dict[str, Any]]:
    """category / keyword / format per slug, with the category's display label. Empty if the columns are missing."""
    if not slugs:
        return {}
    try:
        rows = client.table("blog_posts").select("slug,category,keyword,format").in_("slug", slugs).execute().data or []
    except Exception as exc:
        logger.info("post extras unavailable (migration not run?): %s", str(exc)[:120])
        return {}
    labels = category_labels()
    return {
        r["slug"]: {
            "category": r.get("category"),
            "category_label": labels.get(r.get("category") or "", None),
            "keyword": r.get("keyword"),
            "format": r.get("format"),
        }
        for r in rows
    }


@router.get("/categories", summary="Categories that have published posts, with counts")
def blog_categories(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    client = create_client(settings.supabase_url, settings.supabase_service_key)
    try:
        rows = client.table("blog_posts").select("category").eq("status", "published").limit(1000).execute().data or []
    except Exception:
        return {"items": []}
    counts: dict[str, int] = {}
    for r in rows:
        if r.get("category"):
            counts[r["category"]] = counts.get(r["category"], 0) + 1
    labels = category_labels()
    items = [{"slug": k, "label": labels.get(k, k), "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    return {"items": items}
