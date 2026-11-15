"""
Find trusted articles for a topic in our own library (`news_items`).

Used when a post is written from a free-text topic: Google Search grounding is often refused
on a free Gemini quota, and without this the writer would have nothing to cite.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from src.newsroom.phrases import GENERIC, STOP, VERBS

logger = logging.getLogger("aegis.newsroom.library")


def topic_words(text: str, limit: int = 8) -> list[str]:
    """The words of a topic worth searching headlines for, longest first."""
    words = []
    for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", text or ""):
        low = w.lower().strip(".-")
        if len(low) >= 3 and low not in STOP and low not in GENERIC and low not in VERBS and low not in words:
            words.append(low)
    return sorted(words, key=len, reverse=True)[:limit]


def rank_items(items: list[dict[str, Any]], words: list[str], limit: int = 8) -> list[dict[str, Any]]:
    """Keep headlines sharing at least two topic words (one, if it carries a version number); labs first."""
    scored = []
    for item in items:
        title = " " + str(item.get("title") or "").lower() + " "
        hits = [w for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", title)]
        strong = any(any(c.isdigit() for c in w) for w in hits)
        if len(hits) >= 2 or strong:
            scored.append((len(hits), -int(item.get("tier") or 2), str(item.get("published_at") or ""), item))
    scored.sort(key=lambda t: t[:3], reverse=True)
    out, seen = [], set()
    for *_rest, item in scored:
        if item["url"] not in seen:
            seen.add(item["url"])
            out.append({"title": item["title"], "url": item["url"], "source": item.get("source_key"), "tier": item.get("tier")})
    return out[:limit]


def find_sources_for_topic(client: Any, text: str, limit: int = 8) -> list[dict[str, Any]]:
    """Never raises: an empty list means the library has nothing on this topic (or is not set up)."""
    words = topic_words(text)
    if not words:
        return []
    try:
        clause = ",".join(f"title.ilike.%{w}%" for w in words)
        rows = (
            client.table("news_items")
            .select("title,url,source_key,tier,published_at")
            .or_(clause)
            .order("published_at", desc=True)
            .limit(150)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        logger.info("library lookup unavailable: %s", str(exc)[:120])
        return []
    return rank_items(rows, words, limit)
