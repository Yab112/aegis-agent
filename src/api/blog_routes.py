"""Public read-only blog API (published posts only)."""
from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from postgrest.exceptions import APIError
from supabase import create_client

from config.settings import Settings, get_settings

logger = logging.getLogger("aegis.blog_api")

router = APIRouter(prefix="/blog", tags=["blog"])


class BlogListItem(BaseModel):
    slug: str
    title: str
    description: str
    published_at: str | None
    tags: list[str] = Field(default_factory=list)
    topic_key: str | None = None
    image_url: str | None = None
    image_alt: str | None = None
    og_image_url: str | None = None


class BlogListResponse(BaseModel):
    items: list[BlogListItem]
    page: int
    page_size: int
    total: int


class BlogDetailResponse(BaseModel):
    slug: str
    title: str
    description: str
    body_md: str
    published_at: str | None
    updated_at: str | None = None
    tags: list[str] = Field(default_factory=list)
    image_url: str | None = None
    image_alt: str | None = None
    og_image_url: str | None = None
    canonical_url: str | None = None
    topic_key: str | None = None
    resource_links: list[dict[str, str]] = Field(default_factory=list)
    related_posts: list[BlogListItem] = Field(default_factory=list)


class BlogTopicItem(BaseModel):
    topic: str
    count: int


class BlogTopicsResponse(BaseModel):
    items: list[BlogTopicItem]


def _client(settings: Settings):
    return create_client(settings.supabase_url, settings.supabase_service_key)


def _normalize_topic(value: str | None) -> str | None:
    if value is None:
        return None
    topic = value.strip().lower().replace(" ", "-")
    topic = re.sub(r"[^a-z0-9_-]+", "", topic)
    return topic or None


def _normalize_resource_links(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "title": str(item.get("title") or "Resource").strip() or "Resource",
                "url": url,
                "description": str(item.get("description") or "").strip(),
            }
        )
    return out[:8]


def _fetch_related_posts(client, slug: str, tags: list[str], topic_key: str | None) -> list[BlogListItem]:
    # Prefer same topic, then overlap by tags, and always exclude current slug.
    related_rows: list[dict] = []
    cols = "slug,title,description,published_at,tags,topic_key,image_url,image_alt,og_image_url"
    if topic_key:
        same_topic_res = (
            client.table("blog_posts")
            .select(cols)
            .eq("status", "published")
            .eq("topic_key", topic_key)
            .neq("slug", slug)
            .order("published_at", desc=True)
            .limit(6)
            .execute()
        )
        related_rows.extend(same_topic_res.data or [])

    if len(related_rows) < 6 and tags:
        overlap_res = (
            client.table("blog_posts")
            .select(cols)
            .eq("status", "published")
            .overlaps("tags", tags)
            .neq("slug", slug)
            .order("published_at", desc=True)
            .limit(10)
            .execute()
        )
        seen = {r.get("slug") for r in related_rows}
        for row in overlap_res.data or []:
            slug_val = row.get("slug")
            if slug_val in seen:
                continue
            seen.add(slug_val)
            related_rows.append(row)
            if len(related_rows) >= 6:
                break

    return [
        BlogListItem(
            slug=r["slug"],
            title=r["title"],
            description=r["description"],
            published_at=r.get("published_at"),
            tags=list(r.get("tags") or []),
            topic_key=r.get("topic_key"),
            image_url=r.get("image_url"),
            image_alt=r.get("image_alt"),
            og_image_url=r.get("og_image_url"),
        )
        for r in related_rows[:6]
    ]


