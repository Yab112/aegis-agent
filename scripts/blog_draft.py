"""
Generate one blog draft via Gemini + Supabase (GitHub Actions / local).

Requires env:
  GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY

Optional:
  GEMINI_MODEL, OWNER_NAME, PORTFOLIO_URL, BLOG_DEFAULT_OG_IMAGE,
  BLOG_POST_STATUS_AFTER_WRITE (draft | review | published, default draft)

Usage:
  python scripts/blog_draft.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("blog_draft")


def main() -> None:
    from src.blog.pipeline import BlogPipelineConfig, run_blog_draft_once

    try:
        cfg = BlogPipelineConfig.from_minimal_env()
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)

    try:
        out = run_blog_draft_once(cfg)
        print(json.dumps(out, indent=2))
        sys.exit(0)
    except Exception:
        logger.exception("blog draft pipeline failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
