"""
WhatsApp Cloud API handoff tool.

Sends a structured lead briefing to your personal WhatsApp when
the agent detects a sensitive query (rates, contracts, hiring).

Free tier: 1000 business-initiated messages/month — more than enough.
"""
from __future__ import annotations

import logging

import httpx
from datetime import datetime
from config.settings import get_settings

settings = get_settings()
logger = logging.getLogger("aegis.handoff")

WHATSAPP_API_URL = (
    f"https://graph.facebook.com/v20.0/"
    f"{settings.whatsapp_phone_number_id}/messages"
)


def send_whatsapp_briefing(
    query: str,
    intent: str,
    session_id: str,
    user_email: str | None = None,
) -> bool:
    """
    Send a formatted lead briefing to your WhatsApp number.

    Returns True if sent successfully, False otherwise.
    The agent continues gracefully even if this fails.
    """
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    email_line = f"Email: {user_email}" if user_email else "Email: not provided"

    message_body = (
        f"*Aegis-Agent Lead Alert*\n"
        f"───────────────────\n"
        f"*Time:* {timestamp}\n"
        f"*Session:* {session_id[:8]}...\n"
        f"*Intent:* {intent}\n"
        f"*{email_line}*\n\n"
        f"*Question:*\n_{query}_\n\n"
        f"Suggested response time: within 24 hours"
    )

    # This number is **you (the business owner)** — Meta delivers an alert to this inbox,
    # not to the site visitor. Visitors do not receive WhatsApp from this flow.
    to_raw = settings.whatsapp_recipient_number.strip().replace(" ", "")
    if to_raw.startswith("+"):
        to_raw = to_raw[1:]

    payload = {
        "messaging_product": "whatsapp",
        "to": to_raw,
        "type": "text",
        "text": {"body": message_body},
    }

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }

    logger.info(
        "handoff: POST graph.facebook.com WhatsApp messages (session intent=%s)",
        intent,
    )
    try:
        response = httpx.post(
            WHATSAPP_API_URL,
            json=payload,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        tail = to_raw[-4:] if len(to_raw) >= 4 else "****"
        logger.info(
            "handoff: WhatsApp API ok status=%s (alert sent to owner number …%s, not the visitor)",
            response.status_code,
            tail,
        )
        return True
    except Exception as e:
        logger.exception(
            "handoff: WhatsApp API failed (chat continues): %s",
            e,
        )
        return False
