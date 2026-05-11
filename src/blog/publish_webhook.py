from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger("aegis.blog.webhook")


def _webhook_settings() -> tuple[str | None, str | None, int, int, int]:
    url = (os.getenv("BLOG_PUBLISH_WEBHOOK_URL") or "").strip() or None
    secret = (os.getenv("BLOG_PUBLISH_WEBHOOK_SECRET") or "").strip() or None
    timeout = int(os.getenv("BLOG_PUBLISH_WEBHOOK_TIMEOUT_SECONDS", "10"))
    attempts = int(os.getenv("BLOG_PUBLISH_WEBHOOK_MAX_ATTEMPTS", "3"))
    backoff = int(os.getenv("BLOG_PUBLISH_WEBHOOK_BACKOFF_SECONDS", "2"))
    timeout = max(3, min(timeout, 60))
    attempts = max(1, min(attempts, 6))
    backoff = max(1, min(backoff, 15))
    return url, secret, timeout, attempts, backoff


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(timestamp.encode("utf-8"))
    mac.update(b".")
    mac.update(body)
    return mac.hexdigest()


def notify_blog_post_published(
    *,
    post: dict[str, Any],
    source: str,
    pipeline_run_key: str | None = None,
) -> bool:
    """
    Send an optional webhook event when a post becomes published.

    Returns True when delivered or skipped (no URL configured), False on repeated failure.
    """
    url, secret, timeout, attempts, backoff = _webhook_settings()
    if not url:
        return True

    published_at = str(post.get("published_at") or datetime.now(timezone.utc).isoformat())
    payload = {
        "event": "blog_post_published",
        "source": source,
        "pipeline_run_key": pipeline_run_key,
        "post": {
            "id": post.get("id"),
            "slug": post.get("slug"),
            "title": post.get("title"),
            "topic_key": post.get("topic_key"),
            "published_at": published_at,
            "tags": post.get("tags") or [],
            "image_url": post.get("image_url") or post.get("og_image_url"),
        },
    }

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "aegis-blog-pipeline/1.0",
        "X-Aegis-Event": "blog_post_published",
        "X-Aegis-Timestamp": timestamp,
    }
    if secret:
        headers["X-Aegis-Signature"] = _signature(secret, timestamp, body)

    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=timeout)
            if 200 <= resp.status_code < 300:
                logger.info("publish webhook delivered slug=%s", post.get("slug"))
                return True
            logger.warning(
                "publish webhook failed slug=%s attempt=%s/%s status=%s",
                post.get("slug"),
                attempt,
                attempts,
                resp.status_code,
            )
        except Exception as e:
            logger.warning(
                "publish webhook error slug=%s attempt=%s/%s err=%s",
                post.get("slug"),
                attempt,
                attempts,
                e,
            )
        if attempt < attempts:
            time.sleep(backoff * attempt)

    return False
