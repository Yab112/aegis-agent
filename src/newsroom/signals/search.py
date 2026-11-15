"""What people search for: Google suggestions, Google Trends direction, Wikipedia page views."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger("aegis.newsroom.signals")

# A Wikipedia match only counts when the article is about technology: "Gemini 4" is also a NASA mission.
_TECH = ("artificial intelligence", "machine learning", "language model", "neural", "software", "chatbot", "openai",
         "google", "anthropic", "nvidia", "computer", "algorithm", "semiconductor", "startup", "technology company", "generative")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 12


def autocomplete(phrase: str) -> dict[str, Any] | None:
    """Phrases people actually type that start with ours. More completions means a more searched term."""
    try:
        r = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "q": phrase},
            headers=UA,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        suggestions = [s for s in r.json()[1] if isinstance(s, str)][:10]
    except Exception as exc:
        logger.info("autocomplete unavailable for %r: %s", phrase, exc)
        return None
    related = [s for s in suggestions if s.lower() != phrase.lower()]
    return {"suggestions": related, "count": len(suggestions)}


def trends(phrase: str) -> dict[str, Any] | None:
    """Google Trends interest over 3 months (0-100, relative). Unofficial endpoint: may be rate limited."""
    try:
        s = requests.Session()
        s.headers.update(UA)
        s.get("https://trends.google.com/trends/?geo=US", timeout=TIMEOUT)
        req = {"comparisonItem": [{"keyword": phrase, "geo": "", "time": "today 3-m"}], "category": 0, "property": ""}
        r = s.get("https://trends.google.com/trends/api/explore", params={"hl": "en-US", "tz": "0", "req": json.dumps(req)}, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        widget = json.loads(r.text[5:])["widgets"][0]
        r2 = s.get(
            "https://trends.google.com/trends/api/widgetdata/multiline",
            params={"hl": "en-US", "tz": "0", "req": json.dumps(widget["request"]), "token": widget["token"]},
            timeout=TIMEOUT,
        )
        if r2.status_code != 200:
            return None
        values = [int(p["value"][0]) for p in json.loads(r2.text[6:])["default"]["timelineData"]]
    except Exception as exc:
        logger.info("trends unavailable for %r: %s", phrase, exc)
        return None
    if len(values) < 14:
        return None
    recent = sum(values[-7:]) / 7
    before = sum(values[-28:-7]) / max(1, len(values[-28:-7]))
    return {"recent": round(recent, 1), "before": round(before, 1), "change": round((recent - before) / max(before, 1), 2), "series": values[-30:]}


def wikipedia(phrase: str) -> dict[str, Any] | None:
    """Daily views of the closest Wikipedia article: an absolute number, unlike Trends."""
    try:
        found = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": phrase, "srlimit": 3, "format": "json"},
            headers=UA,
            timeout=TIMEOUT,
        ).json()["query"]["search"]
        # Only trust a match whose title shares the phrase's words and whose text is about technology.
        words = [w for w in phrase.lower().split() if len(w) > 2]
        title = next(
            (
                hit["title"]
                for hit in found
                if words
                and sum(w in hit["title"].lower() for w in words) >= max(1, len(words) // 2)
                and any(t in (hit.get("snippet", "") + " " + hit["title"]).lower() for t in _TECH)
            ),
            None,
        )
        if not title:
            return None
        end = datetime.now(timezone.utc) - timedelta(days=1)
        start = end - timedelta(days=13)
        items = requests.get(
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
            f"{requests.utils.quote(title.replace(' ', '_'), safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}",
            headers=UA,
            timeout=TIMEOUT,
        ).json()["items"]
        views = [int(i["views"]) for i in items]
    except Exception as exc:
        logger.info("wikipedia unavailable for %r: %s", phrase, exc)
        return None
    if not views:
        return None
    return {"article": title, "daily_views": round(sum(views[-7:]) / len(views[-7:])), "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}


def search_signal(phrase: str, *, use_trends: bool = True) -> dict[str, Any]:
    """Combine the three into one 0-1 score, keeping the raw numbers for the dashboard."""
    auto = autocomplete(phrase)
    trend = trends(phrase) if use_trends else None
    wiki = wikipedia(phrase)
    parts: list[float] = []
    if auto:
        parts.append(min(1.0, auto["count"] / 8))
    if trend:
        parts.append(min(1.0, trend["recent"] / 60) * 0.7 + (0.3 if trend["change"] > 0.2 else 0.0))
    if wiki:
        parts.append(min(1.0, math.log10(max(wiki["daily_views"], 1)) / 4))  # 10,000 views a day = 1.0
    return {
        "score": round(sum(parts) / len(parts), 3) if parts else 0.0,
        "suggestions": (auto or {}).get("suggestions", []),
        "trend": trend,
        "wikipedia": wiki,
        "available": [name for name, v in (("suggestions", auto), ("trends", trend), ("wikipedia", wiki)) if v],
    }
