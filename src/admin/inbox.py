"""
Owner inbox: one place that answers a visitor, whichever surface the owner used.

Both the admin page and the Telegram reply-in-thread flow call
``send_visitor_reply``, so every reply is polished, emailed, recorded and
reflected in the conversation's status the same way.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from config.settings import Settings

logger = logging.getLogger("aegis.admin.inbox")

_EMAILISH = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALERT_COLS = "id,created_at,session_id,visitor_email,user_query,intent,telegram_message_id"
STATUSES = ("open", "replied", "handled", "spam")


def valid_email(raw: Any) -> bool:
    return isinstance(raw, str) and bool(_EMAILISH.match(raw.strip())) and len(raw.strip()) < 254


def sb(settings: Settings) -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)


def is_missing_schema(exc: BaseException) -> bool:
    """True when a table/column from scripts/supabase_admin.sql has not been created yet."""
    raw = str(exc).lower()
    return any(code in raw for code in ("pgrst204", "pgrst205", "42703", "42p01", "does not exist", "could not find"))


@dataclass
class ReplyResult:
    ok: bool
    detail: str
    to_email: str | None = None
    sent_body: str = ""


def send_visitor_reply(
    settings: Settings,
    *,
    alert: dict[str, Any],
    owner_text: str,
    channel: str,
    polish: bool = True,
) -> ReplyResult:
    """Email the owner's reply to the visitor behind ``alert`` and record it."""
    from src.email_templates import (
        build_branded_visitor_email_html,
        build_branded_visitor_email_plain,
        polish_owner_reply_for_visitor_email,
    )
    from src.tools.calendar_tool import CalendarOAuthError, send_gmail_multipart

    text = (owner_text or "").strip()
    if not text:
        return ReplyResult(False, "Reply text is empty.")
    visitor_email = alert.get("visitor_email")
    if not valid_email(visitor_email):
        return ReplyResult(False, "No visitor email was captured for this conversation.")
    to_email = str(visitor_email).strip()
    session_id = str(alert.get("session_id") or "")
    session_short = session_id[:8] or "—"

    sent_body = text
    if polish:
        try:
            sent_body = (
                polish_owner_reply_for_visitor_email(
                    settings=settings,
                    owner_draft=text,
                    visitor_question=str(alert.get("user_query") or "")[:800],
                    intent=str(alert.get("intent") or "handoff")[:120],
                )
                or ""
            ).strip() or text
        except Exception as exc:
            logger.warning("reply polish failed, sending as typed: %s", exc)

    try:
        delivered = send_gmail_multipart(
            to_addr=to_email,
            subject=f"Message from {settings.owner_name} (portfolio)",
            text_body=build_branded_visitor_email_plain(
                settings=settings, polished_plain_body=sent_body, session_short=session_short
            ),
            html_body=build_branded_visitor_email_html(
                settings=settings, polished_plain_body=sent_body, session_short=session_short
            ),
        )
    except CalendarOAuthError:
        logger.exception("visitor reply: Gmail OAuth failed")
        return ReplyResult(False, "Gmail send failed (OAuth). Refresh GOOGLE_REFRESH_TOKEN on the API host.", to_email)

    _record_reply(settings, alert, channel, to_email, text, sent_body, bool(delivered))
    if not delivered:
        return ReplyResult(False, "Gmail API returned an error — check API logs.", to_email, sent_body)
    return ReplyResult(True, f"Emailed your reply to {to_email}.", to_email, sent_body)


def _record_reply(
    settings: Settings,
    alert: dict[str, Any],
    channel: str,
    to_email: str,
    body: str,
    sent_body: str,
    delivered: bool,
) -> None:
    """Best effort: a reply that was emailed must not fail because history could not be written."""
    alert_id = alert.get("id")
    try:
        client = sb(settings)
        client.table("inbox_replies").insert(
            {
                "alert_id": alert_id,
                "session_id": str(alert.get("session_id") or ""),
                "channel": channel,
                "to_email": to_email,
                "body": body[:8000],
                "sent_body": sent_body[:8000],
                "delivered": delivered,
            }
        ).execute()
        if delivered and alert_id:
            client.table("handoff_telegram_alerts").update({"status": "replied"}).eq("id", alert_id).execute()
    except Exception as exc:
        logger.warning("reply history not recorded (run scripts/supabase_admin.sql?): %s", exc)


def set_alert_status(settings: Settings, alert_id: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    patch: dict[str, Any] = {"status": status}
    patch["handled_at"] = datetime.now(timezone.utc).isoformat() if status in ("handled", "spam") else None
    sb(settings).table("handoff_telegram_alerts").update(patch).eq("id", alert_id).execute()


def set_alert_status_by_message(settings: Settings, chat_id: str, message_id: int, status: str) -> None:
    """Telegram 'Mark handled' button: the alert is known by its message id."""
    try:
        sb(settings).table("handoff_telegram_alerts").update(
            {"status": status, "handled_at": datetime.now(timezone.utc).isoformat()}
        ).eq("telegram_chat_id", str(chat_id)).eq("telegram_message_id", int(message_id)).execute()
    except Exception as exc:
        logger.warning("alert status not saved: %s", exc)


def fetch_alert_by_message(settings: Settings, chat_id: str, message_id: int) -> dict[str, Any] | None:
    try:
        res = (
            sb(settings)
            .table("handoff_telegram_alerts")
            .select(ALERT_COLS)
            .eq("telegram_chat_id", str(chat_id).strip())
            .eq("telegram_message_id", int(message_id))
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]
    except Exception as exc:
        logger.warning("alert lookup failed: %s", exc)
        return None


def list_alerts(settings: Settings, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first. Falls back to the pre-migration columns so the inbox still lists."""
    client = sb(settings)
    try:
        q = client.table("handoff_telegram_alerts").select(f"{ALERT_COLS},status,handled_at")
        if status:
            q = q.eq("status", status)
        return q.order("created_at", desc=True).limit(limit).execute().data or []
    except Exception as exc:
        if not is_missing_schema(exc):
            raise
        rows = (
            client.table("handoff_telegram_alerts")
            .select(ALERT_COLS)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        return [{**r, "status": "open", "handled_at": None} for r in rows]


def get_conversation(settings: Settings, alert_id: str) -> dict[str, Any] | None:
    """Alert + the chat transcript the visitor had with the agent + replies already sent."""
    client = sb(settings)
    try:
        res = client.table("handoff_telegram_alerts").select(f"{ALERT_COLS},status,handled_at").eq("id", alert_id).limit(1).execute()
    except Exception as exc:
        if not is_missing_schema(exc):
            raise
        res = client.table("handoff_telegram_alerts").select(ALERT_COLS).eq("id", alert_id).limit(1).execute()
    rows = res.data or []
    if not rows:
        return None
    alert = {"status": "open", "handled_at": None, **rows[0]}

    messages: list[dict[str, Any]] = []
    try:
        s = client.table("sessions").select("messages").eq("id", alert["session_id"]).limit(1).execute()
        raw = (s.data or [{}])[0].get("messages") or []
        messages = [
            {"role": str(m.get("role") or ""), "content": str(m.get("content") or "")}
            for m in raw
            if isinstance(m, dict)
        ]
    except Exception as exc:
        logger.info("no transcript for session %s: %s", alert.get("session_id"), exc)

    replies: list[dict[str, Any]] = []
    try:
        replies = (
            client.table("inbox_replies")
            .select("id,created_at,channel,to_email,body,sent_body,delivered")
            .eq("alert_id", alert_id)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        if not is_missing_schema(exc):
            raise
    return {"alert": alert, "messages": messages, "replies": replies}