@router.get("", response_model=BlogListResponse)
def list_blog_posts(
    settings: Annotated[Settings, Depends(get_settings)],
    page: Annotated[int, Query(ge=1, description="1-based page")] = 1,
    page_size: Annotated[int, Query(ge=1, le=50, alias="page_size")] = 10,
    topic: Annotated[
        str | None,
        Query(description="Filter by topic_key or tag (case-insensitive)"),
    ] = None,
):
    client = _client(settings)
    start = (page - 1) * page_size
    end = start + page_size - 1
    normalized_topic = _normalize_topic(topic)

    cols = "slug,title,description,published_at,tags,topic_key,image_url,image_alt,og_image_url"
    base_query = client.table("blog_posts").select(cols).eq("status", "published")

    if not normalized_topic:
        count_res = (
            client.table("blog_posts")
            .select("id", count="exact")
            .eq("status", "published")
            .execute()
        )
        total = count_res.count if count_res.count is not None else 0
        data_res = base_query.order("published_at", desc=True).range(start, end).execute()
        rows = data_res.data or []
    else:
        # Build filtered rows in-memory to support OR behavior: topic_key OR tags overlap.
        data_res = base_query.order("published_at", desc=True).limit(500).execute()
        all_rows = data_res.data or []
        rows_filtered: list[dict] = []
        for r in all_rows:
            row_topic_key = _normalize_topic(r.get("topic_key"))
            row_tags = [_normalize_topic(str(t)) for t in (r.get("tags") or [])]
            if normalized_topic == row_topic_key or normalized_topic in row_tags:
                rows_filtered.append(r)
        total = len(rows_filtered)
        rows = rows_filtered[start : end + 1]

    items = [
        BlogListItem(
            slug=r["slug"],
            title=r["title"],
            description=r["description"],
            published_at=r.get("published_at"),
            tags=list(r.get("tags") or []),
            topic_key=r.get("topic_key"),
            image_url=r.get("image_url"),
            image_alt=r.get("image_alt"),
            og_image_url=r.get("og_image_url"),
        )
        for r in rows
    ]
    return BlogListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/topics", response_model=BlogTopicsResponse)
def list_blog_topics(settings: Annotated[Settings, Depends(get_settings)]):
    client = _client(settings)
    data_res = (
        client.table("blog_posts")
        .select("tags")
        .eq("status", "published")
        .limit(1000)
        .execute()
    )
    counts: dict[str, int] = {}
    for row in data_res.data or []:
        tags = row.get("tags") or []
        if not isinstance(tags, list):
            continue
        for tag in tags:
            t = _normalize_topic(str(tag))
            if not t:
                continue
            counts[t] = counts.get(t, 0) + 1

    items = [BlogTopicItem(topic=topic, count=count) for topic, count in counts.items()]
    items.sort(key=lambda x: (-x.count, x.topic))
    return BlogTopicsResponse(items=items)


@router.get("/{slug}", response_model=BlogDetailResponse)
def get_blog_post(
    slug: str,
    settings: Annotated[Settings, Depends(get_settings)],
):
    client = _client(settings)
    select_cols = (
        "slug,title,description,body_md,published_at,updated_at,tags,"
        "image_url,image_alt,og_image_url,canonical_url,topic_key,resource_links"
    )
    try:
        res = (
            client.table("blog_posts")
            .select(select_cols)
            .eq("slug", slug)
            .eq("status", "published")
            .limit(1)
            .execute()
        )
    except APIError as exc:
        message = str(getattr(exc, "message", exc))
        if "resource_links" not in message:
            raise
        res = (
            client.table("blog_posts")
            .select(
                "slug,title,description,body_md,published_at,updated_at,tags,"
                "image_url,image_alt,og_image_url,canonical_url,topic_key"
            )
            .eq("slug", slug)
            .eq("status", "published")
            .limit(1)
            .execute()
        )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Post not found")
    r = rows[0]
    tags = list(r.get("tags") or [])
    topic_key = r.get("topic_key")
    related_posts = _fetch_related_posts(client, slug=slug, tags=tags, topic_key=topic_key)
    return BlogDetailResponse(
        slug=r["slug"],
        title=r["title"],
        description=r["description"],
        body_md=r.get("body_md") or "",
        published_at=r.get("published_at"),
        updated_at=r.get("updated_at"),
        tags=tags,
        image_url=r.get("image_url"),
        image_alt=r.get("image_alt"),
        og_image_url=r.get("og_image_url"),
        canonical_url=r.get("canonical_url"),
        topic_key=topic_key,
        resource_links=_normalize_resource_links(r.get("resource_links") or []),
        related_posts=related_posts,
    )
