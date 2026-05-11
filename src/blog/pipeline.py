"""
Blog pipeline: propose topic → dedup → write Markdown → insert row (status from env).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from supabase import Client, create_client

from src.blog.publish_webhook import notify_blog_post_published

logger = logging.getLogger("aegis.blog")

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    import google.generativeai as genai
    from google.generativeai import protos as genai_protos


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

IMAGE_PROMPT_PROMPT = """You write visual direction for a technical blog cover image.

Blog title: {title}
Description: {description}
Angle: {angle}
Tags: {tags}

Return ONLY valid JSON (no markdown fences):
{{
    "image_prompt": "A concise, vivid prompt for an AI image model (no text in image, no logos, no watermarks).",
    "image_alt": "A short alt text sentence describing the generated cover image."
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
    # Native image models: request IMAGE modality or the API often returns text-only (no cover bytes).
    blog_image_model: str = "gemini-2.5-flash-image"
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: str | None = None
    cloudinary_folder: str = "aegis/blog"
    blog_generate_images: bool = True
    pipeline_run_key: str | None = None

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
            blog_image_model=os.getenv("BLOG_IMAGE_MODEL", "gemini-2.5-flash-image").strip(),
            cloudinary_cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME") or None,
            cloudinary_api_key=os.getenv("CLOUDINARY_API_KEY") or None,
            cloudinary_api_secret=os.getenv("CLOUDINARY_API_SECRET") or None,
            cloudinary_folder=os.getenv("CLOUDINARY_FOLDER", "aegis/blog").strip() or "aegis/blog",
            blog_generate_images=os.getenv("BLOG_GENERATE_IMAGES", "true").strip().lower() not in ("0", "false", "no", "off"),
            pipeline_run_key=(os.getenv("BLOG_PIPELINE_RUN_KEY") or "").strip() or None,
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
            blog_image_model=os.getenv("BLOG_IMAGE_MODEL", "gemini-2.5-flash-image").strip(),
            cloudinary_cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME") or None,
            cloudinary_api_key=os.getenv("CLOUDINARY_API_KEY") or None,
            cloudinary_api_secret=os.getenv("CLOUDINARY_API_SECRET") or None,
            cloudinary_folder=os.getenv("CLOUDINARY_FOLDER", "aegis/blog").strip() or "aegis/blog",
            blog_generate_images=os.getenv("BLOG_GENERATE_IMAGES", "true").strip().lower() not in ("0", "false", "no", "off"),
            pipeline_run_key=(os.getenv("BLOG_PIPELINE_RUN_KEY") or "").strip() or None,
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


def _stage_retry_settings() -> tuple[int, int]:
    attempts = int(os.environ.get("BLOG_STAGE_RETRY_ATTEMPTS", "3"))
    base_sleep = int(os.environ.get("BLOG_STAGE_RETRY_BASE_SECONDS", "2"))
    attempts = max(1, min(attempts, 6))
    base_sleep = max(1, min(base_sleep, 20))
    return attempts, base_sleep


def _quality_gate_settings() -> tuple[bool, int, int, float, bool, list[str]]:
    enabled = os.environ.get("BLOG_QUALITY_GATE_ENABLED", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    min_words = int(os.environ.get("BLOG_QUALITY_MIN_WORDS", "450"))
    min_headings = int(os.environ.get("BLOG_QUALITY_MIN_HEADINGS", "3"))
    dup_threshold = float(os.environ.get("BLOG_DUPLICATE_SIM_THRESHOLD", "0.72"))
    block_on_fail = os.environ.get("BLOG_QUALITY_BLOCK_ON_FAIL", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    banned_raw = os.environ.get(
        "BLOG_BANNED_PHRASES",
        "as an ai language model,here is the article,in conclusion,overall,to summarize",
    )
    banned = [x.strip().lower() for x in banned_raw.split(",") if x.strip()]
    min_words = max(200, min(min_words, 2500))
    min_headings = max(2, min(min_headings, 10))
    dup_threshold = max(0.4, min(dup_threshold, 0.95))
    return enabled, min_words, min_headings, dup_threshold, block_on_fail, banned


def _tokenize_for_similarity(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", text.lower())
    return set(words)


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a.intersection(b))
    union = len(a.union(b))
    if union == 0:
        return 0.0
    return inter / union


def _max_similarity_against_recent_posts(client: Client, candidate_body: str) -> float:
    candidate_tokens = _tokenize_for_similarity(candidate_body)
    if not candidate_tokens:
        return 0.0
    rows_res = (
        client.table("blog_posts")
        .select("body_md")
        .order("created_at", desc=True)
        .limit(30)
        .execute()
    )
    max_sim = 0.0
    for row in rows_res.data or []:
        body = str(row.get("body_md") or "")
        sim = _jaccard_similarity(candidate_tokens, _tokenize_for_similarity(body))
        if sim > max_sim:
            max_sim = sim
    return max_sim


def _evaluate_quality_gate(client: Client, *, title: str, body_md: str) -> tuple[bool, str, float]:
    enabled, min_words, min_headings, dup_threshold, _, banned_phrases = _quality_gate_settings()
    if not enabled:
        return True, "disabled", 0.0

    words = re.findall(r"\b\w+\b", body_md)
    word_count = len(words)
    if word_count < min_words:
        return False, f"too_short:{word_count}<{min_words}", 0.0

    heading_count = sum(
        1 for line in body_md.splitlines() if line.strip().startswith("##")
    )
    if heading_count < min_headings:
        return False, f"insufficient_headings:{heading_count}<{min_headings}", 0.0

    lowered = body_md.lower()
    for phrase in banned_phrases:
        if phrase and phrase in lowered:
            return False, f"banned_phrase:{phrase}", 0.0

    dup_score = _max_similarity_against_recent_posts(client, body_md)
    if dup_score >= dup_threshold:
        return False, f"duplicate_similarity:{dup_score:.3f}>={dup_threshold}", dup_score

    if len(title.strip()) < 10:
        return False, "title_too_short", dup_score
    return True, "ok", dup_score


def _record_pipeline_failure(
    client: Client,
    *,
    stage: str,
    error: BaseException,
    payload: dict[str, Any],
    run_key: str | None,
    topic_key: str | None,
    slug: str | None,
    idea_id: str | None,
) -> None:
    row = {
        "stage": stage,
        "error_message": str(error)[:4000],
        "payload": payload,
        "pipeline_run_key": run_key,
        "topic_key": topic_key,
        "slug": slug,
        "blog_idea_id": idea_id,
    }
    try:
        client.table("blog_pipeline_failures").insert(row).execute()
    except Exception as log_exc:
        logger.warning("failed to write dead-letter row stage=%s err=%s", stage, log_exc)


def _run_stage_with_retry(
    stage: str,
    fn: Callable[[], Any],
    *,
    attempts: int,
    base_sleep: int,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            sleep_s = base_sleep * attempt
            logger.warning(
                "stage=%s failed attempt=%s/%s err=%s; retrying in %ss",
                stage,
                attempt,
                attempts,
                exc,
                sleep_s,
            )
            time.sleep(sleep_s)
    if last_exc is None:
        raise RuntimeError(f"stage {stage} failed without exception")
    raise last_exc


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


def _run_key_exists(client: Client, run_key: str | None) -> bool:
    if not run_key:
        return False
    r = (
        client.table("blog_posts")
        .select("id")
        .eq("pipeline_run_key", run_key)
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


def _extract_image_bytes_from_gemini_response(resp: Any) -> tuple[bytes, str] | None:
    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if not inline:
                continue
            mime_type = getattr(inline, "mime_type", None) or "image/png"
            data = getattr(inline, "data", None)
            if isinstance(data, bytes) and data:
                return data, mime_type
            if isinstance(data, str) and data.strip():
                try:
                    return base64.b64decode(data), mime_type
                except Exception:
                    continue

    # Some SDK variants expose parts directly on response.
    parts = getattr(resp, "parts", None) or []
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if not inline:
            continue
        mime_type = getattr(inline, "mime_type", None) or "image/png"
        data = getattr(inline, "data", None)
        if isinstance(data, bytes) and data:
            return data, mime_type
        if isinstance(data, str) and data.strip():
            try:
                return base64.b64decode(data), mime_type
            except Exception:
                continue
    return None


def _build_cover_prompt(
    model: Any,
    gen_cfg: Any,
    *,
    title: str,
    description: str,
    angle: str,
    tags: list[str],
) -> tuple[str, str]:
    tags_hint = ", ".join(tags) if tags else "general"
    raw = model.generate_content(
        IMAGE_PROMPT_PROMPT.format(
            title=title,
            description=description,
            angle=angle or title,
            tags=tags_hint,
        ),
        generation_config=gen_cfg,
    ).text
    payload = _extract_json_object(raw or "")
    image_prompt = str(payload.get("image_prompt") or "").strip()
    image_alt = str(payload.get("image_alt") or "").strip()
    if not image_prompt:
        image_prompt = (
            f"Editorial style technical illustration about {title}. "
            "No text, no logos, no watermark. Clean lighting, modern composition."
        )
    if not image_alt:
        image_alt = f"Cover image for {title}"
    return image_prompt[:1200], image_alt[:280]


def _cover_image_generation_config() -> dict[str, Any]:
    """Gemini image endpoints expect image output modality (see Google image-generation docs)."""
    return {
        "response_modalities": [genai_protos.GenerationConfig.Modality.IMAGE],
    }


def _generate_cover_image_bytes(cfg: BlogPipelineConfig, image_prompt: str) -> tuple[bytes, str] | None:
    try:
        image_model = genai.GenerativeModel(cfg.blog_image_model)
        resp = image_model.generate_content(
            image_prompt,
            generation_config=_cover_image_generation_config(),
        )
        out = _extract_image_bytes_from_gemini_response(resp)
        if out is None:
            fr = None
            br = None
            try:
                cands = getattr(resp, "candidates", None) or []
                if cands:
                    fr = getattr(cands[0], "finish_reason", None)
                pf = getattr(resp, "prompt_feedback", None)
                if pf is not None:
                    br = getattr(pf, "block_reason", None)
            except Exception:
                pass
            logger.warning(
                "cover image: model=%s returned no inline image (finish_reason=%s prompt_block=%s). "
                "Check BLOG_IMAGE_MODEL (use a native image model) and API quotas.",
                cfg.blog_image_model,
                fr,
                br,
            )
        return out
    except Exception as e:
        logger.warning("cover image generation failed for model=%s: %s", cfg.blog_image_model, e)
        return None


def _cloudinary_signature(params: dict[str, str], api_secret: str) -> str:
    serialized = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.sha1(f"{serialized}{api_secret}".encode("utf-8")).hexdigest()


def _upload_cover_to_cloudinary(
    cfg: BlogPipelineConfig,
    *,
    slug: str,
    image_bytes: bytes,
    mime_type: str,
    image_prompt: str,
) -> str | None:
    cloud = (cfg.cloudinary_cloud_name or "").strip()
    key = (cfg.cloudinary_api_key or "").strip()
    secret = (cfg.cloudinary_api_secret or "").strip()
    if not cloud or not key or not secret:
        logger.info("cloudinary not configured, skip generated cover upload")
        return None

    ts = str(int(time.time()))
    # Every signed field except api_key, file, signature must be included in the string to sign
    # (sending overwrite/tags without signing caused invalid-signature uploads → no secure_url).
    sign_params = {
        "folder": cfg.cloudinary_folder,
        "public_id": f"{slug}-cover",
        "timestamp": ts,
        "overwrite": "true",
        "tags": "aegis,blog,cover",
    }
    signature = _cloudinary_signature(sign_params, secret)

    url = f"https://api.cloudinary.com/v1_1/{cloud}/image/upload"
    form = {
        "api_key": key,
        "signature": signature,
        **sign_params,
    }
    ext = "png"
    if "/" in mime_type:
        ext = mime_type.split("/", 1)[1].lower()
        if ext == "jpeg":
            ext = "jpg"
    files = {"file": (f"{slug}-cover.{ext}", image_bytes, mime_type)}
    try:
        resp = requests.post(url, data=form, files=files, timeout=60)
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        secure_url = str(payload.get("secure_url") or "").strip()
        return secure_url or None
    except requests.HTTPError as e:
        detail = ""
        if e.response is not None:
            try:
                detail = (e.response.text or "")[:400]
            except Exception:
                pass
        logger.warning("cloudinary upload failed for slug=%s: %s %s", slug, e, detail)
        return None
    except Exception as e:
        logger.warning("cloudinary upload failed for slug=%s: %s", slug, e)
        return None


def _resource_links_from_tags(tags: list[str], source_url: str | None = None) -> list[dict[str, str]]:
    curated: dict[str, tuple[str, str]] = {
        "python": ("Python Docs", "https://docs.python.org/3/"),
        "fastapi": ("FastAPI Docs", "https://fastapi.tiangolo.com/"),
        "ai": ("Google AI Studio Docs", "https://ai.google.dev/gemini-api/docs"),
        "llm": ("LangChain Docs", "https://python.langchain.com/docs/introduction/"),
        "langgraph": ("LangGraph Docs", "https://langchain-ai.github.io/langgraph/"),
        "security": ("OWASP Top 10", "https://owasp.org/www-project-top-ten/"),
        "postgres": ("PostgreSQL Docs", "https://www.postgresql.org/docs/"),
        "supabase": ("Supabase Docs", "https://supabase.com/docs"),
        "docker": ("Docker Docs", "https://docs.docker.com/"),
        "testing": ("pytest Docs", "https://docs.pytest.org/"),
    }
    links: list[dict[str, str]] = []
    if source_url:
        links.append(
            {
                "title": "Inspiration Source",
                "url": source_url,
                "description": "Reference signal that inspired this post topic.",
            }
        )

    seen_urls = {x["url"] for x in links}
    for raw_tag in tags:
        tag = str(raw_tag).strip().lower()
        if not tag or tag not in curated:
            continue
        title, url = curated[tag]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        links.append(
            {
                "title": title,
                "url": url,
                "description": f"Official docs related to {tag}.",
            }
        )
        if len(links) >= 8:
            break
    return links


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
    resource_links = _resource_links_from_tags(tags, source_url=source_url)

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

    raw_slug = _slugify_kebab(str(meta.get("slug") or final_title))
    slug = _unique_slug(client, raw_slug)

    quality_ok, quality_reason, dup_score = _evaluate_quality_gate(
        client,
        title=final_title,
        body_md=body_md,
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

    post_status, published_at = _post_status_after_write()
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
    }
