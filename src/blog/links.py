"""Source links shown under a post, and the figure resolver that finds licensed images."""
from __future__ import annotations

import re
from typing import Any, Callable

from src.blog.prompts import FIGURE_PICK_PROMPT
from src.blog.research import ResearchPack
from src.blog.rich_content import search_commons_images


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


def _resource_links_for_post(pack: ResearchPack, tags: list[str]) -> list[dict[str, str]]:
    """Sources the post was written from first, then official docs for its tags."""
    links = [s.as_resource_link() for s in pack.sources]
    seen = {x["url"] for x in links}
    for extra in _resource_links_from_tags(tags):
        if extra["url"] not in seen and len(links) < 14:
            links.append(extra)
    return links


def _make_figure_resolver(model: Any, gen_cfg: Any) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """Find a licensed image on Wikimedia Commons and let the model reject poor matches."""

    def resolve(raw: dict[str, Any]) -> dict[str, Any] | None:
        query = str(raw.get("query") or "").strip()
        if not query:
            return None
        candidates = search_commons_images(query)
        if not candidates:
            return None
        listing = "\n".join(
            f"{i}. {c['title']} — {c['description'] or 'no description'}" for i, c in enumerate(candidates, 1)
        )
        answer = model.generate_content(
            FIGURE_PICK_PROMPT.format(
                caption=str(raw.get("caption") or "")[:400], query=query[:200], candidates=listing
            ),
            generation_config=gen_cfg,
        ).text
        m = re.search(r"\d+", answer or "")
        choice = int(m.group(0)) if m else 0
        if not 1 <= choice <= len(candidates):
            return None
        return candidates[choice - 1]

    return resolve
