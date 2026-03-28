"""
Twice-weekly blog draft pipeline: propose topic → dedup → write Markdown → insert draft.
"""
from __future__ import annotations

import json
import logging
import os
import re
import warnings
from dataclasses import dataclass
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger("aegis.blog")

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    import google.generativeai as genai


IDEA_PROMPT = """You suggest ONE technical blog post idea for {owner_name}'s site ({portfolio_url}).
Focus areas: full-stack engineering, AI/ML in production, automation, developer tooling.

Rules:
- Propose something useful and specific, not generic fluff.
- Do NOT claim real-time news, product release dates, or statistics you cannot verify.
- topic_key must be a stable snake_case identifier (e.g. rag_chunking_strategies), unique to this angle.

Respond with ONLY valid JSON (no markdown fences):
{{"title": "string", "topic_key": "string", "angle": "one sentence", "tags": ["tag1", "tag2"]}}
"""

WRITE_PROMPT = """You are writing a draft blog post for {owner_name}'s technical blog ({portfolio_url}).

Topic: {title}
Angle: {angle}
Suggested tags: {tags}

Rules:
- Write in Markdown: start with an H2 (##), use ## and ### for sections, include a short intro and conclusion.
- No uncited factual claims about companies, dates, or numbers; prefer patterns and experience-based advice.
- If you mention tools or APIs, keep descriptions accurate at a high level; do not invent version numbers.
- Length: roughly 800–1500 words of prose (code blocks optional but short).
- Voice: clear, practical, slightly warm — not corporate.

Respond with ONLY valid JSON (no markdown fences):
{{
  "slug": "kebab-case-url-segment",
  "title": "final title",
  "description": "one line for SEO/listing, max 200 chars",
  "body_md": "full markdown article only (no YAML frontmatter)",
  "tags": ["lowercase", "tags"]
}}
"""


@dataclass
class BlogPipelineConfig:
    gemini_api_key: str
    gemini_model: str
    supabase_url: str
    supabase_service_key: str
    owner_name: str = "Yabibal"
    portfolio_url: str = "https://yabibal.site"
    default_og_image_url: str | None = None

    @classmethod
    def from_full_settings(cls, settings: Any) -> BlogPipelineConfig:
        return cls(
            gemini_api_key=settings.gemini_api_key,
            gemini_model=settings.gemini_model,
            supabase_url=settings.supabase_url,
            supabase_service_key=settings.supabase_service_key,
            owner_name=settings.owner_name,
            portfolio_url=settings.portfolio_url,
            default_og_image_url=os.getenv("BLOG_DEFAULT_OG_IMAGE") or None,
        )

    @classmethod
    def from_minimal_env(cls) -> BlogPipelineConfig:
        """GitHub Actions / CI: only required keys in the environment."""
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        url = os.environ.get("SUPABASE_URL", "").strip()
        svc = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not key or not url or not svc:
            raise ValueError(
                "Set GEMINI_API_KEY, SUPABASE_URL, and SUPABASE_SERVICE_KEY in the environment."
            )
        model_raw = os.environ.get("GEMINI_MODEL", "").strip()
        return cls(
            gemini_api_key=key,
            gemini_model=model_raw or "gemini-2.5-flash",
            supabase_url=url,
            supabase_service_key=svc,
            owner_name=os.environ.get("OWNER_NAME", "Yabibal").strip(),
            portfolio_url=os.environ.get("PORTFOLIO_URL", "https://yabibal.site").strip(),
            default_og_image_url=os.getenv("BLOG_DEFAULT_OG_IMAGE") or None,
        )


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}\s*$", text)
    if not m:
        raise ValueError(f"No JSON object found in model output: {text[:400]!r}")
    return json.loads(m.group(0))


