"""Reusable email bodies. Stored in `email_templates`; these defaults are inserted once and are yours to edit."""
from __future__ import annotations

from typing import Any

DEFAULT_TEMPLATES: list[dict[str, str]] = [
    {
        "key": "first-reply",
        "name": "First reply",
        "subject": "Re: your message on {{site}}",
        "body_md": "Hi {{first_name}},\n\nThanks for reaching out. I read your message and I'd like to understand what you need before suggesting anything.\n\nCould you tell me:\n- what you want to build or fix\n- what already exists\n- when you need it\n\nA short reply is enough, and I'll come back with a concrete next step.",
    },
    {
        "key": "project-scope",
        "name": "Project scoping",
        "subject": "Next step for your project",
        "body_md": "Hi {{first_name}},\n\nThis is the kind of work I do. To give you a fixed estimate I need three things:\n\n- **The outcome** you want, in one or two sentences\n- **What is already built**, and where it runs\n- **Your deadline**, and who signs off\n\nA 20-minute call is usually the fastest way to settle these.\n\n[[Pick a time|{{site}}/contact]]",
    },
    {
        "key": "pricing",
        "name": "Pricing question",
        "subject": "About pricing",
        "body_md": "Hi {{first_name}},\n\nThanks for asking. My pricing depends on scope, so a real number needs a little detail from you.\n\n## How I usually work\n- A fixed price for a defined piece of work\n- Or a weekly rate when the scope is still moving\n\nSend me what you want built and your timeline, and I'll reply with an estimate.",
    },
    {
        "key": "follow-up",
        "name": "Follow-up",
        "subject": "Following up",
        "body_md": "Hi {{first_name}},\n\nI wanted to follow up on my last message in case it got buried.\n\nIf the timing is not right, no problem at all; tell me and I'll close this on my side. If it is still relevant, a one-line reply is enough to pick it back up.",
    },
    {
        "key": "thank-you",
        "name": "Thank you",
        "subject": "Thank you",
        "body_md": "Hi {{first_name}},\n\nThank you for the conversation. It was good to hear what you are working on.\n\nIf anything comes up where I can help, you know where to find me.\n\n[[See my recent work|{{site}}/work]]",
    },
]


def ensure_defaults(client: Any) -> None:
    have = {r["key"] for r in client.table("email_templates").select("key").execute().data or []}
    new = [t for t in DEFAULT_TEMPLATES if t["key"] not in have]
    if new:
        client.table("email_templates").insert(new).execute()


def list_templates(client: Any) -> list[dict[str, Any]]:
    ensure_defaults(client)
    return client.table("email_templates").select("*").order("name").execute().data or []
