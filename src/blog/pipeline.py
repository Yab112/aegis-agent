"""
Blog pipeline: propose topic → dedup → research → write Markdown → validate → insert row.

The stages live in small modules; this file is the orchestration only:
  prompts.py  config.py  llm.py  quality.py  store.py  cover.py  links.py
  research.py (source pack)  rich_content.py (chart / figure / link validation)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from src.blog.config import BlogPipelineConfig
from src.blog.cover import _build_cover_prompt, _generate_cover_image_bytes, _upload_cover_to_cloudinary
from src.blog.links import _make_figure_resolver, _resource_links_for_post
from src.blog.llm import (
    _blog_generation_config,
    _extract_json_object,
    _run_stage_with_retry,
    _stage_retry_settings,
    _strip_outer_markdown_fence,
    genai,
)
from src.blog.prompts import FORMATS, IDEA_PROMPT, KEYWORD_RULE, WRITE_BODY_PROMPT, WRITE_META_PROMPT
from src.blog.publish_webhook import notify_blog_post_published
from src.blog.quality import _evaluate_quality_gate, _quality_gate_settings
from src.blog.research import build_research_pack
from src.blog.rich_content import drop_advice_sections, process_body, prose_word_count, url_is_live
from src.blog.store import (
    _get_sb_client,
    _pick_pending_blog_idea,
    _post_status_after_write,
    _record_pipeline_failure,
    _run_key_exists,
    _slugify_kebab,
    _topic_exists,
    _unique_slug,
    normalize_topic_key,
)

logger = logging.getLogger("aegis.blog")

__all__ = ["BlogPipelineConfig", "WRITE_BODY_PROMPT", "normalize_topic_key", "run_blog_draft_once"]


def run_blog_draft_once(cfg: BlogPipelineConfig, topic: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    One full pass: idea → dedup → write → insert blog_posts row.

    Insert status and published_at follow BLOG_POST_STATUS_AFTER_WRITE (see _post_status_after_write).

    ``topic`` (admin / Telegram "write about X") skips the idea queue:
    ``{"title": str, "angle": str?, "tags": list[str]?, "source_url": str?}``.
    A topic from the keyword desk also carries ``keyword``, ``category``, ``format``
    (news | analysis | explainer | course), ``sources`` (trusted pages) and ``keyword_id``.

    Returns a dict:
      - status: "inserted" | "skipped_duplicate" | "no_pending_ideas"
      - topic_key, slug (when applicable)
      - id (uuid string when inserted)
      - post_status, published_at when inserted
    """
    genai.configure(api_key=cfg.gemini_api_key)
    model = genai.GenerativeModel(cfg.gemini_model)
    client = _get_sb_client(cfg)
    retry_attempts, retry_base_sleep = _stage_retry_settings()

    if _run_key_exists(client, cfg.pipeline_run_key):
        logger.info("blog draft skipped: pipeline_run_key already exists (%s)", cfg.pipeline_run_key)
        return {
            "status": "skipped_duplicate",
            "reason": "pipeline_run_key",
            "pipeline_run_key": cfg.pipeline_run_key,
        }

    gen_cfg = _blog_generation_config()
    idea_id: str | None = None
    idea_row = None if topic else _pick_pending_blog_idea(client)
    keyword = str((topic or {}).get("keyword") or "").strip()
    category = str((topic or {}).get("category") or "").strip() or None
    post_format = str((topic or {}).get("format") or "analysis").strip().lower()
    fmt = FORMATS.get(post_format) or FORMATS["analysis"]

    if topic:
        title = str(topic.get("title") or "").strip()
        if len(title) < 6:
            raise ValueError("topic.title is required")
        angle = str(topic.get("angle") or title).strip()
        topic_key = normalize_topic_key(str(topic.get("topic_key") or _slugify_kebab(title).replace("-", "_")))[:80]
        raw_tags = topic.get("tags") or []
        tags_idea = [str(t).strip().lower() for t in raw_tags if str(t).strip()][:12] if isinstance(raw_tags, list) else []
        source_url = str(topic.get("source_url") or "").strip() or None
        logger.info("blog draft using requested topic topic_key=%s", topic_key)
    elif idea_row:
        idea_id = str(idea_row["id"])
        topic_key = normalize_topic_key(idea_row.get("topic_key") or "")
        if not topic_key or topic_key == "topic":
            client.table("blog_ideas").update(
                {"status": "failed", "skip_reason": "invalid topic_key"}
            ).eq("id", idea_id).execute()
            raise ValueError("Empty topic_key on blog_ideas row")
        title = str(idea_row.get("title") or "").strip() or "Blog post"
        angle = str(idea_row.get("angle") or title).strip()
        tags_idea = idea_row.get("normalized_tags") or []
        if not isinstance(tags_idea, list):
            tags_idea = []
        tags_idea = [str(t).strip().lower() for t in tags_idea if str(t).strip()][:12]
        source_url = str(idea_row.get("source_url") or "").strip() or None
        logger.info("blog draft using queued idea id=%s topic_key=%s", idea_id, topic_key)
    else:
        fallback = os.environ.get("BLOG_FALLBACK_GEMINI_IDEA", "true").strip().lower()
        if fallback in ("0", "false", "no", "off"):
            logger.info("blog draft skipped: no pending blog_ideas and fallback disabled")
            return {"status": "no_pending_ideas"}

        idea_text = model.generate_content(
            IDEA_PROMPT.format(
                owner_name=cfg.owner_name,
                portfolio_url=cfg.portfolio_url,
            ),
            generation_config=gen_cfg,
        ).text
        idea = _extract_json_object(idea_text or "")
        topic_key = normalize_topic_key(idea["topic_key"])
        if not topic_key or topic_key == "topic":
            raise ValueError("Empty topic_key from idea model")

        title = str(idea["title"]).strip()
        angle = str(idea.get("angle", "")).strip()
        tags_idea = idea.get("tags") or []
        if not isinstance(tags_idea, list):
            tags_idea = []
        tags_idea = [str(t).strip().lower() for t in tags_idea if str(t).strip()][:12]
        source_url = None

    if _topic_exists(client, topic_key):
        logger.info("blog draft skipped: topic_key already exists (%s)", topic_key)
        if idea_id:
            client.table("blog_ideas").update(
                {
                    "status": "skipped",
                    "skip_reason": "topic_key already in blog_posts",
                }
            ).eq("id", idea_id).execute()
        return {"status": "skipped_duplicate", "reason": "topic_key", "topic_key": topic_key}

    tags_hint = ", ".join(tags_idea) if tags_idea else "general"
    try:
        meta_text = _run_stage_with_retry(
            "meta_generation",
            lambda: model.generate_content(
                WRITE_META_PROMPT.format(
                    owner_name=cfg.owner_name,
                    portfolio_url=cfg.portfolio_url,
                    title=title,
                    angle=angle or title,
                    tags=tags_hint,
                ),
                generation_config=gen_cfg,
            ).text,
            attempts=retry_attempts,
            base_sleep=retry_base_sleep,
        )
    except Exception as e:
        _record_pipeline_failure(
            client,
            stage="meta_generation",
            error=e,
            payload={"title": title, "angle": angle, "tags_hint": tags_hint},
            run_key=cfg.pipeline_run_key,
            topic_key=topic_key,
            slug=None,
            idea_id=idea_id,
        )
        if idea_id:
            client.table("blog_ideas").update(
                {"status": "failed", "skip_reason": "meta_generation_failed"}
            ).eq("id", idea_id).execute()
        raise
    meta = _extract_json_object(meta_text or "")

    final_title = str(meta.get("title", title)).strip() or title
    description = str(meta.get("description", "")).strip()
    description = (
        description.replace("\n", " ").replace("\r", " ").replace('"', "'")[:500]
    )
    if not description:
        description = (final_title[:197] + "…") if len(final_title) > 200 else final_title

    tags = meta.get("tags") or tags_idea
    if not isinstance(tags, list):
        tags = tags_idea
    tags = [str(t).strip().lower() for t in tags if str(t).strip()][:16]

    topic_sources = (topic or {}).get("sources")
    if not topic_sources:
        # No keyword behind this post: look for matching articles in our own library of trusted sources.
        from src.newsroom.library import find_sources_for_topic

        topic_sources = find_sources_for_topic(client, f"{final_title} {angle}")
        logger.info("blog research: %s matching article(s) found in the source library", len(topic_sources))

    pack = build_research_pack(
        api_key=cfg.gemini_api_key,
        model=cfg.gemini_model,
        title=final_title,
        angle=angle or title,
        source_url=source_url,
        extra_sources=topic_sources,
    )
    resource_links = _resource_links_for_post(pack, tags)

    try:
        body_text = _run_stage_with_retry(
            "body_generation",
            lambda: model.generate_content(
                WRITE_BODY_PROMPT.format(
                    owner_name=cfg.owner_name,
                    portfolio_url=cfg.portfolio_url,
                    title=final_title,
                    angle=angle or title,
                    tags=tags_hint,
                    sources=pack.prompt_block(),
                    structure=fmt["structure"],
                    keyword_rule=KEYWORD_RULE.format(keyword=keyword) if keyword else "",
                ),
                generation_config=gen_cfg,
            ).text,
            attempts=retry_attempts,
            base_sleep=retry_base_sleep,
        )
    except Exception as e:
        _record_pipeline_failure(
            client,
            stage="body_generation",
            error=e,
            payload={"title": final_title, "tags": tags},
            run_key=cfg.pipeline_run_key,
            topic_key=topic_key,
            slug=None,
            idea_id=idea_id,
        )
        if idea_id:
            client.table("blog_ideas").update(
                {"status": "failed", "skip_reason": "body_generation_failed"}
            ).eq("id", idea_id).execute()
        raise
    body_md = _strip_outer_markdown_fence(body_text or "").strip()
    if not body_md:
        raise ValueError("Empty body from write-body model")
    body_md, rich_report = process_body(
        body_md,
        allowed_urls=pack.urls() + [cfg.portfolio_url],
        figure_resolver=_make_figure_resolver(model, gen_cfg),
        url_checker=url_is_live,
    )
    if post_format != "course":  # a course teaches; everything else reports
        body_md, advice = drop_advice_sections(body_md)
        if advice:
            rich_report.notes.append("removed advice section(s): " + ", ".join(advice))
    logger.info("blog body validated: %s", rich_report.as_dict())

    raw_slug = _slugify_kebab(str(meta.get("slug") or final_title))
    slug = _unique_slug(client, raw_slug)

    quality_ok, quality_reason, dup_score = _evaluate_quality_gate(
        client,
        title=final_title,
        body_md=body_md,
        min_words_override=int(fmt["min_words"]) if topic and topic.get("format") else None,
    )

    image_prompt: str | None = None
    generated_image_url: str | None = None
    image_alt: str | None = None
    if cfg.blog_generate_images:
        try:
            image_prompt, image_alt = _build_cover_prompt(
                model,
                gen_cfg,
                title=final_title,
                description=description,
                angle=angle,
                tags=tags,
            )
            generated = _generate_cover_image_bytes(cfg, image_prompt)
            if generated:
                image_bytes, mime_type = generated
                generated_image_url = _upload_cover_to_cloudinary(
                    cfg,
                    slug=slug,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    image_prompt=image_prompt,
                )
        except Exception as e:
            logger.warning("cover generation pipeline skipped for slug=%s: %s", slug, e)
            _record_pipeline_failure(
                client,
                stage="cover_generation",
                error=e,
                payload={"slug": slug, "title": final_title},
                run_key=cfg.pipeline_run_key,
                topic_key=topic_key,
                slug=slug,
                idea_id=idea_id,
            )

    post_status, published_at = _post_status_after_write(cfg.post_status_override)
    _, _, _, _, block_on_quality_fail, _ = _quality_gate_settings()
    if not quality_ok:
        logger.warning(
            "quality gate failed slug=%s reason=%s dup_score=%.3f",
            slug,
            quality_reason,
            dup_score,
        )
        _record_pipeline_failure(
            client,
            stage="quality_gate",
            error=ValueError(quality_reason),
            payload={"slug": slug, "title": final_title, "dup_score": dup_score},
            run_key=cfg.pipeline_run_key,
            topic_key=topic_key,
            slug=slug,
            idea_id=idea_id,
        )
        if block_on_quality_fail:
            if idea_id:
                client.table("blog_ideas").update(
                    {"status": "failed", "skip_reason": f"quality_gate:{quality_reason}"}
                ).eq("id", idea_id).execute()
            raise ValueError(f"Quality gate failed: {quality_reason}")
        # Soft enforcement: do not auto-publish low-quality content.
        post_status = "review"
        published_at = None

    final_image_url = generated_image_url or cfg.default_og_image_url
    if generated_image_url is None and cfg.blog_generate_images and final_image_url:
        logger.warning(
            "blog cover: slug=%s using fallback image URL (no generated+uploaded cover; "
            "see prior warnings for Gemini vs Cloudinary)",
            slug,
        )
    row: dict[str, Any] = {
        "slug": slug,
        "title": final_title,
        "description": description,
        "body_md": body_md,
        "status": post_status,
        "tags": tags,
        "topic_key": topic_key,
        "resource_links": resource_links,
        "published_at": published_at,
        "image_url": final_image_url,
        "og_image_url": final_image_url,
        "image_alt": image_alt,
        "image_prompt": image_prompt,
        "pipeline_run_key": cfg.pipeline_run_key,
    }
    if category or keyword:  # columns from scripts/supabase_newsroom.sql
        row.update(category=category, keyword=keyword or None, format=post_format)

    try:
        ins = _run_stage_with_retry(
            "insert_post",
            lambda: client.table("blog_posts").insert(row).execute(),
            attempts=retry_attempts,
            base_sleep=retry_base_sleep,
        )
    except Exception as exc:
        message = str(exc)
        if (
            "resource_links" not in message
            and "image_prompt" not in message
            and "pipeline_run_key" not in message
            and "category" not in message
            and "keyword" not in message
            and "format" not in message
        ):
            _record_pipeline_failure(
                client,
                stage="insert_post",
                error=exc,
                payload={"slug": slug, "topic_key": topic_key, "status": post_status},
                run_key=cfg.pipeline_run_key,
                topic_key=topic_key,
                slug=slug,
                idea_id=idea_id,
            )
            if idea_id:
                client.table("blog_ideas").update(
                    {"status": "failed", "skip_reason": "insert_post_failed"}
                ).eq("id", idea_id).execute()
            raise
        fallback_row = dict(row)
        fallback_row.pop("resource_links", None)
        fallback_row.pop("image_prompt", None)
        fallback_row.pop("pipeline_run_key", None)
        for col in ("category", "keyword", "format"):
            fallback_row.pop(col, None)
        ins = _run_stage_with_retry(
            "insert_post_fallback",
            lambda: client.table("blog_posts").insert(fallback_row).execute(),
            attempts=retry_attempts,
            base_sleep=retry_base_sleep,
        )
    if not ins.data:
        raise RuntimeError("Supabase insert returned no data")

    new_id = ins.data[0].get("id")
    inserted_row = ins.data[0] if isinstance(ins.data[0], dict) else {}
    if idea_id and new_id:
        client.table("blog_ideas").update(
            {
                "status": "consumed",
                "consumed_by_post_id": str(new_id),
            }
        ).eq("id", idea_id).execute()
    logger.info(
        "blog post inserted id=%s slug=%s topic_key=%s post_status=%s",
        new_id,
        slug,
        topic_key,
        post_status,
    )
    if post_status == "published":
        webhook_payload = {
            "id": str(new_id) if new_id else None,
            "slug": inserted_row.get("slug") or slug,
            "title": inserted_row.get("title") or final_title,
            "topic_key": inserted_row.get("topic_key") or topic_key,
            "published_at": inserted_row.get("published_at") or published_at,
            "tags": inserted_row.get("tags") or tags,
            "image_url": inserted_row.get("image_url") or final_image_url,
            "og_image_url": inserted_row.get("og_image_url") or final_image_url,
        }
        notify_blog_post_published(
            post=webhook_payload,
            source="draft_pipeline",
            pipeline_run_key=cfg.pipeline_run_key,
        )
    return {
        "status": "inserted",
        "id": str(new_id) if new_id else None,
        "slug": slug,
        "topic_key": topic_key,
        "blog_idea_id": idea_id,
        "pipeline_run_key": cfg.pipeline_run_key,
        "post_status": post_status,
        "published_at": published_at,
        "quality_gate_passed": quality_ok,
        "quality_gate_reason": quality_reason,
        "title": final_title,
        "keyword": keyword or None,
        "keyword_id": (topic or {}).get("keyword_id"),
        "category": category,
        "format": post_format,
        "words": prose_word_count(body_md),
        "sources": len(pack.sources),
        "research_grounded": pack.grounded,
        "rich": rich_report.as_dict(),
    }
