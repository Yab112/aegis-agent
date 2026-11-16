"""
Posts on X that a story can quote.

Only posts linked from a source the writer already read are offered, and each one is
fetched through X's public embed endpoint, so the quoted words, author and date are
the real ones. The site shows them as a quote card with a link to the post.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any

import requests

logger = logging.getLogger("aegis.blog.social")

_STATUS_URL = re.compile(r"https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{5,25})")
_TAG = re.compile(r"<[^>]+>")
OEMBED = "https://publish.twitter.com/oembed"


def canonical(handle: str, status_id: str) -> str:
    return f"https://x.com/{handle}/status/{status_id}"


def canonical_post_url(url: str) -> str | None:
    """twitter.com or x.com, with or without tracking: the same post gets the same URL."""
    m = _STATUS_URL.search(url or "")
    return canonical(*m.groups()) if m else None


def extract_post_urls(page_html: str, limit: int = 8) -> list[str]:
    """Distinct X post links in a page, in the order they appear."""
    seen: list[str] = []
    for handle, status_id in _STATUS_URL.findall(page_html or ""):
        url = canonical(handle, status_id)
        if handle.lower() not in ("intent", "share", "i") and url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


def fetch_x_post(url: str, timeout: int = 12) -> dict[str, Any] | None:
    """{url, author, handle, text, date} for a public post, or None if it cannot be read."""
    m = _STATUS_URL.search(url or "")
    if not m:
        return None
    handle, status_id = m.groups()
    try:
        r = requests.get(
            OEMBED,
            params={"url": f"https://twitter.com/{handle}/status/{status_id}", "omit_script": "1", "dnt": "true"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; blog-pipeline)"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        logger.info("x post unavailable %s: %s", url, exc)
        return None
    block = str(data.get("html") or "")
    paragraph = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
    text = html.unescape(_TAG.sub("", re.sub(r"<br\s*/?>", "\n", paragraph.group(1)))) if paragraph else ""
    text = re.sub(r"\s*pic\.twitter\.com/\w+\s*$", "", text).strip()
    dates = re.findall(r">([A-Z][a-z]+ \d{1,2}, \d{4})</a>", block)
    if not text:
        return None
    return {
        "url": canonical(handle, status_id),
        "author": str(data.get("author_name") or handle)[:80],
        "handle": handle,
        "text": text[:1200],
        "date": dates[-1] if dates else "",
    }


def collect_x_posts(pages_html: list[str], limit: int = 5) -> list[dict[str, Any]]:
    """Resolve the X posts linked from the source pages. Never raises."""
    posts: list[dict[str, Any]] = []
    for page in pages_html:
        for url in extract_post_urls(page):
            if len(posts) >= limit:
                return posts
            if any(p["url"] == url for p in posts):
                continue
            post = fetch_x_post(url)
            if post:
                posts.append(post)
    return posts
