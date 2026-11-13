"""
Promote blog_posts from review → published (never touches draft).

In production this runs inside the API process on a schedule
(``src/scheduler``); this script is the manual / local equivalent.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY

Usage:
  python scripts/blog_publish_reviewed.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("blog_publish_reviewed")


def main() -> None:
    from src.blog.publish_reviewed import run_publish_reviewed

    url = os.environ.get("SUPABASE_URL", "").strip()
    svc = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not svc:
        logger.error("Set SUPABASE_URL and SUPABASE_SERVICE_KEY")
        sys.exit(1)

    print(json.dumps(run_publish_reviewed(url, svc), indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
