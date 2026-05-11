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
import time

from fastapi import APIRouter, HTTPException, Request

from config.settings import get_settings
from src.tools.calendar_tool import CalendarOAuthError, send_gmail_multipart
from src.tools.handoff_tool import send_telegram_thread_reply
from src.tools.lead_tool import fetch_handoff_telegram_alert
from src.tools.visitor_reply_email import (
    build_branded_visitor_email_html,
    build_branded_visitor_email_plain,
    polish_owner_reply_for_visitor_email,
)

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
    intent_tag = str(row.get("intent") or "handoff").strip()[:120] or "handoff"
    session_short = session_id[:8] if len(session_id) >= 8 else (session_id or "—")

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

    t0 = time.perf_counter()
    polished = polish_owner_reply_for_visitor_email(
        settings=settings,
        owner_draft=owner_text,
        visitor_question=prior_q,
        intent=intent_tag,
    )
    if not (polished or "").strip():
        polished = owner_text

    text_body = build_branded_visitor_email_plain(
        settings=settings,
        polished_plain_body=polished,
        session_short=session_short,
    )
    html_body = build_branded_visitor_email_html(
        settings=settings,
        polished_plain_body=polished,
        session_short=session_short,
    )

    try:
        sent = send_gmail_multipart(
            to_addr=str(visitor_email).strip(),
            subject=subject,
            text_body=text_body,
            html_body=html_body,
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

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "telegram visitor email: session=%s ok=%s ms=%.0f polish=%s",
        session_short,
        sent,
        elapsed_ms,
        settings.visitor_reply_email_polish,
    )

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
