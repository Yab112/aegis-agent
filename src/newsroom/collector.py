"""Read every enabled source and store what is new. One slow or blocked source never stops the rest."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.newsroom.fetchers import SourceUnavailable, fetch_source
from src.newsroom.store import NewsroomStore, now_iso

logger = logging.getLogger("aegis.newsroom.collect")


def _fetch(source: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    try:
        return source, fetch_source(source), None
    except SourceUnavailable as exc:
        return source, [], str(exc)
    except Exception as exc:  # network, parse: record it against the source and move on
        return source, [], f"{type(exc).__name__}: {str(exc)[:200]}"


def collect_all(store: NewsroomStore, *, only: str | None = None) -> dict[str, Any]:
    """Returns {sources, ok, failed, new_items, errors: {key: message}}."""
    store.seed_sources()
    sources = [s for s in store.list_sources(only_enabled=True) if s["kind"] != "reference" and (not only or s["key"] == only)]
    new_total = 0
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_fetch, sources))
    for source, items, error in results:
        patch: dict[str, Any] = {"last_checked_at": now_iso(), "last_status": "ok" if error is None else "failed", "last_error": error}
        if error is None:
            try:
                added = store.save_items(source, items)
                new_total += added
                patch["items_total"] = int(source.get("items_total") or 0) + added
            except Exception as exc:
                patch.update(last_status="failed", last_error=f"save failed: {str(exc)[:200]}")
                errors[source["key"]] = patch["last_error"]
        else:
            errors[source["key"]] = error
        store.update_source(source["key"], patch)
    logger.info("newsroom collect: %s sources, %s new items, %s failed", len(sources), new_total, len(errors))
    return {"sources": len(sources), "ok": len(sources) - len(errors), "failed": len(errors), "new_items": new_total, "errors": errors}
