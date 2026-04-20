"""
Fetch developer signals from multiple sources, score with Gemini vs BLOG_FOCUS_TAGS,
then insert blog_ideas with dedup and fallback behavior.
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
DEVTO_ARTICLES = "https://dev.to/api/articles"
REDDIT_TOP = "https://www.reddit.com/r/programming/top.json"
STACKOVERFLOW_QUESTIONS = "https://api.stackexchange.com/2.3/questions"
# GitHub Search requires non-empty `q`; empty env secrets still set the var to "".
DEFAULT_GITHUB_SEARCH_QUERY = "python ai stars:>500 pushed:>2024-01-01"

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


def _devto_fetch_articles(client: httpx.Client, limit: int = 6) -> list[dict[str, Any]]:
    params = {"per_page": str(limit), "top": "7"}
    headers = {"User-Agent": "aegis-agent-blog-ideas"}
    r = client.get(DEVTO_ARTICLES, params=params, headers=headers, timeout=25.0)
    r.raise_for_status()
    items = r.json() or []
    out: list[dict[str, Any]] = []
    for it in items[:limit]:
        aid = str(it.get("id") or "")
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        desc = str(it.get("description") or "").strip()
        url = str(it.get("url") or "").strip()
        tags_text = str(it.get("tag_list") or "").strip()
        out.append(
            {
                "source": "devto",
                "source_id": aid or title[:120],
                "source_url": url,
                "title": title[:500],
                "raw_excerpt": (desc or tags_text or title)[:800],
                "reference_jsonb": {
                    "author": (it.get("user") or {}).get("username"),
                    "tags": tags_text,
                    "positive_reactions_count": it.get("positive_reactions_count"),
                },
            }
        )
    return out


def _reddit_fetch_posts(client: httpx.Client, limit: int = 6) -> list[dict[str, Any]]:
    params = {"t": "week", "limit": str(limit)}
    headers = {
        "Accept": "application/json",
        "User-Agent": "aegis-agent-blog-ideas/1.0",
    }
    r = client.get(REDDIT_TOP, params=params, headers=headers, timeout=25.0)
    if r.status_code in (403, 429):
        logger.warning("Reddit temporarily blocked/rate limited; skip Reddit batch")
        return []
    r.raise_for_status()
    payload = r.json() or {}
    children = (((payload.get("data") or {}).get("children")) or [])
    out: list[dict[str, Any]] = []
    for child in children[:limit]:
        data = child.get("data") or {}
        pid = str(data.get("id") or "")
        title = str(data.get("title") or "").strip()
        if not title:
            continue
        permalink = str(data.get("permalink") or "").strip()
        full_url = f"https://www.reddit.com{permalink}" if permalink else ""
        selftext = str(data.get("selftext") or "").strip()
        out.append(
            {
                "source": "reddit",
                "source_id": pid or title[:120],
                "source_url": full_url,
                "title": title[:500],
                "raw_excerpt": (selftext or title)[:800],
                "reference_jsonb": {
                    "subreddit": data.get("subreddit"),
                    "score": data.get("score"),
                    "num_comments": data.get("num_comments"),
                },
            }
        )
    return out


def _stackoverflow_fetch_questions(client: httpx.Client, limit: int = 6) -> list[dict[str, Any]]:
    params = {
        "order": "desc",
        "sort": "votes",
        "site": "stackoverflow",
        "tagged": "python;fastapi;machine-learning",
        "pagesize": str(limit),
    }
    r = client.get(STACKOVERFLOW_QUESTIONS, params=params, timeout=25.0)
    if r.status_code in (400, 403, 429):
        logger.warning("StackOverflow API unavailable/rate limited; skip SO batch")
        return []
    r.raise_for_status()
    items = (r.json() or {}).get("items") or []
    out: list[dict[str, Any]] = []
    for it in items[:limit]:
        qid = str(it.get("question_id") or "")
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "source": "stackoverflow",
                "source_id": qid or title[:120],
                "source_url": str(it.get("link") or "").strip(),
                "title": title[:500],
                "raw_excerpt": title[:800],
                "reference_jsonb": {
                    "score": it.get("score"),
                    "answer_count": it.get("answer_count"),
                    "tags": it.get("tags") or [],
                },
            }
        )
    return out


def _github_fetch_repos(
    client: httpx.Client,
    query: str,
    limit: int = 6,
    token: str | None = None,
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        logger.warning("GitHub search query empty; skip GitHub batch")
        return []
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aegis-agent-blog-ideas",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"q": q, "sort": "updated", "per_page": str(limit)}
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


def _round_robin_merge(groups: list[list[dict[str, Any]]], max_items: int) -> list[dict[str, Any]]:
    pools = [list(g) for g in groups if g]
    if not pools or max_items <= 0:
        return []
    merged: list[dict[str, Any]] = []
    idx = 0
    while pools and len(merged) < max_items:
        idx = idx % len(pools)
        pool = pools[idx]
        if not pool:
            pools.pop(idx)
            continue
        merged.append(pool.pop(0))
        idx += 1
    return merged


def _collect_multisource_batch(
    http: httpx.Client,
    github_query: str,
    github_token: str | None,
) -> list[dict[str, Any]]:
    hn_limit = int(os.environ.get("BLOG_HN_LIMIT", "12"))
    gh_limit = int(os.environ.get("BLOG_GITHUB_LIMIT", "6"))
    devto_limit = int(os.environ.get("BLOG_DEVTO_LIMIT", "6"))
    reddit_limit = int(os.environ.get("BLOG_REDDIT_LIMIT", "6"))
    so_limit = int(os.environ.get("BLOG_STACKOVERFLOW_LIMIT", "6"))

    collectors: list[tuple[str, Any]] = [
        ("hackernews", lambda: _hn_fetch_stories(http, limit=hn_limit)),
        (
            "github",
            lambda: _github_fetch_repos(
                http,
                query=github_query,
                limit=gh_limit,
                token=github_token,
            ),
        ),
        ("devto", lambda: _devto_fetch_articles(http, limit=devto_limit)),
        ("reddit", lambda: _reddit_fetch_posts(http, limit=reddit_limit)),
        ("stackoverflow", lambda: _stackoverflow_fetch_questions(http, limit=so_limit)),
    ]

    groups: list[list[dict[str, Any]]] = []
    # Round-robin source order across runs so one source cannot dominate by position.
    start_index = int(os.environ.get("BLOG_SOURCE_START_INDEX", "0"))
    start_index = max(0, start_index) % len(collectors)
    rotated = collectors[start_index:] + collectors[:start_index]
    os.environ["BLOG_SOURCE_START_INDEX"] = str((start_index + 1) % len(collectors))

    for source_name, fetcher in rotated:
        try:
            rows = fetcher()
            logger.info("source=%s fetched=%s", source_name, len(rows))
            if rows:
                groups.append(rows)
        except Exception as e:
            # Keep going: one failed source should not break the ingest pipeline.
            logger.warning("source=%s failed: %s", source_name, e)

    max_candidates = int(os.environ.get("BLOG_MAX_CANDIDATES", "40"))
    return _round_robin_merge(groups, max_items=max_candidates)


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

    raw_gh_q = (github_query or os.environ.get("BLOG_GITHUB_SEARCH_QUERY") or "").strip()
    gh_q = raw_gh_q if raw_gh_q else DEFAULT_GITHUB_SEARCH_QUERY
    tok = (github_token or "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    tok = tok or os.environ.get("BLOG_GITHUB_TOKEN", "").strip() or None

    with httpx.Client() as http:
        batch = _collect_multisource_batch(http=http, github_query=gh_q, github_token=tok)

    for raw in batch:
        try:
            scored = score_raw_item(
                model, gen_cfg, owner_name, portfolio_url, raw
            )
        except Exception as e:
            logger.warning(
                "gemini score failed source=%s id=%s err=%s",
                raw.get("source"),
                raw.get("source_id"),
                e,
            )
            # Fallback retry with shorter excerpt before giving up on this signal.
            try:
                raw_retry = dict(raw)
                raw_retry["raw_excerpt"] = str(raw.get("raw_excerpt") or "")[:250]
                scored = score_raw_item(model, gen_cfg, owner_name, portfolio_url, raw_retry)
            except Exception:
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
