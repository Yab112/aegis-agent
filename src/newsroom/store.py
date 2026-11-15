"""All Supabase access for the news desk: sources, collected items, keywords."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client, create_client

from src.newsroom.registry import SEED_SOURCES

logger = logging.getLogger("aegis.newsroom.store")

MIGRATION_HINT = "Run scripts/supabase_newsroom.sql in Supabase, then retry."
KEYWORD_COLS = "id,phrase,display,category,suggested_format,score,signals,reason,status,item_count,source_count,first_seen_at,updated_at,post_id"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NewsroomStore:
    def __init__(self, client: Client):
        self.db = client

    @classmethod
    def from_settings(cls, settings: Any) -> "NewsroomStore":
        return cls(create_client(settings.supabase_url, settings.supabase_service_key))

    # ── sources ───────────────────────────────────────────────────────────────
    def seed_sources(self) -> int:
        """Insert sources that are not in the table yet. Rows you edited in the admin are never overwritten."""
        have = {r["key"] for r in self.db.table("news_sources").select("key").execute().data or []}
        new = [s for s in SEED_SOURCES if s["key"] not in have]
        if new:
            self.db.table("news_sources").insert(new).execute()
        return len(new)

    def list_sources(self, *, only_enabled: bool = False) -> list[dict[str, Any]]:
        q = self.db.table("news_sources").select("*")
        if only_enabled:
            q = q.eq("enabled", True)
        return q.order("tier").order("name").execute().data or []

    def update_source(self, key: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        rows = self.db.table("news_sources").update(patch).eq("key", key).execute().data or []
        return rows[0] if rows else None

    def add_source(self, row: dict[str, Any]) -> dict[str, Any]:
        return (self.db.table("news_sources").insert(row).execute().data or [row])[0]

    # ── items ─────────────────────────────────────────────────────────────────
    def save_items(self, source: dict[str, Any], items: list[dict[str, Any]]) -> int:
        """Insert items not seen before (unique on url). Returns how many were new."""
        if not items:
            return 0
        urls = [i["url"] for i in items]
        known = {r["url"] for r in self.db.table("news_items").select("url").in_("url", urls).execute().data or []}
        # A page scan has no dates. The first scan of a source is its back catalogue, so it is
        # dated a month back and never counts as fresh; later scans find only what is new.
        first_scan = source.get("kind") == "page" and not int(source.get("items_total") or 0)
        undated = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat() if first_scan else now_iso()
        rows = [
            {**i, "source_key": source["key"], "tier": source["tier"], "published_at": i.get("published_at") or undated}
            for i in items
            if i["url"] not in known
        ]
        if rows:
            self.db.table("news_items").upsert(rows, on_conflict="url", ignore_duplicates=True).execute()
        return len(rows)

    def recent_items(self, days: int = 7, limit: int = 4000) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return (
            self.db.table("news_items")
            .select("id,source_key,tier,title,summary,url,published_at,first_seen_at")
            .gte("published_at", since)
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

    def list_items(self, *, source_key: str | None = None, limit: int = 60) -> list[dict[str, Any]]:
        q = self.db.table("news_items").select("id,source_key,tier,title,url,published_at")
        if source_key:
            q = q.eq("source_key", source_key)
        return q.order("published_at", desc=True).limit(limit).execute().data or []

    def count_items(self) -> int:
        return self.db.table("news_items").select("id", count="exact").limit(1).execute().count or 0

    # ── keywords ──────────────────────────────────────────────────────────────
    def existing_keywords(self) -> dict[str, dict[str, Any]]:
        rows = self.db.table("keywords").select("phrase,status,signals,first_seen_at,updated_at").execute().data or []
        return {r["phrase"]: r for r in rows}

    def upsert_keywords(self, rows: list[dict[str, Any]]) -> None:
        for i in range(0, len(rows), 50):
            self.db.table("keywords").upsert(rows[i : i + 50], on_conflict="phrase").execute()

    def best_keywords(self, *, limit: int = 10, category: str | None = None, status: str | None = "candidate") -> list[dict[str, Any]]:
        q = self.db.table("keywords").select(KEYWORD_COLS)
        if status:
            q = q.eq("status", status)
        if category:
            q = q.eq("category", category)
        return q.order("score", desc=True).limit(limit).execute().data or []

    def get_keyword(self, keyword_id: str) -> dict[str, Any] | None:
        rows = self.db.table("keywords").select("*").eq("id", keyword_id).limit(1).execute().data or []
        return rows[0] if rows else None

    def find_keyword(self, phrase: str) -> dict[str, Any] | None:
        rows = self.db.table("keywords").select("*").eq("phrase", phrase.strip().lower()).limit(1).execute().data or []
        return rows[0] if rows else None

    def set_keyword(self, keyword_id: str, patch: dict[str, Any]) -> None:
        self.db.table("keywords").update({**patch, "updated_at": now_iso()}).eq("id", keyword_id).execute()

    def recent_post_titles(self, days: int = 45) -> list[str]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.table("blog_posts").select("title").gte("created_at", since).limit(200).execute().data or []
        return [str(r.get("title") or "") for r in rows]
