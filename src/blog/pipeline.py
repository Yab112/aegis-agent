"""
Blog pipeline: propose topic → dedup → write Markdown → insert row (status from env).
"""
from __future__ import annotations

import json
import logging
import os
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
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

WRITE_META_PROMPT = """You plan a technical blog post for {owner_name}'s site ({portfolio_url}).

Topic: {title}
Angle: {angle}
Suggested tags: {tags}

Return ONLY a small JSON object (no markdown fences, no body text here):
- slug: kebab-case URL segment
- title: final headline (plain text, no line breaks)
- description: one line for SEO/cards, max 200 characters, no line breaks or double quotes inside
- tags: array of lowercase strings (max 12)

Example shape:
{{"slug": "my-topic-slug", "title": "My Title", "description": "One line only.", "tags": ["tag1"]}}
"""

WRITE_BODY_PROMPT = """You write the ARTICLE ONLY for {owner_name}'s technical blog ({portfolio_url}).

Planned title: {title}
Angle: {angle}
Tags (use naturally if they fit): {tags}

Output rules:
- Output ONLY Markdown for the article body. No JSON. No YAML frontmatter.
- First line must start with ## (your first heading).
- Use ## and ### for sections; include intro and conclusion.
- Roughly 600–1200 words; short code blocks allowed.
- No uncited factual claims about companies, dates, or statistics; prefer patterns and experience.
- Voice: clear, practical, slightly warm — not corporate.
- Do not wrap the whole article in ``` markdown fences.
- No preamble like "Here is the article:" — start directly with ##.
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


def _strip_markdown_code_fence(text: str) -> str:
    """Remove leading ```json / ``` wrapper models often add despite instructions."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if not lines:
        return t
    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    elif lines and lines[-1].strip().endswith("```"):
        lines[-1] = lines[-1].rsplit("```", 1)[0].rstrip()
    return "\n".join(lines).strip()


def _strip_outer_markdown_fence(text: str) -> str:
    """If the model wrapped the whole article in ``` / ```markdown, remove one outer fence."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if not lines:
        return t
    first = lines[0].strip()
    if first.startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_balanced_json_object(text: str) -> str:
    """Find first top-level `{` … `}` with string/escape awareness (handles body_md with braces)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("No opening brace in model output")
    depth = 0
    in_string = False
    escape = False
    quote: str | None = None
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif quote and ch == quote:
                in_string = False
                quote = None
            continue
        if ch == '"':
            in_string = True
            quote = '"'
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError(
        "JSON object appears truncated (no closing `}`). "
        "Try raising BLOG_MAX_OUTPUT_TOKENS or shortening the article in the prompt."
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip() if text else ""
    if not raw:
        raise ValueError("Empty model output")
    cleaned = _strip_markdown_code_fence(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    try:
        chunk = _extract_balanced_json_object(cleaned)
        return json.loads(chunk)
    except (json.JSONDecodeError, ValueError) as e:
        preview = raw[:500].replace("\n", "\\n")
        raise ValueError(f"Could not parse JSON from model: {e}; preview={preview!r}") from e


def _blog_generation_config() -> Any:
    """Larger default output so write step is not cut off mid-JSON."""
    max_tokens = int(os.environ.get("BLOG_MAX_OUTPUT_TOKENS", "8192"))
    max_tokens = max(2048, min(max_tokens, 65536))
    try:
        return genai.GenerationConfig(max_output_tokens=max_tokens)
    except AttributeError:
        return genai.types.GenerationConfig(max_output_tokens=max_tokens)


def normalize_topic_key(raw: str) -> str:
    topic_key = str(raw).strip().lower().replace(" ", "_")
    topic_key = re.sub(r"[^a-z0-9_]+", "", topic_key)
    return topic_key or "topic"


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


def _post_status_after_write() -> tuple[str, str | None]:
    """
    BLOG_POST_STATUS_AFTER_WRITE: draft | review | published (default draft).

    - draft: insert as before; you must promote manually or via publish flow.
    - review: ready for blog-publish.yml (review → published) with no human edit.
    - published: live immediately (sets published_at); public API shows the post.
    """
    raw = os.environ.get("BLOG_POST_STATUS_AFTER_WRITE", "draft").strip().lower()
    if raw in ("published", "publish", "live"):
        return "published", datetime.now(timezone.utc).isoformat()
    if raw == "review":
        return "review", None
    if raw == "draft":
        return "draft", None
    logger.warning(
        "Invalid BLOG_POST_STATUS_AFTER_WRITE=%r; using draft", raw
    )
    return "draft", None


def _pick_pending_blog_idea(client: Client) -> dict[str, Any] | None:
    """Oldest pending first within same skill tier (high before medium before low)."""
    r = client.table("blog_ideas").select("*").eq("status", "pending").execute()
    rows = r.data or []
    if not rows:
        return None
    rank = {"high": 0, "medium": 1, "low": 2}
    rows.sort(
        key=lambda x: (
            rank.get(str(x.get("skill_fit") or "medium").lower(), 2),
            x.get("created_at") or "",
        )
    )
    return rows[0]


def run_blog_draft_once(cfg: BlogPipelineConfig) -> dict[str, Any]:
    """
    One full pass: idea → dedup → write → insert blog_posts row.

    Insert status and published_at follow BLOG_POST_STATUS_AFTER_WRITE (see _post_status_after_write).

    Returns a dict:
      - status: "inserted" | "skipped_duplicate" | "no_pending_ideas"
      - topic_key, slug (when applicable)
      - id (uuid string when inserted)
      - post_status, published_at when inserted
    """
    genai.configure(api_key=cfg.gemini_api_key)
    model = genai.GenerativeModel(cfg.gemini_model)
    client = _get_sb_client(cfg)

    gen_cfg = _blog_generation_config()
    idea_id: str | None = None
    idea_row = _pick_pending_blog_idea(client)

    if idea_row:
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
    meta_text = model.generate_content(
        WRITE_META_PROMPT.format(
            owner_name=cfg.owner_name,
            portfolio_url=cfg.portfolio_url,
            title=title,
            angle=angle or title,
            tags=tags_hint,
        ),
        generation_config=gen_cfg,
    ).text
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

    body_text = model.generate_content(
        WRITE_BODY_PROMPT.format(
            owner_name=cfg.owner_name,
            portfolio_url=cfg.portfolio_url,
            title=final_title,
            angle=angle or title,
            tags=tags_hint,
        ),
        generation_config=gen_cfg,
    ).text
    body_md = _strip_outer_markdown_fence(body_text or "").strip()
    if not body_md:
        raise ValueError("Empty body from write-body model")

    raw_slug = _slugify_kebab(str(meta.get("slug") or final_title))
    slug = _unique_slug(client, raw_slug)

    post_status, published_at = _post_status_after_write()
    row: dict[str, Any] = {
        "slug": slug,
        "title": final_title,
        "description": description,
        "body_md": body_md,
        "status": post_status,
        "tags": tags,
        "topic_key": topic_key,
        "published_at": published_at,
        "image_url": cfg.default_og_image_url,
        "og_image_url": cfg.default_og_image_url,
    }

    ins = client.table("blog_posts").insert(row).execute()
    if not ins.data:
        raise RuntimeError("Supabase insert returned no data")

    new_id = ins.data[0].get("id")
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
    return {
        "status": "inserted",
        "id": str(new_id) if new_id else None,
        "slug": slug,
        "topic_key": topic_key,
        "blog_idea_id": idea_id,
        "post_status": post_status,
        "published_at": published_at,
    }
