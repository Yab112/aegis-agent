"""What builders are doing with a topic: Hacker News stories, GitHub repositories, Hugging Face models."""
from __future__ import annotations

import logging
import math
import time
from typing import Any

import requests

logger = logging.getLogger("aegis.newsroom.signals")

UA = {"User-Agent": "newsroom-signals/1.0"}
TIMEOUT = 12


def hacker_news(phrase: str, days: int = 7) -> dict[str, Any] | None:
    try:
        data = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": f'"{phrase}"', "tags": "story", "numericFilters": f"created_at_i>{int(time.time()) - days * 86400}", "hitsPerPage": 5},
            timeout=TIMEOUT,
        ).json()
        top = max((int(h.get("points") or 0) for h in data.get("hits", [])), default=0)
        return {"stories": int(data.get("nbHits") or 0), "top_points": top}
    except Exception as exc:
        logger.info("hn unavailable for %r: %s", phrase, exc)
        return None


def github(phrase: str) -> dict[str, Any] | None:
    """Repositories pushed in the last 30 days that mention the phrase. Unauthenticated: 10 calls a minute."""
    try:
        since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": f'"{phrase}" pushed:>{since}', "sort": "stars", "per_page": 1},
            headers=UA,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        top = (data.get("items") or [{}])[0]
        return {"repos": int(data.get("total_count") or 0), "top_repo": top.get("full_name"), "top_stars": top.get("stargazers_count")}
    except Exception as exc:
        logger.info("github unavailable for %r: %s", phrase, exc)
        return None


def hugging_face(phrase: str) -> dict[str, Any] | None:
    try:
        rows = requests.get(
            "https://huggingface.co/api/models", params={"search": phrase, "sort": "downloads", "limit": 20}, timeout=TIMEOUT
        ).json()
        if not isinstance(rows, list):
            return None
        return {"models": len(rows), "top_model": rows[0]["id"] if rows else None, "top_downloads": rows[0].get("downloads") if rows else 0}
    except Exception as exc:
        logger.info("hugging face unavailable for %r: %s", phrase, exc)
        return None


def _log_scale(value: float, full: float) -> float:
    return min(1.0, math.log10(max(value, 0) + 1) / math.log10(full + 1))


def community_signal(phrase: str) -> dict[str, Any]:
    hn, gh, hf = hacker_news(phrase), github(phrase), hugging_face(phrase)
    parts: list[float] = []
    if hn:
        parts.append(max(_log_scale(hn["stories"], 50), _log_scale(hn["top_points"], 500)))
    if gh:
        parts.append(_log_scale(gh["repos"], 2000))
    if hf:
        parts.append(_log_scale(hf["models"], 20))
    return {
        "score": round(sum(parts) / len(parts), 3) if parts else 0.0,
        "hacker_news": hn,
        "github": gh,
        "hugging_face": hf,
        "available": [name for name, v in (("hacker_news", hn), ("github", gh), ("hugging_face", hf)) if v],
    }
