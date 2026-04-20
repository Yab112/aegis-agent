"""
Generate one blog draft via Gemini + Supabase (GitHub Actions / local).

Requires env:
  GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY

Optional:
  GEMINI_MODEL, OWNER_NAME, PORTFOLIO_URL, BLOG_DEFAULT_OG_IMAGE,
    BLOG_POST_STATUS_AFTER_WRITE (draft | review | published, default draft),
    BLOG_GENERATE_IMAGES, BLOG_IMAGE_MODEL,
    CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET, CLOUDINARY_FOLDER

Usage:
  python scripts/blog_draft.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
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

    max_attempts = 6
    base_sleep = 15

    for attempt in range(1, max_attempts + 1):
        try:
            out = run_blog_draft_once(cfg)
            print(json.dumps(out, indent=2))
            sys.exit(0)

        except Exception as e:
            # Handle Gemini quota/rate-limit specifically
            msg = str(e)
            is_quota = "ResourceExhausted" in msg or "429" in msg or "Quota exceeded" in msg

            if is_quota and attempt < max_attempts:
                sleep_s = base_sleep * attempt  # simple linear backoff
                logger.warning(
                    "Gemini quota exceeded (attempt %s/%s). Sleeping %ss then retrying...",
                    attempt, max_attempts, sleep_s
                )
                time.sleep(sleep_s)
                continue

            logger.exception("blog draft pipeline failed")
            sys.exit(1)


if __name__ == "__main__":
    main()