def _slugify_kebab(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "post"


def _get_sb_client(cfg: BlogPipelineConfig) -> Client:
    return create_client(cfg.supabase_url, cfg.supabase_service_key)


def _topic_exists(client: Client, topic_key: str) -> bool:
    r = (
        client.table("blog_posts")
        .select("id")
        .eq("topic_key", topic_key)
        .limit(1)
        .execute()
    )
    return bool(r.data)


def _slug_taken(client: Client, slug: str) -> bool:
    r = (
        client.table("blog_posts")
        .select("id")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    return bool(r.data)


def _unique_slug(client: Client, base_slug: str) -> str:
    if not _slug_taken(client, base_slug):
        return base_slug
    for i in range(2, 50):
        candidate = f"{base_slug}-{i}"
        if not _slug_taken(client, candidate):
            return candidate
    raise RuntimeError("Could not allocate unique slug")


def run_blog_draft_once(cfg: BlogPipelineConfig) -> dict[str, Any]:
    """
    One full pass: idea → dedup → write → insert draft.

    Returns a dict:
      - status: "inserted" | "skipped_duplicate"
      - topic_key, slug (when applicable)
      - id (uuid string when inserted)
    """
    genai.configure(api_key=cfg.gemini_api_key)
    model = genai.GenerativeModel(cfg.gemini_model)
    client = _get_sb_client(cfg)

    idea_text = model.generate_content(
        IDEA_PROMPT.format(
            owner_name=cfg.owner_name,
            portfolio_url=cfg.portfolio_url,
        )
    ).text
    idea = _extract_json_object(idea_text or "")
    topic_key = str(idea["topic_key"]).strip().lower().replace(" ", "_")
    topic_key = re.sub(r"[^a-z0-9_]+", "", topic_key)
    if not topic_key:
        raise ValueError("Empty topic_key from idea model")

    if _topic_exists(client, topic_key):
        logger.info("blog draft skipped: topic_key already exists (%s)", topic_key)
        return {"status": "skipped_duplicate", "reason": "topic_key", "topic_key": topic_key}

    title = str(idea["title"]).strip()
    angle = str(idea.get("angle", "")).strip()
    tags_idea = idea.get("tags") or []
    if not isinstance(tags_idea, list):
        tags_idea = []
    tags_idea = [str(t).strip().lower() for t in tags_idea if str(t).strip()][:12]

    write_text = model.generate_content(
        WRITE_PROMPT.format(
            owner_name=cfg.owner_name,
            portfolio_url=cfg.portfolio_url,
            title=title,
            angle=angle or title,
            tags=", ".join(tags_idea) if tags_idea else "general",
        )
    ).text
    draft = _extract_json_object(write_text or "")

    raw_slug = _slugify_kebab(str(draft.get("slug") or title))
    slug = _unique_slug(client, raw_slug)

    body_md = str(draft.get("body_md", "")).strip()
    if not body_md:
        raise ValueError("Empty body_md from write model")

    final_title = str(draft.get("title", title)).strip() or title
    description = str(draft.get("description", "")).strip()[:500]
    if not description:
        description = (body_md[:197] + "…") if len(body_md) > 200 else body_md

    tags = draft.get("tags") or tags_idea
    if not isinstance(tags, list):
        tags = tags_idea
    tags = [str(t).strip().lower() for t in tags if str(t).strip()][:16]

    row: dict[str, Any] = {
        "slug": slug,
        "title": final_title,
        "description": description,
        "body_md": body_md,
        "status": "draft",
        "tags": tags,
        "topic_key": topic_key,
        "published_at": None,
        "image_url": cfg.default_og_image_url,
        "og_image_url": cfg.default_og_image_url,
    }

    ins = client.table("blog_posts").insert(row).execute()
    if not ins.data:
        raise RuntimeError("Supabase insert returned no data")

    new_id = ins.data[0].get("id")
    logger.info("blog draft inserted id=%s slug=%s topic_key=%s", new_id, slug, topic_key)
    return {
        "status": "inserted",
        "id": str(new_id) if new_id else None,
        "slug": slug,
        "topic_key": topic_key,
    }
