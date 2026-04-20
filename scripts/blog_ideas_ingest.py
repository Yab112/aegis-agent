"""
Ingest blog ideas from multiple public sources (HN, GitHub, Dev.to, Reddit,
Stack Overflow), score with Gemini, insert blog_ideas.

Env (required): GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY
Optional: GEMINI_MODEL, OWNER_NAME, PORTFOLIO_URL, BLOG_FOCUS_TAGS,
           BLOG_GITHUB_SEARCH_QUERY, BLOG_GITHUB_TOKEN, BLOG_HN_LIMIT,
           BLOG_GITHUB_LIMIT, BLOG_DEVTO_LIMIT, BLOG_REDDIT_LIMIT,
           BLOG_STACKOVERFLOW_LIMIT, BLOG_MAX_CANDIDATES

Usage:
  python scripts/blog_ideas_ingest.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("blog_ideas_ingest")


def main() -> None:
    from src.blog.idea_ingest import run_idea_ingest

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    url = os.environ.get("SUPABASE_URL", "").strip()
    svc = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not key or not url or not svc:
        logger.error("Set GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY")
        sys.exit(1)

    model = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
    owner = os.environ.get("OWNER_NAME", "Yabibal Eshetie").strip()
    portfolio = os.environ.get("PORTFOLIO_URL", "https://yabibal.site").strip()
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("BLOG_GITHUB_TOKEN")
    if gh_token:
        gh_token = gh_token.strip()

    try:
        out = run_idea_ingest(
            gemini_api_key=key,
            gemini_model=model,
            supabase_url=url,
            supabase_service_key=svc,
            owner_name=owner,
            portfolio_url=portfolio,
            github_token=gh_token or None,
        )
        print(json.dumps(out, indent=2))
        sys.exit(0)
    except Exception:
        logger.exception("blog_ideas_ingest failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
