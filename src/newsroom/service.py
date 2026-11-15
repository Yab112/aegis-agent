"""Keyword refresh: headlines → candidates → signals → score → `keywords` table."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.newsroom.categories import categorize, suggest_format
from src.newsroom.phrases import Candidate, extract_candidates
from src.newsroom.scoring import already_covered, coverage_signals, score_keyword
from src.newsroom.signals.community import community_signal
from src.newsroom.signals.search import search_signal
from src.newsroom.store import NewsroomStore, now_iso

logger = logging.getLogger("aegis.newsroom")

EXTERNAL_TTL_HOURS = 12  # outside signals are reused for this long (rate limits, and they move slowly)
MAX_EXTERNAL_LOOKUPS = 20  # per refresh: the strongest candidates get outside signals first
MAX_SOURCES_STORED = 30


def _fresh(stored: dict[str, Any] | None) -> bool:
    fetched = ((stored or {}).get("signals") or {}).get("external_at")
    if not fetched:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched)
    except ValueError:
        return False
    return age < timedelta(hours=EXTERNAL_TTL_HOURS)


def external_signals(phrase: str, *, use_trends: bool = True) -> dict[str, Any]:
    return {"search": search_signal(phrase, use_trends=use_trends), "community": community_signal(phrase), "external_at": now_iso()}


def _source_rows(cand: Candidate) -> list[dict[str, Any]]:
    """The pages the writer should read: lab sources first, then newest."""
    rows = sorted(cand.items, key=lambda i: str(i.get("published_at") or ""), reverse=True)
    rows.sort(key=lambda i: int(i.get("tier") or 2))  # stable: newest first within each tier
    seen: set[str] = set()
    out = []
    for i in rows:
        if i["url"] in seen:
            continue
        seen.add(i["url"])
        out.append({"title": i["title"], "url": i["url"], "source": i["source_key"], "tier": i.get("tier"), "published_at": i.get("published_at")})
    return out[:MAX_SOURCES_STORED]


def build_keyword_row(cand: Candidate, external: dict[str, Any], post_titles: list[str]) -> dict[str, Any]:
    signals: dict[str, Any] = {**coverage_signals(cand.items), **external}
    covered = already_covered(cand.phrase, post_titles)
    score, reason = score_keyword(signals, covered_by=covered)
    fmt, fmt_reason = suggest_format(signals)
    signals["format_reason"] = fmt_reason
    return {
        "phrase": cand.phrase,
        "display": cand.display,
        "category": categorize(cand.phrase, [i["title"] for i in cand.items], cand.sources),
        "suggested_format": fmt,
        "score": score,
        "signals": signals,
        "reason": reason,
        "item_count": len(cand.items),
        "source_count": len(cand.sources),
        "sources": _source_rows(cand),
        "updated_at": now_iso(),
    }


def refresh_keywords(store: NewsroomStore, *, days: int = 7, pause: float = 1.2) -> dict[str, Any]:
    """Recompute every candidate from the last `days` of headlines. Safe to run often."""
    items = store.recent_items(days=days)
    candidates = extract_candidates(items)
    existing = store.existing_keywords()
    post_titles = store.recent_post_titles()
    rows: list[dict[str, Any]] = []
    lookups = 0
    for cand in candidates:
        stored = existing.get(cand.phrase)
        if _fresh(stored):
            old = stored["signals"]
            external = {k: old[k] for k in ("search", "community", "external_at") if k in old}
        elif lookups < MAX_EXTERNAL_LOOKUPS:
            external = external_signals(cand.phrase)
            lookups += 1
            time.sleep(pause)  # stay polite to free endpoints
        else:
            external = {}  # scored on coverage alone this round; picked up next refresh
        row = build_keyword_row(cand, external, post_titles)
        # Every row carries a status: rows are upserted in one batch, and a key missing from
        # some rows is sent as NULL, which the table rejects. An existing status is always kept
        # (written, dismissed, writing), so a refresh never undoes your decision.
        row["status"] = (stored or {}).get("status") or "candidate"
        rows.append(row)
    if rows:
        store.upsert_keywords(rows)
    logger.info("keyword refresh: %s items, %s candidates, %s outside lookups", len(items), len(rows), lookups)
    return {"items": len(items), "keywords": len(rows), "external_lookups": lookups, "top": [(r["phrase"], r["score"]) for r in sorted(rows, key=lambda r: -r["score"])[:5]]}


def analyze_phrase(store: NewsroomStore, phrase: str, *, days: int = 14) -> dict[str, Any]:
    """Full breakdown for any phrase on demand, whether or not the collector surfaced it."""
    key = " ".join(phrase.lower().split())
    padded = f" {key} "
    items = [i for i in store.recent_items(days=days) if padded in f" {str(i.get('title') or '').lower()} "]
    cand = Candidate(phrase=key, display=phrase.strip(), items=items)
    row = build_keyword_row(cand, external_signals(key), store.recent_post_titles())
    row["in_library"] = bool(items)
    return row
