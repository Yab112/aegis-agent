"""
Research step for the blog writer: build a pack of real, fetched sources.

The writer may only cite what is in this pack, which is what lets a post carry
numbered footnotes, sourced charts and a "Sources" list without inventing URLs.

Two inputs, both optional and both failure-tolerant:
  1. the page that inspired the idea (``blog_ideas.source_url``), fetched and
     reduced to text;
  2. Gemini with Google Search grounding, called over REST, which returns the
     pages it actually read alongside its notes.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

from src.blog.rich_content import HTTP_UA, normalize_url

logger = logging.getLogger("aegis.blog.research")

GEMINI_REST = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RESEARCH_PROMPT = """You are the researcher for a technical blog post.

Working title: {title}
Angle: {angle}

Search the web and collect what a careful writer needs:
- concrete facts, numbers and dates, each with where it comes from
- data that could be plotted (a small series or comparison), with its exact values and units
- primary sources first: official docs, papers, engineering blogs, benchmarks, standards
- points where sources disagree, or where the evidence is thin

Write plain research notes, grouped by theme. Quote numbers exactly. Do not write the article.
If you cannot find evidence for something, say so instead of guessing.
"""


@dataclass
class Source:
    title: str
    url: str
    excerpt: str = ""

    def as_resource_link(self) -> dict[str, str]:
        return {"title": self.title[:160], "url": self.url, "description": self.excerpt[:220]}


@dataclass
class ResearchPack:
    notes: str = ""
    sources: list[Source] = field(default_factory=list)
    grounded: bool = False
    x_posts: list[dict[str, Any]] = field(default_factory=list)  # posts on X linked from the sources

    def urls(self) -> list[str]:
        return [s.url for s in self.sources]

    def add(self, source: Source) -> None:
        key = normalize_url(source.url)
        if key and all(normalize_url(s.url) != key for s in self.sources):
            self.sources.append(source)

    def prompt_block(self) -> str:
        if not self.sources and not self.notes:
            return "(no sources were retrieved)"
        lines: list[str] = []
        for i, s in enumerate(self.sources, 1):
            lines.append(f"[S{i}] {s.title}\n     {s.url}")
            if s.excerpt:
                lines.append(f"     excerpt: {s.excerpt[:1500]}")
        if self.x_posts:
            lines.append("\nPOSTS ON X linked from the sources (the only ones you may quote):")
            for i, post in enumerate(self.x_posts, 1):
                lines.append(f"[X{i}] {post['author']} (@{post['handle']}), {post['date']}: {post['text'][:400]}\n     {post['url']}")
        if self.notes:
            lines.append("\nRESEARCH NOTES (from the sources above):\n" + self.notes[:12000])
        return "\n".join(lines)


def is_public_http_url(url: str) -> bool:
    """Only fetch public http(s) hosts — never loopback, link-local or private ranges."""
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


_PAGE_HTML: dict[str, str] = {}  # url -> raw html of pages read in this run (for links to posts on X)


def fetch_page_text(url: str, *, timeout: int = 15, max_chars: int = 6000) -> tuple[str, str] | None:
    """Return (title, readable text) for a public page, or None."""
    if not is_public_http_url(url):
        return None
    try:
        resp = requests.get(url, headers={"User-Agent": HTTP_UA}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.info("research: could not fetch %s (%s)", url, exc)
        return None
    if "html" not in resp.headers.get("content-type", "html"):
        return None
    try:
        from bs4 import BeautifulSoup

        _PAGE_HTML[url] = resp.text[:800_000]
        soup = BeautifulSoup(resp.text[:800_000], "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
            tag.decompose()
        title = (soup.title.string if soup.title and soup.title.string else "").strip()
        root = soup.find("article") or soup.find("main") or soup.body or soup
        text = re.sub(r"\s+", " ", root.get_text(" ")).strip()
    except Exception as exc:
        logger.info("research: could not parse %s (%s)", url, exc)
        return None
    if len(text) < 200:
        return None
    return title[:200] or urlparse(url).netloc, text[:max_chars]


def _resolve_redirect(url: str, timeout: int = 8) -> str | None:
    """Grounding returns short-lived redirect links; store the page they point to."""
    if "grounding-api-redirect" not in url:
        return url
    try:
        r = requests.get(url, headers={"User-Agent": HTTP_UA}, timeout=timeout, stream=True, allow_redirects=True)
        final = r.url
        r.close()
        return final if final and "grounding-api-redirect" not in final else None
    except requests.RequestException:
        return None


def grounded_research(
    *, api_key: str, model: str, title: str, angle: str, timeout: int = 90, max_sources: int = 10
) -> tuple[str, list[Source]]:
    """Ask Gemini to research with Google Search; return (notes, pages it read)."""
    resp = requests.post(
        GEMINI_REST.format(model=model.removeprefix("models/")),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "contents": [{"role": "user", "parts": [{"text": RESEARCH_PROMPT.format(title=title, angle=angle)}]}],
            "tools": [{"google_search": {}}],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    cand = ((resp.json() or {}).get("candidates") or [{}])[0]
    notes = "".join(str(p.get("text") or "") for p in ((cand.get("content") or {}).get("parts") or []))
    chunks: list[dict[str, Any]] = (cand.get("groundingMetadata") or {}).get("groundingChunks") or []
    sources: list[Source] = []
    seen: set[str] = set()
    for chunk in chunks:
        web = chunk.get("web") or {}
        raw_url = str(web.get("uri") or "").strip()
        if not raw_url:
            continue
        url = _resolve_redirect(raw_url)
        if not url or not url.startswith("http"):
            continue
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        sources.append(Source(title=str(web.get("title") or urlparse(url).netloc)[:200], url=url))
        if len(sources) >= max_sources:
            break
    return notes.strip(), sources


def build_research_pack(
    *,
    api_key: str,
    model: str,
    title: str,
    angle: str,
    source_url: str | None = None,
    extra_sources: list[dict[str, Any]] | None = None,
) -> ResearchPack:
    """Never raises: a post can still be written from a thin pack, it just cites less."""
    pack = ResearchPack()
    # Pages the keyword desk collected from trusted sources: read the first few in full.
    for i, src in enumerate(extra_sources or []):
        url, src_title = str(src.get("url") or ""), str(src.get("title") or "")
        if not url.startswith("http") or url == source_url:
            continue
        got = fetch_page_text(url, max_chars=4000) if i < 6 else None
        pack.add(Source(title=(got[0] if got else src_title) or url, url=url, excerpt=got[1] if got else ""))
    if source_url:
        got = fetch_page_text(source_url)
        if got:
            pack.add(Source(title=got[0], url=source_url, excerpt=got[1]))
        else:
            pack.add(Source(title="Inspiration source", url=source_url))

    enabled = os.environ.get("BLOG_RESEARCH_GROUNDING", "true").strip().lower() not in ("0", "false", "no", "off")
    if enabled:
        research_model = os.environ.get("BLOG_RESEARCH_MODEL", "").strip() or model
        try:
            notes, sources = grounded_research(api_key=api_key, model=research_model, title=title, angle=angle)
            pack.notes = notes
            pack.grounded = bool(sources)
            for s in sources:
                pack.add(s)
        except Exception as exc:
            logger.warning("research: grounded search unavailable (%s); writing from the source page only", exc)
    try:
        from src.blog.social import collect_x_posts

        pack.x_posts = collect_x_posts([_PAGE_HTML[u] for u in pack.urls() if u in _PAGE_HTML])
    except Exception as exc:
        logger.info("research: posts on X not collected (%s)", exc)
    _PAGE_HTML.clear()
    logger.info(
        "research: %s source(s), %s post(s) on X, grounded=%s, notes=%s chars",
        len(pack.sources), len(pack.x_posts), pack.grounded, len(pack.notes),
    )
    return pack
