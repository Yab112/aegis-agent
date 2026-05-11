"""Public read-only blog API (published posts only)."""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from postgrest.exceptions import APIError
from supabase import create_client

from config.settings import Settings, get_settings

logger = logging.getLogger("aegis.blog_api")

router = APIRouter(prefix="/blog", tags=["blog"])
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, object]] = {}


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


class BlogSearchResponse(BaseModel):
    items: list[BlogListItem]
    page: int
    page_size: int
    total: int
    query: str


def _client(settings: Settings):
    return create_client(settings.supabase_url, settings.supabase_service_key)


def _cache_get(key: str, ttl_seconds: int) -> object | None:
    if ttl_seconds <= 0:
        return None
    now = time.time()
    with _CACHE_LOCK:
        payload = _CACHE.get(key)
        if not payload:
            return None
        created_at, value = payload
        if now - created_at > ttl_seconds:
            _CACHE.pop(key, None)
            return None
        return value


def _cache_set(key: str, value: object, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)


def _normalize_topic(value: str | None) -> str | None:
    if value is None:
        return None
    topic = value.strip().lower().replace(" ", "-")
    topic = re.sub(r"[^a-z0-9_-]+", "", topic)
    return topic or None


def _public_image_urls(row: dict, default_og: str | None) -> tuple[str | None, str | None]:
    """
    Prefer stored URLs; if both missing, use BLOG_DEFAULT_OG_IMAGE from API settings so
    clients never get null OG for published posts when a default is configured.
    """
    raw_img = (row.get("image_url") or "").strip() or None
    raw_og = (row.get("og_image_url") or "").strip() or None
    img = raw_img or raw_og
    og = raw_og or raw_img
    if img or og:
        return img or og, og or img
    d = (default_og or "").strip() or None
    return d, d


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


def _fetch_related_posts(
    client,
    slug: str,
    tags: list[str],
    topic_key: str | None,
    *,
    default_og: str | None,
) -> list[BlogListItem]:
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

    out: list[BlogListItem] = []
    for r in related_rows[:6]:
        img, og = _public_image_urls(r, default_og)
        out.append(
            BlogListItem(
                slug=r["slug"],
                title=r["title"],
                description=r["description"],
                published_at=r.get("published_at"),
                tags=list(r.get("tags") or []),
                topic_key=r.get("topic_key"),
                image_url=img,
                image_alt=r.get("image_alt"),
                og_image_url=og,
            )
        )
    return out


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
    ttl = settings.blog_api_cache_ttl_seconds
    cache_key = f"blog:list:{page}:{page_size}:{topic or ''}"
    cached = _cache_get(cache_key, ttl)
    if isinstance(cached, BlogListResponse):
        return cached

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

    default_og = settings.blog_default_og_image
    items: list[BlogListItem] = []
    for r in rows:
        img, og = _public_image_urls(r, default_og)
        items.append(
            BlogListItem(
                slug=r["slug"],
                title=r["title"],
                description=r["description"],
                published_at=r.get("published_at"),
                tags=list(r.get("tags") or []),
                topic_key=r.get("topic_key"),
                image_url=img,
                image_alt=r.get("image_alt"),
                og_image_url=og,
            )
        )
    response = BlogListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )
    _cache_set(cache_key, response, ttl)
    return response


@router.get("/topics", response_model=BlogTopicsResponse)
def list_blog_topics(settings: Annotated[Settings, Depends(get_settings)]):
    ttl = settings.blog_api_cache_ttl_seconds
    cache_key = "blog:topics"
    cached = _cache_get(cache_key, ttl)
    if isinstance(cached, BlogTopicsResponse):
        return cached

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
    response = BlogTopicsResponse(items=items)
    _cache_set(cache_key, response, ttl)
    return response


@router.get("/search", response_model=BlogSearchResponse)
def search_blog_posts(
    settings: Annotated[Settings, Depends(get_settings)],
    q: Annotated[str, Query(min_length=2, description="Search query")],
    page: Annotated[int, Query(ge=1, description="1-based page")] = 1,
    page_size: Annotated[int, Query(ge=1, le=50, alias="page_size")] = 10,
    topic: Annotated[
        str | None,
        Query(description="Optional topic/tag filter on top of search"),
    ] = None,
):
    ttl = settings.blog_api_cache_ttl_seconds
    cache_key = f"blog:search:{q}:{page}:{page_size}:{topic or ''}"
    cached = _cache_get(cache_key, ttl)
    if isinstance(cached, BlogSearchResponse):
        return cached

    client = _client(settings)
    start = (page - 1) * page_size
    end = start + page_size - 1
    normalized_topic = _normalize_topic(topic)
    q_norm = q.strip().lower()

    res = (
        client.table("blog_posts")
        .select(
            "slug,title,description,body_md,published_at,tags,topic_key,image_url,image_alt,og_image_url"
        )
        .eq("status", "published")
        .order("published_at", desc=True)
        .limit(1000)
        .execute()
    )
    rows = res.data or []

    matched: list[dict] = []
    for row in rows:
        row_topic = _normalize_topic(row.get("topic_key"))
        row_tags_norm = [_normalize_topic(str(t)) for t in (row.get("tags") or [])]
        if normalized_topic and not (
            normalized_topic == row_topic or normalized_topic in row_tags_norm
        ):
            continue

        haystack = " ".join(
            [
                str(row.get("title") or ""),
                str(row.get("description") or ""),
                str(row.get("body_md") or "")[:4000],
                " ".join(str(t) for t in (row.get("tags") or [])),
                str(row.get("topic_key") or ""),
            ]
        ).lower()
        if q_norm in haystack:
            matched.append(row)

    total = len(matched)
    paged = matched[start : end + 1]
    default_og = settings.blog_default_og_image
    items: list[BlogListItem] = []
    for r in paged:
        img, og = _public_image_urls(r, default_og)
        items.append(
            BlogListItem(
                slug=r["slug"],
                title=r["title"],
                description=r["description"],
                published_at=r.get("published_at"),
                tags=list(r.get("tags") or []),
                topic_key=r.get("topic_key"),
                image_url=img,
                image_alt=r.get("image_alt"),
                og_image_url=og,
            )
        )
    response = BlogSearchResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        query=q,
    )
    _cache_set(cache_key, response, ttl)
    return response


@router.get("/{slug}", response_model=BlogDetailResponse)
def get_blog_post(
    slug: str,
    settings: Annotated[Settings, Depends(get_settings)],
):
    ttl = settings.blog_api_cache_ttl_seconds
    cache_key = f"blog:detail:{slug}"
    cached = _cache_get(cache_key, ttl)
    if isinstance(cached, BlogDetailResponse):
        return cached

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
    default_og = settings.blog_default_og_image
    detail_img, detail_og = _public_image_urls(r, default_og)
    related_posts = _fetch_related_posts(
        client, slug=slug, tags=tags, topic_key=topic_key, default_og=default_og
    )
    response = BlogDetailResponse(
        slug=r["slug"],
        title=r["title"],
        description=r["description"],
        body_md=r.get("body_md") or "",
        published_at=r.get("published_at"),
        updated_at=r.get("updated_at"),
        tags=tags,
        image_url=detail_img,
        image_alt=r.get("image_alt"),
        og_image_url=detail_og,
        canonical_url=r.get("canonical_url"),
        topic_key=topic_key,
        resource_links=_normalize_resource_links(r.get("resource_links") or []),
        related_posts=related_posts,
    )
    _cache_set(cache_key, response, ttl)
    return response
