"""
Owner commands for the Telegram bot: run the site from the chat.

Only messages from ``TELEGRAM_CHAT_ID`` reach this module (the webhook checks).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.admin import inbox
from src.telegram_bot.notify import admin_url, post_keyboard
from src.tools.handoff_tool import telegram_call

logger = logging.getLogger("aegis.telegram.commands")

HELP = """Aegis control

Inbox
/inbox — open conversations
/reply <id> <text> — email a visitor (or reply to the alert bubble)

Blog
/keywords — best keywords to write now, with a Write button
/posts — drafts and posts waiting for review
/write <topic> — research and write a post now
/write — write from the best queued idea
/idea <title> — queue an idea for the writer
/publish <slug> · /unpublish <slug>

Site
/stats — numbers at a glance
/jobs — scheduled jobs · /run <job>
/admin — link to the admin page"""

BOT_COMMANDS = [
    {"command": "inbox", "description": "Open conversations"},
    {"command": "keywords", "description": "Best keywords to write now"},
    {"command": "posts", "description": "Drafts and posts in review"},
    {"command": "write", "description": "Write a post: /write <topic>"},
    {"command": "idea", "description": "Queue a blog idea"},
    {"command": "stats", "description": "Numbers at a glance"},
    {"command": "jobs", "description": "Scheduled jobs"},
    {"command": "admin", "description": "Open the admin page"},
    {"command": "help", "description": "All commands"},
]


def _send(chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    telegram_call("sendMessage", payload)


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _age(iso: str | None) -> str:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 0)}m ago"
    if mins < 60 * 48:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def _find_alert(settings: Any, prefix: str) -> dict[str, Any] | None:
    prefix = prefix.strip().lower()
    if len(prefix) < 4:
        return None
    matches = [a for a in inbox.list_alerts(settings, limit=200) if str(a["id"]).lower().startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def _post_by_slug(settings: Any, slug: str) -> dict[str, Any] | None:
    rows = (
        inbox.sb(settings).table("blog_posts").select("id,slug,title,status,published_at").eq("slug", slug.strip()).limit(1).execute().data
        or []
    )
    return rows[0] if rows else None


def set_post_status(settings: Any, post_id: str, new_status: str) -> dict[str, Any] | None:
    """Publish / unpublish, with the same side effects as the admin page."""
    from src.api.admin_routes import _clear_blog_cache

    client = inbox.sb(settings)
    rows = client.table("blog_posts").select("id,slug,title,status,published_at").eq("id", post_id).limit(1).execute().data or []
    if not rows:
        return None
    patch: dict[str, Any] = {"status": new_status}
    going_live = new_status == "published" and rows[0]["status"] != "published"
    if going_live and not rows[0].get("published_at"):
        patch["published_at"] = datetime.now(timezone.utc).isoformat()
    updated = client.table("blog_posts").update(patch).eq("id", post_id).execute().data or []
    _clear_blog_cache()
    post = updated[0] if updated else {**rows[0], **patch}
    if going_live:
        from src.blog.publish_webhook import notify_blog_post_published

        notify_blog_post_published(post=post, source="telegram", pipeline_run_key=None)
    return post


def handle_command(settings: Any, chat_id: str, text: str) -> bool:
    """Return True when ``text`` was a command and has been answered."""
    if not text.startswith("/"):
        return False
    head, _, arg = text.partition(" ")
    cmd = head[1:].split("@", 1)[0].lower()
    arg = arg.strip()
    try:
        _dispatch(settings, chat_id, cmd, arg)
    except Exception as exc:
        logger.exception("telegram command /%s failed", cmd)
        _send(chat_id, f"/{cmd} failed: {str(exc)[:300]}")
    return True


def _dispatch(settings: Any, chat_id: str, cmd: str, arg: str) -> None:
    site = str(settings.portfolio_url).rstrip("/")

    if cmd in ("start", "help"):
        _send(chat_id, HELP)

    elif cmd == "admin":
        _send(chat_id, "Admin page:", {"inline_keyboard": [[{"text": "Open admin", "url": admin_url(settings)}]]})

    elif cmd == "inbox":
        alerts = [a for a in inbox.list_alerts(settings, limit=100) if a.get("status") == "open"][:8]
        if not alerts:
            _send(chat_id, "Inbox zero. No open conversations.")
            return
        lines = [f"{len(alerts)} open conversation(s):", ""]
        for a in alerts:
            lines.append(f"{str(a['id'])[:6]} · {a.get('visitor_email') or 'no email'} · {_age(a.get('created_at'))}")
            lines.append(f"   {_clip(a.get('user_query') or '', 140)}")
        lines += ["", "Answer with /reply <id> <text>"]
        _send(chat_id, "\n".join(lines), {"inline_keyboard": [[{"text": "Open inbox", "url": admin_url(settings, "/inbox")}]]})

    elif cmd == "reply":
        ident, _, body = arg.partition(" ")
        alert = _find_alert(settings, ident) if ident else None
        if not alert or not body.strip():
            _send(chat_id, "Usage: /reply <id> <text>. Get the id from /inbox.")
            return
        result = inbox.send_visitor_reply(settings, alert=alert, owner_text=body, channel="telegram")
        _send(chat_id, result.detail)

    elif cmd == "posts":
        rows = (
            inbox.sb(settings)
            .table("blog_posts")
            .select("id,slug,title,status,created_at")
            .in_("status", ["draft", "review"])
            .order("created_at", desc=True)
            .limit(6)
            .execute()
            .data
            or []
        )
        if not rows:
            _send(chat_id, "Nothing waiting. /write <topic> starts a new post.")
            return
        for r in rows:
            _send(
                chat_id,
                f"{r['status'].upper()} · {_age(r.get('created_at'))}\n{r['title']}",
                post_keyboard(settings, str(r["id"]), r["slug"], r["status"]),
            )

    elif cmd == "keywords":
        from src.telegram_bot.keywords import send_best_keywords

        send_best_keywords(settings, chat_id, _send)

    elif cmd == "write":
        from src.api.admin_routes import start_generation

        topic = {"title": arg} if len(arg) >= 6 else None
        if arg and not topic:
            _send(chat_id, "Give me a fuller topic, e.g. /write Why RAG pipelines fail on tables")
            return
        if start_generation(settings, topic, "review"):
            what = f"“{arg}”" if topic else "the best queued idea"
            _send(chat_id, f"Researching and writing {what}. This takes a few minutes; I'll message you when it is ready to review.")
        else:
            _send(chat_id, "The writer is already running. I'll message you when it finishes.")

    elif cmd == "idea":
        from src.api.admin_routes import add_manual_idea

        if len(arg) < 6:
            _send(chat_id, "Usage: /idea <title of the post you want written>")
            return
        add_manual_idea(settings, arg)
        _send(chat_id, f"Queued: {arg}\nIt goes to the front of the writer's queue.")

    elif cmd in ("publish", "unpublish"):
        post = _post_by_slug(settings, arg) if arg else None
        if not post:
            _send(chat_id, f"Usage: /{cmd} <slug>. See /posts.")
            return
        updated = set_post_status(settings, str(post["id"]), "published" if cmd == "publish" else "draft")
        if cmd == "publish":
            _send(chat_id, f"Published: {site}/blog/{post['slug']}")
        else:
            _send(chat_id, f"Unpublished “{(updated or post)['title']}”. It is a draft again.")

    elif cmd == "stats":
        from src.api.admin_routes import overview

        o = overview(settings)
        p = o["posts"]
        _send(
            chat_id,
            "\n".join(
                [
                    "Site at a glance",
                    f"Inbox: {o['inbox']['open']} open of {o['inbox']['total']}",
                    f"Leads captured: {o['leads']}",
                    f"Posts: {p.get('published')} published · {p.get('review')} in review · {p.get('draft')} draft",
                    f"Ideas queued: {o['ideas_pending']}",
                    f"Writer: {'running' if o['generation'].get('running') else 'idle'}",
                ]
            ),
        )

    elif cmd == "jobs":
        from src.scheduler.service import get_scheduler

        lines = ["Scheduled jobs", ""]
        for j in get_scheduler().status()["jobs"]:
            state = "on" if j["enabled"] else "paused"
            lines.append(f"{j['name']} ({state}) · last: {j.get('last_status') or 'never'} · next: {(j.get('next_run_at') or 'on change')[:16]}")
        lines += ["", "Run one now with /run <job>"]
        _send(chat_id, "\n".join(lines))

    elif cmd == "run":
        import threading

        from src.scheduler.service import get_scheduler

        scheduler = get_scheduler()
        if arg not in scheduler.jobs:
            _send(chat_id, f"Unknown job. Known: {', '.join(scheduler.jobs)}")
            return
        threading.Thread(target=scheduler.run_job, args=(arg,), kwargs={"trigger": "manual"}, daemon=True).start()
        _send(chat_id, f"Started {arg}.")

    else:
        _send(chat_id, "Unknown command. /help lists what I can do.")


def handle_post_button(settings: Any, callback: dict[str, Any], chat_id: str, message_id: int) -> None:
    """Inline buttons under a post message: publish, unpublish, discard (with confirm)."""
    cb_id = callback.get("id")
    _, action, post_id = str(callback.get("data") or "").split(":", 2)
    site = str(settings.portfolio_url).rstrip("/")

    def answer(text: str) -> None:
        telegram_call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:190]})

    def set_keyboard(markup: dict[str, Any]) -> None:
        telegram_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": markup})

    if action in ("pub", "unpub"):
        post = set_post_status(settings, post_id, "published" if action == "pub" else "draft")
        if not post:
            answer("That post no longer exists")
            return
        set_keyboard(post_keyboard(settings, post_id, post["slug"], post["status"]))
        if action == "pub":
            answer("Published")
            _send(chat_id, f"✅ Live: {site}/blog/{post['slug']}")
        else:
            answer("Back to draft")
    elif action == "del":
        set_keyboard(
            {
                "inline_keyboard": [
                    [
                        {"text": "Yes, delete it", "callback_data": f"bp:delok:{post_id}"},
                        {"text": "Keep", "callback_data": f"bp:keep:{post_id}"},
                    ]
                ]
            }
        )
        answer("Delete this post?")
    elif action == "delok":
        from src.api.admin_routes import _clear_blog_cache

        inbox.sb(settings).table("blog_posts").delete().eq("id", post_id).execute()
        _clear_blog_cache()
        set_keyboard({"inline_keyboard": [[{"text": "🗑 Deleted", "callback_data": "ho:noop"}]]})
        answer("Deleted")
    elif action == "keep":
        rows = inbox.sb(settings).table("blog_posts").select("slug,status").eq("id", post_id).limit(1).execute().data or []
        if rows:
            set_keyboard(post_keyboard(settings, post_id, rows[0]["slug"], rows[0]["status"]))
        answer("Kept")
    else:
        answer("")
