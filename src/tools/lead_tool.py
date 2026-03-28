"""
Lead logger — writes captured leads to Supabase leads table.
Acts as a mini-CRM for your freelance pipeline.
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone

from supabase import create_client

from config.settings import get_settings

settings = get_settings()
_log = logging.getLogger("aegis.lead")
_client = None

def get_client():
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client


def log_lead(
    session_id: str,
    email: str,
    intent: str,
    query: str,
) -> bool:
    """
    Upsert a lead record. Uses email as the natural unique key so
    repeat visits from the same lead don't create duplicates.
    """
    try:
        client = get_client()
        client.table("leads").upsert(
            {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "email": email,
                "intent": intent,
                "query": query,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="email",
        ).execute()
        return True
    except Exception as e:
        _log.warning("Lead log failed: %s", e)
        return False


def upsert_session(session_id: str, messages: list[dict], user_email: str | None = None) -> None:
    """Persist conversation session for context continuity."""
    try:
        client = get_client()
        client.table("sessions").upsert(
            {
                "id": session_id,
                "messages": messages,
                "user_email": user_email,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as e:
        _log.warning("Session upsert failed: %s", e)


def load_session(session_id: str) -> list[dict]:
    """Load prior conversation messages for a session."""
    try:
        client = get_client()
        result = client.table("sessions").select("messages").eq("id", session_id).execute()
        if result.data:
            return result.data[0].get("messages", [])
    except Exception:
        pass
    return []
