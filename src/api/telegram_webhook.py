"""
Telegram Bot updates webhook: owner replies (in-thread) → email visitor.

Register with Telegram::
    curl -fsS -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \\
      -d "url=https://YOUR_API_HOST/v1/telegram/webhook" \\
      -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from config.settings import get_settings
from src.tools.handoff_tool import send_telegram_thread_reply
from src.tools.lead_tool import fetch_handoff_telegram_alert

logger = logging.getLogger("aegis.telegram_webhook")

router = APIRouter(prefix="/telegram", tags=["telegram"])

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

    callback = body.get("callback_query")
    if isinstance(callback, dict):
        _handle_button(callback, str(settings.telegram_chat_id).strip())
        return {"ok": True}

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
        # Owner typed in chat but did not use Reply on the lead alert — we cannot map to a visitor.
        loose = (msg.get("text") or msg.get("caption") or "").strip()
        if loose.startswith("/"):
            from src.telegram_bot.commands import handle_command

            handle_command(settings, owner_chat, loose)
        elif loose:
            send_telegram_thread_reply(
                chat_id=owner_chat,
                reply_to_message_id=incoming_mid,
                text=(
                    "To email a visitor, reply to their alert bubble, or use /reply <id> <text> "
                    "(ids are in /inbox). /help lists everything else I can do."
                ),
            )
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

    from src.admin import inbox

    row = inbox.fetch_alert_by_message(settings, str(chat_id), orig_id) or fetch_handoff_telegram_alert(
        str(chat_id), orig_id
    )
    if not row:
        logger.info("telegram webhook: no handoff row for reply_to message_id=%s", orig_id)
        send_telegram_thread_reply(
            chat_id=owner_chat,
            reply_to_message_id=incoming_mid,
            text=(
                "That message is not a visitor alert I have on record, so there is nobody to email. "
                "Reply to a lead alert bubble, or use /reply <id> <text> with an id from /inbox."
            ),
        )
        return {"ok": True}

    result = inbox.send_visitor_reply(settings, alert=row, owner_text=owner_text, channel="telegram")
    logger.info("telegram visitor email: session=%s ok=%s", str(row.get("session_id") or "")[:8], result.ok)
    send_telegram_thread_reply(chat_id=owner_chat, reply_to_message_id=incoming_mid, text=result.detail)
    return {"ok": True}


def _handle_button(callback: dict, owner_chat: str) -> None:
    """Route inline buttons: ``bp:*`` act on blog posts, ``ho:*`` on visitor alerts."""
    data = str(callback.get("data") or "")
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "").strip()
    message_id = message.get("message_id")
    if data.startswith("bp:") and chat_id == owner_chat and isinstance(message_id, int):
        from src.telegram_bot.commands import handle_post_button

        try:
            handle_post_button(get_settings(), callback, chat_id, message_id)
        except Exception:
            logger.exception("telegram webhook: post button failed")
        return
    if data.startswith("kw:") and chat_id == owner_chat and isinstance(message_id, int):
        from src.telegram_bot.keywords import handle_keyword_button

        try:
            handle_keyword_button(get_settings(), callback, chat_id, message_id)
        except Exception:
            logger.exception("telegram webhook: keyword button failed")
        return
    _handle_handoff_button(callback, owner_chat)


def _handle_handoff_button(callback: dict, owner_chat: str) -> None:
    """Inline buttons under a handoff alert: suggest a reply, or mark it handled."""
    from datetime import datetime, timezone

    from src.tools.handoff_brief import reply_draft
    from src.tools.handoff_tool import telegram_call

    settings = get_settings()
    cb_id = callback.get("id")
    data = str(callback.get("data") or "")
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "").strip()
    message_id = message.get("message_id")
    if chat_id != owner_chat or not isinstance(message_id, int):
        logger.info("telegram webhook: ignored callback from chat_id=%s", chat_id)
        return

    if data.startswith("ho:draft:"):
        draft = reply_draft(data.split(":", 2)[2], settings.owner_name, settings.portfolio_url)
        send_telegram_thread_reply(
            chat_id=chat_id,
            reply_to_message_id=message_id,
            text=(
                "Draft, not sent. Copy it, edit it, then send it as a reply to the alert above "
                "to email the visitor:\n\n" + draft
            ),
        )
        telegram_call("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Draft posted below"})
    elif data == "ho:done":
        stamp = datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")
        telegram_call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": [[{"text": f"✅ Handled {stamp}", "callback_data": "ho:noop"}]]},
            },
        )
        telegram_call("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Marked handled"})
        from src.admin import inbox

        inbox.set_alert_status_by_message(settings, chat_id, message_id, "handled")
    else:
        telegram_call("answerCallbackQuery", {"callback_query_id": cb_id})
