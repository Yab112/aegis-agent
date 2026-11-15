"""Admin: the source list and what was collected. Secret-protected."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from config.settings import Settings, get_settings
from src.api.blog_internal import verify_internal_secret
from src.api.keyword_routes import guard
from src.newsroom.collector import collect_all
from src.newsroom.registry import GROUPS
from src.newsroom.store import NewsroomStore

router = APIRouter(prefix="/newsroom", tags=["newsroom"], dependencies=[Depends(verify_internal_secret)])
SettingsDep = Annotated[Settings, Depends(get_settings)]


class SourcePatch(BaseModel):
    enabled: bool | None = None
    tier: int | None = Field(default=None, ge=1, le=4)
    name: str | None = Field(default=None, min_length=2, max_length=120)
    feed_url: str | None = Field(default=None, max_length=600)
    link_pattern: str | None = Field(default=None, max_length=200)


class SourceCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9-]{3,40}$")
    name: str = Field(min_length=2, max_length=120)
    url: str = Field(min_length=8, max_length=600)
    feed_url: str | None = Field(default=None, max_length=600)
    link_pattern: str | None = Field(default=None, max_length=200)
    tier: int = Field(default=2, ge=1, le=4)


@router.get("/sources")
def list_sources(settings: SettingsDep) -> dict[str, Any]:
    store = NewsroomStore.from_settings(settings)
    try:
        store.seed_sources()
        return {"items": store.list_sources(), "items_total": store.count_items()}
    except Exception as exc:
        raise guard(exc) from exc


@router.post("/sources", status_code=201)
def add_source(body: SourceCreate, settings: SettingsDep) -> dict[str, Any]:
    row = body.model_dump()
    row["kind"] = "feed" if body.feed_url else "page"
    row["grp"] = GROUPS[body.tier]
    try:
        return NewsroomStore.from_settings(settings).add_source(row)
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            raise HTTPException(409, detail="A source with this key already exists") from exc
        raise guard(exc) from exc


@router.patch("/sources/{key}")
def patch_source(key: str, body: SourcePatch, settings: SettingsDep) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "tier" in patch:
        patch["grp"] = GROUPS[patch["tier"]]
    if "feed_url" in patch:
        patch["kind"] = "feed" if patch["feed_url"].strip() else "page"
        patch["feed_url"] = patch["feed_url"].strip() or None
    if not patch:
        raise HTTPException(422, detail="Nothing to update")
    try:
        row = NewsroomStore.from_settings(settings).update_source(key, patch)
    except Exception as exc:
        raise guard(exc) from exc
    if not row:
        raise HTTPException(404, detail="Source not found")
    return row


@router.get("/items")
def list_items(settings: SettingsDep, source: str | None = None, limit: int = 60) -> dict[str, Any]:
    try:
        return {"items": NewsroomStore.from_settings(settings).list_items(source_key=source, limit=min(max(limit, 1), 200))}
    except Exception as exc:
        raise guard(exc) from exc


@router.post("/collect", status_code=202, summary="Read all sources now (background)")
def collect(settings: SettingsDep, background: BackgroundTasks, source: str | None = None) -> dict[str, str]:
    background.add_task(collect_all, NewsroomStore.from_settings(settings), only=source)
    return {"status": "accepted"}
