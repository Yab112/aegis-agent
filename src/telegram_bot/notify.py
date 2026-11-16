"""Telegram messages about the blog: a post was written, with buttons to act on it."""
from __future__ import annotations

import logging
from typing import Any

from src.tools.handoff_tool import telegram_call

logger = logging.getLogger("aegis.telegram.notify")


def admin_url(settings: Any, path: str = "") -> str:
    return f"{str(settings.portfolio_url).rstrip('/')}/admin{path}"


def post_keyboard(settings: Any, post_id: str, slug: str, post_status: str) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    if post_status == "published":
        rows.append([{"text": "↩️ Unpublish", "callback_data": f"bp:unpub:{post_id}"}])
        rows.append([{"text": "🌐 View live", "url": f"{str(settings.portfolio_url).rstrip('/')}/blog/{slug}"}])
    else:
        rows.append(
            [
                {"text": "✅ Publish now", "callback_data": f"bp:pub:{post_id}"},
                {"text": "🗑 Discard", "callback_data": f"bp:del:{post_id}"},
            ]
        )
    rows.append([{"text": "✏️ Read and edit in admin", "url": admin_url(settings, f"/posts/{post_id}")}])
    return {"inline_keyboard": rows}


def post_written_text(result: dict[str, Any]) -> str:
    rich = result.get("rich") or {}
    facts = [f"{result.get('words', '?')} words", f"{result.get('sources', 0)} source(s)"]
    for key, label in (("charts", "chart"), ("tables", "table"), ("figures", "figure"), ("x_posts", "X post"), ("diagrams", "diagram"), ("footnotes", "footnote")):
        n = int(rich.get(key) or 0)
        if n:
            facts.append(f"{n} {label}{'s' if n != 1 else ''}")
    lines = [
        f"📝 New post written: {result.get('title') or result.get('slug')}",
        f"Status: {result.get('post_status')}",
        " · ".join(facts),
    ]
    if not result.get("research_grounded"):
        lines.append("⚠️ Web research was unavailable, so this post cites little. Read it before publishing.")
    if not result.get("quality_gate_passed", True):
        lines.append(f"⚠️ Quality gate: {result.get('quality_gate_reason')} (held for review)")
    fixes = []
    for key, label in (
        ("charts_dropped", "invalid chart(s) removed"),
        ("charts_marked_illustrative", "chart(s) relabelled illustrative"),
        ("figures_dropped", "figure(s) without a usable image removed"),
        ("links_unlinked", "unverified link(s) removed"),
        ("footnotes_without_source", "footnote(s) without a source link"),
    ):
        if int(rich.get(key) or 0):
            fixes.append(f"{rich[key]} {label}")
    if fixes:
        lines.append("Checks: " + "; ".join(fixes))
    return "\n".join(lines)


def notify_post_written(settings: Any, result: dict[str, Any]) -> bool:
    """Tell the owner a post exists and let them publish or discard it from the chat."""
    if result.get("status") != "inserted" or not result.get("id"):
        return False
    chat_id = str(getattr(settings, "telegram_chat_id", "") or "").strip()
    if not chat_id:
        return False
    return telegram_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": post_written_text(result)[:3900],
            "disable_web_page_preview": True,
            "reply_markup": post_keyboard(
                settings, str(result["id"]), str(result.get("slug") or ""), str(result.get("post_status") or "")
            ),
        },
    )
