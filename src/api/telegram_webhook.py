"""
Telegram Bot updates webhook: owner replies (in-thread) → email visitor.

Register with Telegram::
    curl -fsS -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \\
      -d "url=https://YOUR_API_HOST/v1/telegram/webhook" \\
      -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request

from config.settings import get_settings
from src.tools.calendar_tool import CalendarOAuthError, send_gmail_plain_text
from src.tools.handoff_tool import send_telegram_thread_reply
from src.tools.lead_tool import fetch_handoff_telegram_alert

logger = logging.getLogger("aegis.telegram_webhook")

router = APIRouter(prefix="/telegram", tags=["telegram"])

_EMAILISH = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_visitor_email(raw: str | None) -> bool:
    if not raw or not isinstance(raw, str):
        return False
    s = raw.strip()
    return bool(_EMAILISH.match(s)) and len(s) < 254


@router.post("/webhook")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    settings = get_settings()
    secret = (settings.telegram_webhook_secret or "").strip()
    if secret:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected JSON body")

    msg = body.get("message") or body.get("edited_message")
    if not isinstance(msg, dict):
        return {"ok": True}

    if msg.get("from", {}).get("is_bot"):
        return {"ok": True}

    incoming_mid = msg.get("message_id")
    if not isinstance(incoming_mid, int):
        return {"ok": True}

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    owner_chat = str(settings.telegram_chat_id).strip()
    if str(chat_id).strip() != owner_chat:
        logger.info("telegram webhook: ignored chat_id=%s", chat_id)
        return {"ok": True}

    reply_to = msg.get("reply_to_message")
    if not isinstance(reply_to, dict):
        return {"ok": True}

    orig_id = reply_to.get("message_id")
    if not isinstance(orig_id, int):
        return {"ok": True}

    owner_text = (msg.get("text") or msg.get("caption") or "").strip()
    if not owner_text:
        send_telegram_thread_reply(
            chat_id=owner_chat,
            reply_to_message_id=incoming_mid,
            text="Send a text reply (not only media) so we can email it to the visitor.",
        )
        return {"ok": True}

    row = fetch_handoff_telegram_alert(str(chat_id), orig_id)
    if not row:
        logger.info(
            "telegram webhook: no handoff row for reply_to message_id=%s", orig_id
        )
        return {"ok": True}

    visitor_email = row.get("visitor_email")
    session_id = str(row.get("session_id") or "")
    prior_q = str(row.get("user_query") or "")[:800]

    if not _valid_visitor_email(visitor_email):
        send_telegram_thread_reply(
            chat_id=owner_chat,
            reply_to_message_id=incoming_mid,
            text=(
                "No visitor email was captured for that alert — ask them to leave an "
                "email on the site, or handle this thread manually."
            ),
        )
        return {"ok": True}

    subject = f"Message from {settings.owner_name} (portfolio)"
    mail_body = (
        f"Hi,\n\n"
        f"{settings.owner_name} asked me to pass this along regarding your "
        f"message on {settings.portfolio_url}:\n\n"
        f"---\n{owner_text}\n---\n\n"
        f"Your earlier question (summary):\n{prior_q}\n\n"
        f"— {settings.assistant_name}\n"
        f"(session {session_id[:8]}…)\n"
    )

    try:
        sent = send_gmail_plain_text(
            to_addr=str(visitor_email).strip(),
            subject=subject,
            body=mail_body,
        )
    except CalendarOAuthError:
        logger.exception("telegram webhook: Gmail OAuth failed")
        send_telegram_thread_reply(
            chat_id=owner_chat,
            reply_to_message_id=incoming_mid,
            text=(
                "Gmail send failed (OAuth). Fix GOOGLE_REFRESH_TOKEN / scopes on the API "
                "host, then try again."
            ),
        )
        return {"ok": True}

    if sent:
        send_telegram_thread_reply(
            chat_id=owner_chat,
            reply_to_message_id=incoming_mid,
            text=f"Emailed your reply to {str(visitor_email).strip()}.",
        )
    else:
        send_telegram_thread_reply(
            chat_id=owner_chat,
            reply_to_message_id=incoming_mid,
            text="Gmail API returned an error — check API logs.",
        )

    return {"ok": True}
