"""
One score per keyword (0-100) from signals that each run 0-1.

  coverage   how many trusted sources wrote about it, lab sources counting more
  momentum   share of that coverage from the last 48 hours
  search     suggestions + Trends + Wikipedia views
  community  Hacker News + GitHub + Hugging Face
  freshness  hours since a trusted source first published it

A keyword we already published on recently is multiplied down, not removed, so it
can come back if the story moves.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

WEIGHTS = {"coverage": 0.28, "search": 0.25, "freshness": 0.18, "momentum": 0.17, "community": 0.12}
TIER_WEIGHT = {1: 1.5, 2: 1.0, 3: 0.6, 4: 0.8}
FRESH_HALF_LIFE_HOURS = 72.0


def _parse(ts: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def coverage_signals(items: list[dict[str, Any]], now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Coverage, momentum and freshness all come from our own collected items."""
    now = now or datetime.now(timezone.utc)
    by_source: dict[str, int] = {}
    for item in items:
        by_source.setdefault(item["source_key"], int(item.get("tier") or 2))
    weighted = sum(TIER_WEIGHT.get(t, 1.0) for t in by_source.values())
    dates = [d for d in (_parse(i.get("published_at")) for i in items) if d]
    last_48h = sum(1 for d in dates if (now - d).total_seconds() <= 48 * 3600)
    first = min(dates) if dates else now
    age_hours = max(0.0, (now - first).total_seconds() / 3600)
    return {
        "coverage": {
            "score": round(min(1.0, weighted / 6.0), 3),
            "sources": len(by_source),
            "lab_sources": sum(1 for t in by_source.values() if t == 1),
            "items": len(items),
        },
        "momentum": {"score": round(last_48h / len(dates), 3) if dates else 0.0, "last_48h": last_48h},
        "freshness": {"score": round(0.5 ** (age_hours / FRESH_HALF_LIFE_HOURS), 3), "age_hours": round(age_hours, 1)},
    }


def already_covered(phrase: str, post_titles: list[str]) -> str | None:
    """The recent post title that already targets this phrase, if any."""
    # Version numbers are short but decisive: "Gemini 3" does not cover "Gemini 4".
    words = [w for w in re.findall(r"[a-z0-9.]+", phrase.lower()) if len(w) > 2 or any(c.isdigit() for c in w)]
    if not words:
        return None
    for title in post_titles:
        tokens = set(re.findall(r"[a-z0-9.]+", title.lower()))
        if all(w in tokens for w in words):
            return title
    return None


def score_keyword(signals: dict[str, dict[str, Any]], *, covered_by: str | None = None) -> tuple[float, str]:
    """Return (score 0-100, one-sentence reason naming the strongest and weakest signal)."""
    parts = {name: float((signals.get(name) or {}).get("score") or 0.0) for name in WEIGHTS}
    raw = sum(parts[name] * weight for name, weight in WEIGHTS.items())
    score = raw * 100
    if covered_by:
        score *= 0.25
    best = max(parts, key=lambda n: parts[n] * WEIGHTS[n])
    worst = min(parts, key=lambda n: parts[n])
    words = {
        "coverage": f"{signals['coverage']['sources']} trusted sources ({signals['coverage']['lab_sources']} labs)",
        "search": "search interest",
        "freshness": f"first seen {signals['freshness']['age_hours']:.0f}h ago",
        "momentum": f"{signals['momentum']['last_48h']} items in the last 48h",
        "community": "builder activity",
    }
    reason = f"Strongest: {words[best]}. Weakest: {worst} ({parts[worst]:.2f})."
    if covered_by:
        reason += f" Already covered by “{covered_by[:70]}”, so the score is cut to a quarter."
    return round(score, 1), reason
