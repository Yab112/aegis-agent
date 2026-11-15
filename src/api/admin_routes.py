"""
Admin console API (secret-protected) + the public projects feed.

Everything under ``/admin`` requires the API secret (``Authorization: Bearer`` or
``X-Internal-Secret``). The portfolio's ``/admin`` pages call it from the server
only, after the owner has signed in there, so the secret never reaches a browser.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from config.settings import Settings, get_settings
from src.admin import inbox
from src.api.blog_internal import verify_internal_secret

logger = logging.getLogger("aegis.admin")

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_internal_secret)])
public_router = APIRouter(tags=["projects"])

SettingsDep = Annotated[Settings, Depends(get_settings)]

POST_LIST_COLS = "id,slug,title,description,status,tags,image_url,published_at,created_at,updated_at,topic_key"
POST_STATUSES = ("draft", "review", "published")
MIGRATION_HINT = "Run scripts/supabase_admin.sql in Supabase, then retry."


def _guard(exc: Exception) -> HTTPException:
    if inbox.is_missing_schema(exc):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=MIGRATION_HINT)
    logger.exception("admin request failed")
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)[:500])


def _clear_blog_cache() -> None:
    from src.api import blog_routes

    with blog_routes._CACHE_LOCK:
        blog_routes._CACHE.clear()


def _slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


# ── Overview ──────────────────────────────────────────────────────────────────

def _count(client: Any, table: str, **eq: str) -> int | None:
    try:
        q = client.table(table).select("id", count="exact").limit(1)
        for k, v in eq.items():
            q = q.eq(k, v)
        return q.execute().count or 0
    except Exception:
        return None


@router.get("/overview", summary="Counts for the admin home page")
def overview(settings: SettingsDep) -> dict[str, Any]:
    client = inbox.sb(settings)
    alerts = inbox.list_alerts(settings, limit=200)
    return {
        "inbox": {
            "open": sum(1 for a in alerts if a.get("status") == "open"),
            "total": len(alerts),
            "latest": alerts[:5],
        },
        "posts": {s: _count(client, "blog_posts", status=s) for s in POST_STATUSES},
        "ideas_pending": _count(client, "blog_ideas", status="pending"),
        "leads": _count(client, "leads"),
        "projects": _count(client, "projects"),
        "generation": dict(_GENERATION),
    }


# ── Inbox ─────────────────────────────────────────────────────────────────────

class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    polish: bool = True


class StatusBody(BaseModel):
    status: str


@router.get("/inbox")
def inbox_list(settings: SettingsDep, status_filter: Annotated[str | None, Query(alias="status")] = None) -> dict[str, Any]:
    try:
        return {"items": inbox.list_alerts(settings, status=status_filter or None, limit=100)}
    except Exception as exc:
        raise _guard(exc) from exc


@router.get("/inbox/{alert_id}")
def inbox_detail(alert_id: str, settings: SettingsDep) -> dict[str, Any]:
    from src.tools.handoff_brief import categorize, reply_draft

    try:
        convo = inbox.get_conversation(settings, alert_id)
    except Exception as exc:
        raise _guard(exc) from exc
    if not convo:
        raise HTTPException(404, detail="Conversation not found")
    question = str(convo["alert"].get("user_query") or "")
    convo["suggested_reply"] = reply_draft(categorize(question), settings.owner_name, settings.portfolio_url)
    return convo


@router.post("/inbox/{alert_id}/reply")
def inbox_reply(alert_id: str, body: ReplyBody, settings: SettingsDep) -> dict[str, Any]:
    convo = inbox.get_conversation(settings, alert_id)
    if not convo:
        raise HTTPException(404, detail="Conversation not found")
    result = inbox.send_visitor_reply(
        settings, alert=convo["alert"], owner_text=body.text, channel="admin", polish=body.polish
    )
    if not result.ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result.detail)
    return {"ok": True, "detail": result.detail, "sent_body": result.sent_body}


@router.post("/inbox/{alert_id}/status")
def inbox_status(alert_id: str, body: StatusBody, settings: SettingsDep) -> dict[str, Any]:
    try:
        inbox.set_alert_status(settings, alert_id, body.status)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except Exception as exc:
        raise _guard(exc) from exc
    return {"ok": True, "status": body.status}


# ── Blog posts ────────────────────────────────────────────────────────────────

class PostPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    description: str | None = Field(default=None, max_length=500)
    body_md: str | None = None
    tags: list[str] | None = None
    slug: str | None = None
    status: str | None = None
    image_url: str | None = None
    image_alt: str | None = None


class PostCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=500)
    body_md: str = ""
    tags: list[str] = []


class GenerateBody(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    angle: str | None = Field(default=None, max_length=600)
    tags: list[str] = []
    source_url: str | None = None
    status: str = "review"


@router.get("/posts")
def posts_list(settings: SettingsDep, status_filter: Annotated[str | None, Query(alias="status")] = None) -> dict[str, Any]:
    q = inbox.sb(settings).table("blog_posts").select(POST_LIST_COLS)
    if status_filter in POST_STATUSES:
        q = q.eq("status", status_filter)
    return {"items": q.order("created_at", desc=True).limit(200).execute().data or []}


@router.get("/posts/{post_id}")
def posts_get(post_id: str, settings: SettingsDep) -> dict[str, Any]:
    rows = inbox.sb(settings).table("blog_posts").select("*").eq("id", post_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, detail="Post not found")
    return rows[0]


@router.post("/posts", status_code=201)
def posts_create(body: PostCreate, settings: SettingsDep) -> dict[str, Any]:
    slug = _slugify(body.title) or "post"
    row = {
        "slug": f"{slug}-{datetime.now(timezone.utc):%m%d%H%M}",
        "title": body.title.strip(),
        "description": body.description.strip() or body.title.strip(),
        "body_md": body.body_md,
        "tags": [t.strip().lower() for t in body.tags if t.strip()][:16],
        "status": "draft",
    }
    return (inbox.sb(settings).table("blog_posts").insert(row).execute().data or [row])[0]


@router.patch("/posts/{post_id}")
def posts_patch(post_id: str, body: PostPatch, settings: SettingsDep) -> dict[str, Any]:
    client = inbox.sb(settings)
    current = client.table("blog_posts").select("id,slug,status,published_at").eq("id", post_id).limit(1).execute().data or []
    if not current:
        raise HTTPException(404, detail="Post not found")
    patch = body.model_dump(exclude_none=True)
    if "tags" in patch:
        patch["tags"] = [t.strip().lower() for t in patch["tags"] if t.strip()][:16]
    if "slug" in patch:
        patch["slug"] = _slugify(patch["slug"])
        if not patch["slug"]:
            raise HTTPException(422, detail="Slug is empty")
    if "image_url" in patch:
        patch["og_image_url"] = patch["image_url"] or None
        patch["image_url"] = patch["image_url"] or None
    going_live = False
    if "status" in patch:
        if patch["status"] not in POST_STATUSES:
            raise HTTPException(422, detail=f"status must be one of {', '.join(POST_STATUSES)}")
        going_live = patch["status"] == "published" and current[0]["status"] != "published"
        if going_live and not current[0].get("published_at"):
            patch["published_at"] = datetime.now(timezone.utc).isoformat()
    if not patch:
        raise HTTPException(422, detail="Nothing to update")
    try:
        rows = client.table("blog_posts").update(patch).eq("id", post_id).execute().data or []
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            raise HTTPException(409, detail="That slug is already used by another post") from exc
        raise _guard(exc) from exc
    _clear_blog_cache()
    post = rows[0] if rows else {}
    if going_live and post:
        from src.blog.publish_webhook import notify_blog_post_published

        notify_blog_post_published(post=post, source="admin", pipeline_run_key=None)
    return post


@router.delete("/posts/{post_id}")
def posts_delete(post_id: str, settings: SettingsDep) -> dict[str, Any]:
    inbox.sb(settings).table("blog_posts").delete().eq("id", post_id).execute()
    _clear_blog_cache()
    return {"ok": True}


# One writer run at a time; state is per-process, which is enough for one owner.
_GENERATION: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "result": None, "error": None}
_GENERATION_LOCK = threading.Lock()


def _run_generation(settings: Settings, topic: dict[str, Any] | None, post_status: str) -> None:
    from src.blog.pipeline import BlogPipelineConfig, run_blog_draft_once
    from src.telegram_bot.notify import notify_post_written

    try:
        cfg = BlogPipelineConfig.from_full_settings(settings)
        cfg.post_status_override = post_status
        result = run_blog_draft_once(cfg, topic=topic)
        _GENERATION.update(result=result, error=None)
        _settle_keyword(settings, topic, "written" if result.get("status") == "inserted" else "candidate", result.get("id"))
        notify_post_written(settings, result)
    except Exception as exc:
        logger.exception("admin: generation failed")
        _GENERATION.update(result=None, error=str(exc)[:600])
        _settle_keyword(settings, topic, "candidate", None)
    finally:
        _GENERATION.update(running=False, finished_at=datetime.now(timezone.utc).isoformat())
        _clear_blog_cache()


def _settle_keyword(settings: Settings, topic: dict[str, Any] | None, state: str, post_id: Any) -> None:
    """A keyword being written becomes `written` (linked to its post) or goes back to `candidate`."""
    keyword_id = (topic or {}).get("keyword_id")
    if not keyword_id:
        return
    try:
        from src.newsroom.store import NewsroomStore

        patch: dict[str, Any] = {"status": state}
        if post_id:
            patch["post_id"] = str(post_id)
        NewsroomStore.from_settings(settings).set_keyword(str(keyword_id), patch)
    except Exception as exc:
        logger.warning("keyword status not updated: %s", exc)


def start_generation(settings: Settings, topic: dict[str, Any] | None, post_status: str = "review") -> bool:
    """Start the writer in a thread. False if one is already running."""
    with _GENERATION_LOCK:
        if _GENERATION["running"]:
            return False
        _GENERATION.update(
            running=True,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
            result=None,
            error=None,
            topic=(topic or {}).get("title"),
        )
    threading.Thread(target=_run_generation, args=(settings, topic, post_status), daemon=True).start()
    return True


@router.post("/posts/generate", status_code=202, summary="Write a post now (from a topic, or the best queued idea)")
def posts_generate(body: GenerateBody, settings: SettingsDep) -> dict[str, Any]:
    if body.status not in POST_STATUSES:
        raise HTTPException(422, detail=f"status must be one of {', '.join(POST_STATUSES)}")
    topic = None
    if body.title and body.title.strip():
        topic = {"title": body.title.strip(), "angle": body.angle, "tags": body.tags, "source_url": body.source_url}
    if not start_generation(settings, topic, body.status):
        raise HTTPException(409, detail="The writer is already running")
    return {"status": "accepted", "watch": "GET /admin/generation"}


@router.get("/generation")
def generation_state() -> dict[str, Any]:
    return dict(_GENERATION)


# ── Ideas ─────────────────────────────────────────────────────────────────────

class IdeaCreate(BaseModel):
    title: str = Field(min_length=6, max_length=300)
    angle: str = Field(default="", max_length=600)
    tags: list[str] = []
    source_url: str | None = None


def add_manual_idea(settings: Settings, title: str, angle: str = "", tags: list[str] | None = None, source_url: str | None = None) -> dict[str, Any]:
    row = {
        "source": "manual",
        "title": title.strip(),
        "angle": (angle or "").strip(),
        "normalized_tags": [t.strip().lower() for t in (tags or []) if t.strip()][:12],
        "topic_key": _slugify(title).replace("-", "_")[:80] or "topic",
        "skill_fit": "high",
        "source_url": (source_url or "").strip() or None,
    }
    return (inbox.sb(settings).table("blog_ideas").insert(row).execute().data or [row])[0]


@router.get("/ideas")
def ideas_list(settings: SettingsDep, status_filter: Annotated[str, Query(alias="status")] = "pending") -> dict[str, Any]:
    q = inbox.sb(settings).table("blog_ideas").select(
        "id,created_at,source,source_url,title,angle,normalized_tags,skill_fit,status,skip_reason"
    )
    if status_filter != "all":
        q = q.eq("status", status_filter)
    return {"items": q.order("created_at", desc=True).limit(100).execute().data or []}


@router.post("/ideas", status_code=201)
def ideas_create(body: IdeaCreate, settings: SettingsDep) -> dict[str, Any]:
    try:
        return add_manual_idea(settings, body.title, body.angle, body.tags, body.source_url)
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            raise HTTPException(409, detail="An idea with this topic is already queued") from exc
        raise _guard(exc) from exc


@router.post("/ideas/{idea_id}/skip")
def ideas_skip(idea_id: str, settings: SettingsDep) -> dict[str, Any]:
    inbox.sb(settings).table("blog_ideas").update({"status": "skipped", "skip_reason": "skipped in admin"}).eq(
        "id", idea_id
    ).execute()
    return {"ok": True}


# ── Projects ──────────────────────────────────────────────────────────────────

class ProjectLink(BaseModel):
    type: str = Field(min_length=1, max_length=40)
    href: str = Field(min_length=4, max_length=600)


class ProjectBody(BaseModel):
    slug: str | None = None
    title: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    dates: str | None = Field(default=None, max_length=80)
    href: str | None = Field(default=None, max_length=600)
    image: str | None = Field(default=None, max_length=600)
    technologies: list[str] | None = None
    links: list[ProjectLink] | None = None
    visible: bool | None = None
    featured: bool | None = None
    active: bool | None = None
    client: bool | None = None
    sort_order: int | None = None


def _project_patch(body: ProjectBody) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "slug" in patch:
        patch["slug"] = _slugify(patch["slug"])
    if "technologies" in patch:
        patch["technologies"] = [t.strip() for t in patch["technologies"] if t.strip()][:24]
    for key in ("href", "image"):
        if key in patch and not patch[key].strip():
            patch[key] = None
    return patch


@router.get("/projects")
def projects_list(settings: SettingsDep) -> dict[str, Any]:
    try:
        rows = inbox.sb(settings).table("projects").select("*").order("sort_order").order("created_at").execute().data
    except Exception as exc:
        raise _guard(exc) from exc
    return {"items": rows or []}


@router.post("/projects", status_code=201)
def projects_create(body: ProjectBody, settings: SettingsDep) -> dict[str, Any]:
    patch = _project_patch(body)
    if not patch.get("title"):
        raise HTTPException(422, detail="title is required")
    patch["slug"] = patch.get("slug") or _slugify(patch["title"])
    if not patch["slug"]:
        raise HTTPException(422, detail="slug is empty")
    try:
        return (inbox.sb(settings).table("projects").insert(patch).execute().data or [patch])[0]
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            raise HTTPException(409, detail="A project with this slug already exists") from exc
        raise _guard(exc) from exc


@router.patch("/projects/{project_id}")
def projects_patch(project_id: str, body: ProjectBody, settings: SettingsDep) -> dict[str, Any]:
    patch = _project_patch(body)
    if not patch:
        raise HTTPException(422, detail="Nothing to update")
    try:
        rows = inbox.sb(settings).table("projects").update(patch).eq("id", project_id).execute().data or []
    except Exception as exc:
        raise _guard(exc) from exc
    if not rows:
        raise HTTPException(404, detail="Project not found")
    return rows[0]


@router.delete("/projects/{project_id}")
def projects_delete(project_id: str, settings: SettingsDep) -> dict[str, Any]:
    try:
        inbox.sb(settings).table("projects").delete().eq("id", project_id).execute()
    except Exception as exc:
        raise _guard(exc) from exc
    return {"ok": True}


@public_router.get("/projects", summary="Project rows managed in the admin (overrides + additions)")
def public_projects(settings: SettingsDep) -> dict[str, Any]:
    """
    Public and unauthenticated on purpose: it only returns what the portfolio
    shows anyway. Hidden rows are included as ``{slug, visible: false}`` so the
    site knows to hide a built-in project. Empty list until the table exists.
    """
    try:
        rows = inbox.sb(settings).table("projects").select("*").order("sort_order").order("created_at").execute().data or []
    except Exception as exc:
        if not inbox.is_missing_schema(exc):
            logger.warning("public projects failed: %s", exc)
        return {"items": []}
    items = []
    for r in rows:
        if not r.get("visible", True):
            items.append({"slug": r["slug"], "visible": False})
            continue
        r.pop("id", None)
        r.pop("created_at", None)
        items.append(r)
    return {"items": items}
