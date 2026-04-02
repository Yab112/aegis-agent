"""
Fetch developer signals (Hacker News, GitHub), score with Gemini vs BLOG_FOCUS_TAGS, insert blog_ideas.
"""
from __future__ import annotations

import logging
import os
import warnings
from typing import Any

import httpx
from supabase import Client, create_client

logger = logging.getLogger("aegis.blog_ideas")

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    import google.generativeai as genai

from src.blog.pipeline import (
    _blog_generation_config,
    _extract_json_object,
    normalize_topic_key,
)


HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
GITHUB_SEARCH = "https://api.github.com/search/repositories"

SCORE_PROMPT = """You vet a blog idea for {owner_name}'s technical blog ({portfolio_url}).

Author focus areas (must align; reject obvious mismatches): {focus_tags}

External signal (title + excerpt only; do not copy text verbatim into a blog):
Source: {source}
Title: {title}
Excerpt: {excerpt}
URL: {url}

Return ONLY valid JSON (no markdown fences):
{{
  "topic_key": "stable_snake_case_id_for_dedup",
  "normalized_tags": ["lowercase", "tags"],
  "angle": "one sentence problem/solution angle for an original post",
  "skill_fit": "high" | "medium" | "low",
  "skip": true | false,
  "skip_reason": "null or short reason if skip true"
}}

Rules:
- skill_fit high/medium only if the topic fits the focus areas; else low and skip true.
- topic_key must be unique-ish (e.g. hn_12345 or gh_owner_repo_slug).
- Do not invent statistics or news; angle is for an educational opinion/tutorial style post.
"""


def _focus_tags_str() -> str:
    return os.environ.get(
        "BLOG_FOCUS_TAGS",
        "python,ai,fastapi,fullstack,security,llm,automation,typescript",
    ).strip()


def _hn_fetch_stories(client: httpx.Client, limit: int = 12) -> list[dict[str, Any]]:
    r = client.get(HN_TOP, timeout=30.0)
    r.raise_for_status()
    ids = r.json()[:limit]
    out: list[dict[str, Any]] = []
    for story_id in ids:
        ir = client.get(HN_ITEM.format(id=story_id), timeout=20.0)
        if ir.status_code != 200:
            continue
        data = ir.json()
        if not data or data.get("type") != "story":
            continue
        title = data.get("title") or ""
        url = data.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        # Skip job listings noise
        if "hiring" in title.lower() and "who's" not in title.lower():
            continue
        out.append(
            {
                "source": "hackernews",
                "source_id": str(story_id),
                "source_url": url,
                "title": title[:500],
                "raw_excerpt": (data.get("text") or "")[:800] or title[:400],
                "reference_jsonb": {"hn_id": story_id, "score": data.get("score")},
            }
        )
    return out


