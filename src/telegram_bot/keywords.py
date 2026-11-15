"""Telegram: the keyword desk in the chat. /keywords lists the best ones; a button starts the writer."""
from __future__ import annotations

from typing import Any, Callable

from src.newsroom.categories import category_labels
from src.newsroom.store import NewsroomStore
from src.tools.handoff_tool import telegram_call


def send_best_keywords(settings: Any, chat_id: str, send: Callable[..., None], limit: int = 5) -> None:
    rows = NewsroomStore.from_settings(settings).best_keywords(limit=limit)
    if not rows:
        send(chat_id, "No keyword candidates yet. The source reader and keyword desk fill this on schedule (/jobs).")
        return
    labels = category_labels()
    for r in rows:
        sig = r.get("signals") or {}
        cov = sig.get("coverage") or {}
        lines = [
            f"{r['score']:.0f}/100 · {r.get('display') or r['phrase']}",
            f"{labels.get(r.get('category'), 'AI news')} · best as {r.get('suggested_format')}",
            f"{cov.get('sources', 0)} sources ({cov.get('lab_sources', 0)} labs) · search {(sig.get('search') or {}).get('score', 0):.2f}",
            r.get("reason") or "",
        ]
        send(
            chat_id,
            "\n".join(lines),
            {
                "inline_keyboard": [
                    [
                        {"text": "✍️ Write it", "callback_data": f"kw:write:{r['id']}"},
                        {"text": "Dismiss", "callback_data": f"kw:skip:{r['id']}"},
                    ]
                ]
            },
        )


def handle_keyword_button(settings: Any, callback: dict[str, Any], chat_id: str, message_id: int) -> None:
    from src.api.admin_routes import start_generation
    from src.api.keyword_routes import topic_from_keyword

    _, action, keyword_id = str(callback.get("data") or "").split(":", 2)
    store = NewsroomStore.from_settings(settings)
    kw = store.get_keyword(keyword_id)

    def done(label: str, toast: str) -> None:
        telegram_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": "ho:noop"}]]}})
        telegram_call("answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": toast})

    if not kw:
        telegram_call("answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": "That keyword is gone"})
    elif action == "skip":
        store.set_keyword(keyword_id, {"status": "dismissed"})
        done("Dismissed", "Dismissed")
    elif action == "write":
        topic = {**topic_from_keyword(kw), "keyword_id": keyword_id}
        if start_generation(settings, topic, "review"):
            store.set_keyword(keyword_id, {"status": "writing"})
            done("✍️ Writing…", "Started. I will message you when it is ready to review.")
        else:
            telegram_call("answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": "The writer is already running"})
