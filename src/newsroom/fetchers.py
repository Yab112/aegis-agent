"""Read one source and return its latest items. No database access here."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from time import struct_time
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import requests

logger = logging.getLogger("aegis.newsroom.fetch")

UA = {"User-Agent": "Mozilla/5.0 (compatible; newsroom-collector/1.0)"}
MAX_ITEMS_PER_SOURCE = 40
_TAG = re.compile(r"<[^>]+>")


class SourceUnavailable(RuntimeError):
    """The source answered, but not with something we can read (blocked, rate limited, empty)."""


def clean_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", str(value or "")))).strip()
    return text[:limit]


def _iso(parsed: struct_time | None) -> str | None:
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _get(url: str, timeout: int = 25) -> requests.Response:
    resp = requests.get(url, headers=UA, timeout=timeout)
    if resp.status_code in (401, 403, 429):
        raise SourceUnavailable(f"HTTP {resp.status_code}: the site refused an automated reader")
    resp.raise_for_status()
    return resp


def _item(title: str, url: str, summary: str = "", published_at: str | None = None) -> dict[str, Any]:
    return {"title": clean_text(title, 300), "url": url.split("#")[0], "summary": clean_text(summary, 600), "published_at": published_at}


def fetch_feed(source: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = feedparser.parse(_get(source["feed_url"]).content)
    items = []
    for entry in parsed.entries[:MAX_ITEMS_PER_SOURCE]:
        title, link = entry.get("title"), entry.get("link")
        if not title or not link:
            continue
        items.append(
            _item(title, link, entry.get("summary") or "", _iso(entry.get("published_parsed") or entry.get("updated_parsed")))
        )
    return items


def fetch_page(source: dict[str, Any]) -> list[dict[str, Any]]:
    """No feed: every link on the listing page whose path matches the source's pattern is an article."""
    from bs4 import BeautifulSoup

    pattern = re.compile(source.get("link_pattern") or r"^/[a-z0-9-]+/[a-z0-9-]+")
    soup = BeautifulSoup(_get(source["url"]).text, "html.parser")
    base_host = urlparse(source["url"]).netloc
    seen: dict[str, dict[str, Any]] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if not pattern.search(href):
            continue
        url = urljoin(source["url"], href)
        if urlparse(url).netloc != base_host:
            continue
        heading = a.find(["h1", "h2", "h3", "h4"])
        title = clean_text(heading.get_text(" ") if heading else a.get_text(" "), 300)
        if source["key"] == "github-trending":
            title = href.strip("/").replace("/", " / ")
        # The same article is often linked twice (image + headline); keep the longest text.
        if len(title) >= 8 and len(title) > len(seen.get(url, {}).get("title", "")):
            seen[url] = _item(title, url)
        if len(seen) >= MAX_ITEMS_PER_SOURCE:
            break
    return list(seen.values())


def fetch_hf_papers(source: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for row in _get(source["feed_url"]).json()[:MAX_ITEMS_PER_SOURCE]:
        paper = row.get("paper") or {}
        if paper.get("id") and (row.get("title") or paper.get("title")):
            items.append(
                _item(
                    row.get("title") or paper["title"],
                    f"https://huggingface.co/papers/{paper['id']}",
                    paper.get("summary") or "",
                    row.get("publishedAt") or paper.get("publishedAt"),
                )
            )
    return items


def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Dispatch on `kind`. Raises SourceUnavailable when the source gives nothing readable."""
    kind = source.get("kind")
    if kind == "feed":
        items = fetch_feed(source)
    elif kind == "json":
        items = fetch_hf_papers(source)
    elif kind == "page":
        items = fetch_page(source)
    else:
        return []
    if not items and not source["key"].startswith("arxiv"):  # arXiv publishes nothing at weekends
        raise SourceUnavailable("Read the page but found no articles (layout changed, or rendered by script).")
    return items
