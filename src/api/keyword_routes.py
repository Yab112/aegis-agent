"""
Keyword API: what the writer agent (or you) asks before writing.

  GET  /keywords/best              the keywords worth writing now
  GET  /keywords/analyze?q=        full breakdown for any phrase, on demand
  GET  /keywords/{id}              one keyword with every signal
  GET  /keywords/{id}/sources      the trusted pages to read first
  POST /keywords/{id}/write        start the writer on it
  POST /keywords/{id}/dismiss      never suggest it again
  POST /keywords/refresh           recompute now (background)

All routes need the API secret. They describe your publishing strategy, so they are not public.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from config.settings import Settings, get_settings
from src.api.blog_internal import verify_internal_secret
from src.newsroom import service
from src.newsroom.categories import category_labels
from src.newsroom.store import MIGRATION_HINT, NewsroomStore

logger = logging.getLogger("aegis.keywords")

router = APIRouter(prefix="/keywords", tags=["keywords"], dependencies=[Depends(verify_internal_secret)])
SettingsDep = Annotated[Settings, Depends(get_settings)]

FORMAT_ANGLE = {
    "news": "Report what happened, who said it and why it matters, from the primary sources.",
    "analysis": "Compare what the sources say, show the numbers, and take a position.",
    "explainer": "Explain the concept plainly for an engineer who has not followed it.",
    "course": "Teach it step by step: prerequisites, core ideas, worked example, exercises.",
}


def _store(settings: Settings) -> NewsroomStore:
    return NewsroomStore.from_settings(settings)


def _missing_schema(exc: Exception) -> bool:
    raw = str(exc).lower()
    return any(code in raw for code in ("pgrst204", "pgrst205", "42703", "42p01", "does not exist", "could not find"))


def guard(exc: Exception) -> HTTPException:
    if _missing_schema(exc):
        return HTTPException(503, detail=MIGRATION_HINT)
    logger.exception("newsroom request failed")
    return HTTPException(500, detail=str(exc)[:400])


def _public(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["category_label"] = category_labels().get(row.get("category") or "", "AI news")
    return row


def topic_from_keyword(kw: dict[str, Any]) -> dict[str, Any]:
    """What the writer needs: a working title, the angle for the format, and the pages to read."""
    sources = kw.get("sources") or []
    display = kw.get("display") or kw["phrase"]
    lead = sources[0]["title"] if sources else display
    return {
        "title": lead if display.lower() in lead.lower() else f"{display}: {lead}",
        "angle": FORMAT_ANGLE.get(kw.get("suggested_format") or "analysis", FORMAT_ANGLE["analysis"]),
        "tags": [kw.get("category") or "ai-news"],
        "source_url": sources[0]["url"] if sources else None,
        "sources": sources[:12],
        "keyword": display,
        "category": kw.get("category"),
        "format": kw.get("suggested_format"),
        "topic_key": "kw_" + "".join(c if c.isalnum() else "_" for c in kw["phrase"])[:70],
    }


@router.get("/best", summary="Keywords worth writing now, best first")
def best(
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    category: str | None = None,
    status_filter: Annotated[str, Query(alias="status")] = "candidate",
) -> dict[str, Any]:
    try:
        rows = _store(settings).best_keywords(limit=limit, category=category, status=None if status_filter == "all" else status_filter)
    except Exception as exc:
        raise guard(exc) from exc
    return {"items": [_public(r) for r in rows], "categories": category_labels()}


@router.get("/analyze", summary="Score any phrase now (live signal lookups, takes a few seconds)")
def analyze(settings: SettingsDep, q: Annotated[str, Query(min_length=2, max_length=80)]) -> dict[str, Any]:
    try:
        return _public(service.analyze_phrase(_store(settings), q))
    except Exception as exc:
        raise guard(exc) from exc


@router.post("/refresh", status_code=202, summary="Recompute keywords from the collected headlines")
def refresh(settings: SettingsDep, background: BackgroundTasks) -> dict[str, str]:
    background.add_task(service.refresh_keywords, _store(settings))
    return {"status": "accepted"}


def _keyword_or_404(settings: Settings, keyword_id: str) -> tuple[NewsroomStore, dict[str, Any]]:
    store = _store(settings)
    try:
        kw = store.get_keyword(keyword_id)
    except Exception as exc:
        raise guard(exc) from exc
    if not kw:
        raise HTTPException(404, detail="Keyword not found")
    return store, kw


@router.get("/{keyword_id}")
def detail(keyword_id: str, settings: SettingsDep) -> dict[str, Any]:
    return _public(_keyword_or_404(settings, keyword_id)[1])


@router.get("/{keyword_id}/sources", summary="Trusted pages to read before writing, lab sources first")
def sources(keyword_id: str, settings: SettingsDep) -> dict[str, Any]:
    kw = _keyword_or_404(settings, keyword_id)[1]
    return {"keyword": kw.get("display") or kw["phrase"], "items": kw.get("sources") or []}


@router.post("/{keyword_id}/write", status_code=202, summary="Research and write a post on this keyword")
def write(keyword_id: str, settings: SettingsDep) -> dict[str, Any]:
    from src.api.admin_routes import start_generation

    store, kw = _keyword_or_404(settings, keyword_id)
    topic = topic_from_keyword(kw)
    topic["keyword_id"] = keyword_id
    if not start_generation(settings, topic, "review"):
        raise HTTPException(409, detail="The writer is already running")
    store.set_keyword(keyword_id, {"status": "writing"})
    return {"status": "accepted", "topic": {k: topic[k] for k in ("title", "keyword", "category", "format")}}


@router.post("/{keyword_id}/dismiss")
def dismiss(keyword_id: str, settings: SettingsDep) -> dict[str, bool]:
    store, _ = _keyword_or_404(settings, keyword_id)
    store.set_keyword(keyword_id, {"status": "dismissed"})
    return {"ok": True}