def _github_fetch_repos(
    client: httpx.Client,
    query: str,
    limit: int = 6,
    token: str | None = None,
) -> list[dict[str, Any]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aegis-agent-blog-ideas",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"q": query, "sort": "updated", "per_page": str(limit)}
    r = client.get(GITHUB_SEARCH, params=params, headers=headers, timeout=30.0)
    if r.status_code == 403:
        logger.warning("GitHub search rate limited or forbidden; skip GitHub batch")
        return []
    r.raise_for_status()
    items = r.json().get("items") or []
    out: list[dict[str, Any]] = []
    for it in items:
        full = it.get("full_name") or ""
        sid = str(it.get("id") or full.replace("/", "_"))
        desc = (it.get("description") or "")[:500]
        out.append(
            {
                "source": "github",
                "source_id": sid,
                "source_url": it.get("html_url") or "",
                "title": (it.get("name") or full)[:200] + (f" — {full}" if full else ""),
                "raw_excerpt": desc,
                "reference_jsonb": {
                    "full_name": full,
                    "stars": it.get("stargazers_count"),
                    "language": it.get("language"),
                },
            }
        )
    return out


def _topic_blocked_for_ingest(sb: Client, topic_key: str) -> bool:
    p = (
        sb.table("blog_posts")
        .select("id")
        .eq("topic_key", topic_key)
        .limit(1)
        .execute()
    )
    if p.data:
        return True
    i = (
        sb.table("blog_ideas")
        .select("id")
        .eq("topic_key", topic_key)
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    return bool(i.data)


def _insert_idea(sb: Client, raw: dict[str, Any], scored: dict[str, Any]) -> bool:
    topic_key = normalize_topic_key(str(scored.get("topic_key") or raw["title"]))
    if not topic_key or topic_key == "topic" or _topic_blocked_for_ingest(sb, topic_key):
        logger.info("skip ingest duplicate topic_key=%s", topic_key)
        return False
    tags = scored.get("normalized_tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip().lower() for t in tags if str(t).strip()][:16]
    row = {
        "source": raw["source"],
        "source_url": raw.get("source_url"),
        "source_id": raw.get("source_id"),
        "title": raw["title"][:500],
        "raw_excerpt": (raw.get("raw_excerpt") or "")[:2000],
        "reference_jsonb": raw.get("reference_jsonb") or {},
        "normalized_tags": tags,
        "topic_key": topic_key,
        "angle": str(scored.get("angle", ""))[:2000],
        "skill_fit": str(scored.get("skill_fit", "medium")).lower(),
        "status": "pending",
    }
    if row["skill_fit"] not in ("high", "medium", "low"):
        row["skill_fit"] = "medium"
    try:
        sb.table("blog_ideas").insert(row).execute()
        logger.info("inserted blog_idea topic_key=%s source=%s", topic_key, raw["source"])
        return True
    except Exception as e:
        logger.warning("insert blog_idea failed: %s", e)
        return False


def score_raw_item(
    model: Any,
    gen_cfg: Any,
    owner_name: str,
    portfolio_url: str,
    raw: dict[str, Any],
) -> dict[str, Any] | None:
    text = model.generate_content(
        SCORE_PROMPT.format(
            owner_name=owner_name,
            portfolio_url=portfolio_url,
            focus_tags=_focus_tags_str(),
            source=raw["source"],
            title=raw["title"],
            excerpt=(raw.get("raw_excerpt") or "")[:1200],
            url=raw.get("source_url") or "",
        ),
        generation_config=gen_cfg,
    ).text
    return _extract_json_object(text or "")


def run_idea_ingest(
    gemini_api_key: str,
    gemini_model: str,
    supabase_url: str,
    supabase_service_key: str,
    owner_name: str,
    portfolio_url: str,
    github_token: str | None = None,
    github_query: str | None = None,
) -> dict[str, Any]:
    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel(gemini_model)
    gen_cfg = _blog_generation_config()
    sb = create_client(supabase_url, supabase_service_key)

    inserted = 0
    skipped = 0
    errors = 0

    gh_q = github_query or os.environ.get(
        "BLOG_GITHUB_SEARCH_QUERY", "python ai stars:>500 pushed:>2024-01-01"
    )
    tok = (github_token or "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    tok = tok or os.environ.get("BLOG_GITHUB_TOKEN", "").strip() or None

    with httpx.Client() as http:
        batch = _hn_fetch_stories(http, limit=int(os.environ.get("BLOG_HN_LIMIT", "12")))
        batch.extend(
            _github_fetch_repos(
                http,
                query=gh_q,
                limit=int(os.environ.get("BLOG_GITHUB_LIMIT", "6")),
                token=tok,
            )
        )

    for raw in batch:
        try:
            scored = score_raw_item(
                model, gen_cfg, owner_name, portfolio_url, raw
            )
        except Exception as e:
            logger.exception("gemini score failed for %s: %s", raw.get("source_id"), e)
            errors += 1
            continue
        if scored.get("skip") is True or str(scored.get("skip", "")).lower() in (
            "true",
            "1",
            "yes",
        ):
            skipped += 1
            logger.info(
                "skipped signal %s: %s",
                raw.get("source_id"),
                scored.get("skip_reason"),
            )
            continue
        fit = str(scored.get("skill_fit", "")).lower()
        if fit == "low":
            skipped += 1
            continue
        if _insert_idea(sb, raw, scored):
            inserted += 1

    return {
        "candidates": len(batch),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    }
