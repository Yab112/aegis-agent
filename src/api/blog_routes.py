"""Public read-only blog API (published posts only)."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
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


def _client(settings: Settings):
    return create_client(settings.supabase_url, settings.supabase_service_key)


@router.get("", response_model=BlogListResponse)
def list_blog_posts(
    settings: Annotated[Settings, Depends(get_settings)],
    page: Annotated[int, Query(ge=1, description="1-based page")] = 1,
    page_size: Annotated[int, Query(ge=1, le=50, alias="page_size")] = 10,
):
    client = _client(settings)
    start = (page - 1) * page_size
    end = start + page_size - 1

    count_res = (
        client.table("blog_posts")
        .select("id", count="exact")
        .eq("status", "published")
        .execute()
    )
    total = count_res.count if count_res.count is not None else 0

    cols = "slug,title,description,published_at,tags,image_url,image_alt,og_image_url"
    data_res = (
        client.table("blog_posts")
        .select(cols)
        .eq("status", "published")
        .order("published_at", desc=True)
        .range(start, end)
        .execute()
    )
    rows = data_res.data or []
    items = [
        BlogListItem(
            slug=r["slug"],
            title=r["title"],
            description=r["description"],
            published_at=r.get("published_at"),
            tags=list(r.get("tags") or []),
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


@router.get("/{slug}", response_model=BlogDetailResponse)
def get_blog_post(
    slug: str,
    settings: Annotated[Settings, Depends(get_settings)],
):
    client = _client(settings)
    res = (
        client.table("blog_posts")
        .select(
            "slug,title,description,body_md,published_at,updated_at,tags,"
            "image_url,image_alt,og_image_url,canonical_url"
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
    return BlogDetailResponse(
        slug=r["slug"],
        title=r["title"],
        description=r["description"],
        body_md=r.get("body_md") or "",
        published_at=r.get("published_at"),
        updated_at=r.get("updated_at"),
        tags=list(r.get("tags") or []),
        image_url=r.get("image_url"),
        image_alt=r.get("image_alt"),
        og_image_url=r.get("og_image_url"),
        canonical_url=r.get("canonical_url"),
    )
