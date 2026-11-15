"""
Trusted sources for the AI news desk (seed list; the live list is the `news_sources` table).

kind:
  feed       RSS / Atom, read with feedparser
  page       no feed: scan the listing page for article links matching `link_pattern`
  json       a JSON endpoint (handled by name in fetchers.py)
  reference  a leaderboard or data site: never collected as news, offered to the writer as a data source
tier: 1 lab primary source · 2 newsroom · 3 papers / code / benchmarks · 4 data and evaluation companies

Feed availability was checked on 2026-11-15. `blocked` records sources that refused automated
readers that day; they stay in the list, disabled, so the admin shows why they are empty.
"""
from __future__ import annotations

from typing import Any

GROUPS = {1: "Lab newsrooms and research", 2: "Tech and AI newsrooms", 3: "Papers, code and benchmarks", 4: "Data and evaluation companies"}


def _s(key: str, name: str, url: str, tier: int, kind: str = "feed", feed: str | None = None, pattern: str | None = None, blocked: str | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "url": url,
        "feed_url": feed,
        "kind": kind,
        "link_pattern": pattern,
        "tier": tier,
        "grp": GROUPS[tier],
        "enabled": blocked is None and kind != "reference",
        "last_error": blocked,
    }


SEED_SOURCES: list[dict[str, Any]] = [
    # ── Tier 1: frontier labs, open-source and infrastructure ────────────────
    _s("openai-news", "OpenAI News", "https://openai.com/news/", 1, feed="https://openai.com/news/rss.xml"),
    _s("anthropic-news", "Anthropic Newsroom", "https://www.anthropic.com/news", 1, "page", pattern=r"^/news/[a-z0-9-]+$"),
    _s("anthropic-research", "Anthropic Research", "https://www.anthropic.com/research", 1, "page", pattern=r"^/research/[a-z0-9-]+$"),
    _s("anthropic-alignment", "Anthropic Alignment Science", "https://alignment.anthropic.com/", 1, "page", pattern=r"^/?20\d\d/[a-z0-9-]+/?"),
    _s("deepmind-blog", "Google DeepMind Blog", "https://deepmind.google/discover/blog/", 1, feed="https://deepmind.google/blog/rss.xml"),
    _s("google-research", "Google Research Blog", "https://research.google/blog/", 1, feed="https://research.google/blog/rss/"),
    _s("meta-ai", "Meta AI Blog", "https://ai.meta.com/blog/", 1, "page", pattern=r"^(https://ai\.meta\.com)?/blog/[a-z0-9-]+/?$"),
    _s("xai-news", "xAI News", "https://x.ai/news", 1, "page", pattern=r"^/news/[a-z0-9-]+$"),
    _s("mistral-news", "Mistral AI News", "https://mistral.ai/news/", 1, "page", pattern=r"^/news/[a-z0-9-]+/?$"),
    _s("deepseek-news", "DeepSeek API News", "https://api-docs.deepseek.com/news", 1, "page", pattern=r"^/news/news\d+"),
    _s("qwen-blog", "Qwen Blog (Alibaba)", "https://qwenlm.github.io/blog/", 1, feed="https://qwenlm.github.io/blog/index.xml"),
    _s("hf-blog", "Hugging Face Blog", "https://huggingface.co/blog", 1, feed="https://huggingface.co/blog/feed.xml"),
    _s("nvidia-blog", "NVIDIA Technical Blog", "https://developer.nvidia.com/blog/", 1, feed="https://developer.nvidia.com/blog/feed/"),
    _s("msr-blog", "Microsoft Research Blog", "https://www.microsoft.com/en-us/research/blog/", 1, feed="https://www.microsoft.com/en-us/research/feed/"),
    _s("aws-ml", "AWS Machine Learning Blog", "https://aws.amazon.com/blogs/machine-learning/", 1, feed="https://aws.amazon.com/blogs/machine-learning/feed/"),
    # ── Tier 2: newsrooms ─────────────────────────────────────────────────────
    _s("verge-ai", "The Verge, AI", "https://www.theverge.com/ai-artificial-intelligence", 2, feed="https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    _s("techcrunch-ai", "TechCrunch, AI", "https://techcrunch.com/category/artificial-intelligence/", 2, feed="https://techcrunch.com/category/artificial-intelligence/feed/"),
    _s("ars-ai", "Ars Technica, AI", "https://arstechnica.com/ai/", 2, feed="https://arstechnica.com/ai/feed/"),
    _s("venturebeat-ai", "VentureBeat, AI", "https://venturebeat.com/category/ai/", 2, feed="https://venturebeat.com/category/ai/feed/"),
    _s("mittr-ai", "MIT Technology Review, AI", "https://www.technologyreview.com/topic/artificial-intelligence/", 2, feed="https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    _s("reuters-ai", "Reuters, Artificial Intelligence", "https://www.reuters.com/technology/artificial-intelligence/", 2, "page", blocked="Refuses automated readers (HTTP 401)."),
    _s("the-decoder", "The Decoder", "https://the-decoder.com/", 2, feed="https://the-decoder.com/feed/"),
    # ── Tier 3: papers, code, benchmarks ──────────────────────────────────────
    _s("arxiv-cs-cl", "arXiv cs.CL", "https://arxiv.org/list/cs.CL/new", 3, feed="https://rss.arxiv.org/rss/cs.CL"),
    _s("arxiv-cs-lg", "arXiv cs.LG", "https://arxiv.org/list/cs.LG/new", 3, feed="https://rss.arxiv.org/rss/cs.LG"),
    _s("hf-papers", "Hugging Face Daily Papers", "https://huggingface.co/papers", 3, "json", feed="https://huggingface.co/api/daily_papers"),
    _s("github-trending", "GitHub Trending", "https://github.com/trending", 3, "page", pattern=r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
    _s("artificial-analysis", "Artificial Analysis", "https://artificialanalysis.ai/", 3, "reference"),
    _s("lmarena", "LMArena", "https://lmarena.ai/", 3, "reference"),
    _s("swe-bench", "SWE-bench", "https://www.swebench.com/", 3, "reference"),
    # ── Tier 4: data and evaluation companies ─────────────────────────────────
    _s("scale-blog", "Scale AI Blog", "https://scale.com/blog", 4, "page", pattern=r"^/blog/[a-z0-9-]+$"),
    _s("scale-research", "Scale SEAL Research", "https://scale.com/research", 4, "page", blocked="Refuses automated readers (HTTP 403)."),
    _s("surge-blog", "Surge AI Blog", "https://www.surgehq.ai/blog", 4, "page", pattern=r"^/blog/[a-z0-9-]+$"),
    _s("snorkel-blog", "Snorkel AI Blog", "https://snorkel.ai/blog/", 4, feed="https://snorkel.ai/feed/"),
]

# Sources whose items are too many and too raw to count as "coverage" on their own.
FIREHOSE = {"arxiv-cs-cl", "arxiv-cs-lg", "github-trending"}


def trusted_domains() -> set[str]:
    from urllib.parse import urlparse

    return {urlparse(s["url"]).netloc.removeprefix("www.") for s in SEED_SOURCES}
