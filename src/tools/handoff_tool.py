"""
Telegram Bot API handoff + owner reply relay.

1. Sends a structured lead briefing to your Telegram when the agent hands off.
2. When you **reply in Telegram to that alert** (same thread), the API webhook emails
   your reply text to the visitor (if we captured their email on the handoff).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from config.settings import get_settings

logger = logging.getLogger("aegis.handoff")

TELEGRAM_MAX_MESSAGE_LEN = 4096


@dataclass(frozen=True)
class TelegramBriefingResult:
    ok: bool
    message_id: int | None = None


def _telegram_api_base(bot_token: str) -> str:
    return f"https://api.telegram.org/bot{bot_token.strip()}"


def send_telegram_briefing(
    query: str,
    intent: str,
    session_id: str,
    user_email: str | None = None,
) -> TelegramBriefingResult:
    """
    Send a formatted lead briefing to the configured Telegram chat.

    Returns ``TelegramBriefingResult`` with Telegram ``message_id`` when ``ok``,
    so the owner can **reply to that message** to email the visitor (webhook flow).
    """
    settings = get_settings()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    email_line = f"Email: {user_email}" if user_email else "Email: not provided"

    message_body = (
        "Aegis-Agent Lead Alert\n"
        "───────────────────\n"
        f"Time: {timestamp}\n"
        f"Session: {session_id[:8]}…\n"
        f"Intent: {intent}\n"
        f"{email_line}\n\n"
        "Question:\n"
        f"{query}\n\n"
        "— Reply to this message (thread) to email your answer to the visitor "
        "(when email was provided).\n"
        "Suggested response time: within 24 hours"
    )
    if len(message_body) > TELEGRAM_MAX_MESSAGE_LEN:
        message_body = message_body[: TELEGRAM_MAX_MESSAGE_LEN - 20] + "\n…(truncated)"

    chat_id = settings.telegram_chat_id.strip()
    token = settings.telegram_bot_token.strip()

    payload = {
        "chat_id": chat_id,
        "text": message_body,
        "disable_web_page_preview": True,
    }

    logger.info(
        "handoff: POST api.telegram.org sendMessage (session intent=%s)",
        intent,
    )
    try:
        response = httpx.post(
            f"{_telegram_api_base(token)}/sendMessage",
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        if not data.get("ok"):
            logger.error(
                "handoff: Telegram API returned ok=false: %s",
                data.get("description") or data,
            )
            return TelegramBriefingResult(ok=False)
        mid = (data.get("result") or {}).get("message_id")
        if isinstance(mid, int):
            logger.info(
                "handoff: Telegram ok message_id=%s (reply in-thread to email visitor)",
                mid,
            )
            return TelegramBriefingResult(ok=True, message_id=mid)
        logger.warning("handoff: Telegram ok but no message_id in result")
        return TelegramBriefingResult(ok=True, message_id=None)
    except Exception as e:
        logger.exception(
            "handoff: Telegram failed (chat continues): %s",
            e,
        )
        return TelegramBriefingResult(ok=False)


def send_telegram_thread_reply(
    *,
    chat_id: str,
    reply_to_message_id: int,
    text: str,
) -> bool:
    """Send a short message in the same chat, threaded under ``reply_to_message_id``."""
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    body = (text or "").strip()
    if len(body) > TELEGRAM_MAX_MESSAGE_LEN:
        body = body[: TELEGRAM_MAX_MESSAGE_LEN - 10] + "…"
    if not body:
        return False
    try:
        r = httpx.post(
            f"{_telegram_api_base(token)}/sendMessage",
            json={
                "chat_id": chat_id.strip(),
                "text": body,
                "reply_to_message_id": reply_to_message_id,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json() if r.content else {}
        return bool(data.get("ok"))
    except Exception as e:
        logger.warning("handoff: thread reply failed: %s", e)
        return False
