"""Turn a handoff into a briefing the owner can act on in ten seconds.

Everything here is deterministic: scoring, category, recommended next steps and
the draft reply are rules over the conversation, so the alert never depends on a
model being up — a handoff usually happens exactly when something needs a human.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote_plus

from src.agent.visitor import VisitorProfile

TELEGRAM_MAX_MESSAGE_LEN = 4096

_FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com", "gmx.com", "yandex.com",
}

_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("solicitation", r"\b(seo services|guest post|backlinks?|link exchange|sponsored post|crypto|casino|forex|buy followers|web traffic)\b"),
    ("hiring", r"\b(hir(e|ing)|full[- ]?time|part[- ]?time|role|position|job|recruit\w*|salary|compensation|join (our|the) team|interview|cv|resume)\b"),
    ("pricing", r"\b(rate|rates|price|pricing|cost|quote|estimate|how much|budget|hourly|retainer|invoice)\b"),
    ("project", r"\b(project|build|develop\w*|mvp|app|website|platform|automation|agent|rag|integrat\w*|freelance|contract|scope|deliver\w*)\b"),
    ("partnership", r"\b(partner\w*|collaborat\w*|co-?found\w*|speak\w*|podcast|workshop|mentor\w*)\b"),
    ("support", r"\b(bug|broken|error|not working|issue|problem|complain\w*|refund)\b"),
)

_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"(\$|€|£)\s?\d|\b\d+\s?(k|usd|eur|birr|etb)\b|\bbudget\b", 15, "mentions a budget or figure"),
    (r"\b(asap|urgent\w*|deadline|this week|next week|by (monday|tuesday|wednesday|thursday|friday|end of)|within \d+ (days?|weeks?)|start (now|immediately|soon))\b", 15, "has a timeline"),
    (r"\b(we are|we're|our (company|team|startup|product|client)|i run|i lead|founder|cto|ceo|head of|hr|recruiter)\b", 10, "speaks for a company or team"),
    (r"\b(call|meet\w*|zoom|google meet|schedule|available|availability)\b", 10, "wants to talk live"),
)

_CATEGORY_LABEL = {
    "hiring": "Hiring",
    "pricing": "Pricing",
    "project": "New project",
    "partnership": "Partnership",
    "support": "Support",
    "solicitation": "Solicitation",
    "other": "General",
}


@dataclass(frozen=True)
class HandoffBrief:
    text: str
    score: int
    temperature: str  # "hot" | "warm" | "cold"
    category: str
    signals: tuple[str, ...]
    actions: tuple[str, ...]
    reply_markup: dict = field(default_factory=dict)


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def categorize(text: str) -> str:
    for name, pattern in _CATEGORY_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return "other"


def reply_draft(category: str, owner_name: str, portfolio_url: str) -> str:
    """A first-person reply the owner can edit and send. Never sent automatically."""
    contact = f"{portfolio_url.rstrip('/')}/contact"
    body = {
        "hiring": (
            "Thanks for reaching out about the role. I'd like to understand it properly before we talk numbers: "
            "what the team is building, what you need this person to own in the first 90 days, and your timeline. "
            "I can do a 20-minute call this week."
        ),
        "pricing": (
            "Thanks for asking. My pricing depends on scope, so the fastest way to a real number is three things: "
            "what you want built, what already exists, and when you need it live. "
            "Send me those and I'll come back with a fixed estimate and a timeline."
        ),
        "project": (
            "Thanks for the details, this is the kind of work I do. To scope it properly I need to know: "
            "the outcome you want, what's already built, your deadline, and who signs off. "
            "A 20-minute call is usually enough to turn that into a plan."
        ),
        "partnership": (
            "Thanks for thinking of me. Tell me a bit more about the format, the audience and the date you have in mind, "
            "and I'll let you know quickly whether I can commit."
        ),
        "support": (
            "Sorry about the trouble, and thanks for flagging it. Could you send me the steps that lead to the problem "
            "and what you expected to happen? I'll look at it as soon as I have that."
        ),
    }.get(
        category,
        "Thanks for your message. Could you tell me a little more about what you need and when you need it? "
        "I'll reply with a concrete next step.",
    )
    return f"Hi,\n\n{body}\n\nYou can also pick a time or send details here: {contact}\n\n{owner_name}"


def _actions(category: str, temperature: str, email: str | None, visitor: VisitorProfile) -> list[str]:
    out: list[str] = []
    if category == "solicitation":
        return ["No reply needed: this reads like a sales pitch. Tap Mark handled."]
    if not email:
        out.append("No email captured: they can only be reached while they are still in the chat. Aegis asked for one.")
    elif temperature == "hot":
        out.append("Reply within 2 hours: reply to this message and it is emailed to them.")
    else:
        out.append("Reply within 24 hours: reply to this message and it is emailed to them.")
    if visitor.kind == "ai_agent":
        out.append("An AI agent wrote this for someone. Keep the reply factual and self-contained: it will be summarised.")
    out.append(
        {
            "hiring": "Ask for the role scope, team and salary band before quoting anything. Attach your CV.",
            "pricing": "Do not quote blind: ask for scope, what exists and the deadline, then send a fixed estimate.",
            "project": "Offer a 20-minute scoping call and ask what is already built.",
            "partnership": "Ask for format, audience and date before committing.",
            "support": "Ask for steps to reproduce and what they expected.",
        }.get(category, "Ask what they need and by when.")
    )
    if email and email.rsplit("@", 1)[-1].lower() not in _FREE_MAIL:
        out.append("Company email: check their site and LinkedIn (buttons below) before you reply.")
    return out


def build_handoff_brief(
    *,
    query: str,
    intent: str,
    session_id: str,
    user_email: str | None,
    history: list[tuple[str, str]] | None = None,
    visitor: VisitorProfile | None = None,
    assistant_name: str = "Aegis",
    now: datetime | None = None,
) -> HandoffBrief:
    """``history`` is a list of (role, text) with role "visitor" or "assistant", oldest first."""
    visitor = visitor or VisitorProfile()
    history = history or []
    visitor_text = " ".join(t for role, t in history if role == "visitor") or query
    category = categorize(f"{visitor_text} {query}")

    score = 20
    signals: list[str] = []
    if user_email:
        score += 25
        signals.append("email captured")
    for pattern, points, label in _SIGNALS:
        if re.search(pattern, visitor_text, re.IGNORECASE):
            score += points
            signals.append(label)
    if category in ("hiring", "project", "pricing"):
        score += 15
        signals.append(f"{_CATEGORY_LABEL[category].lower()} intent")
    visitor_turns = sum(1 for role, _ in history if role == "visitor")
    if visitor_turns >= 3:
        score += 5
        signals.append(f"{visitor_turns} messages in this conversation")
    if visitor.kind == "ai_agent":
        score -= 10
        signals.append("written by an AI agent")
    if category == "solicitation":
        score = min(score, 10)
        signals.append("reads like a sales pitch")
    score = max(0, min(100, score))
    temperature = "hot" if score >= 65 else "warm" if score >= 40 else "cold"

    actions = _actions(category, temperature, user_email, visitor)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    badge = {"hot": "🔥 HOT", "warm": "🟡 WARM", "cold": "⚪ COLD"}[temperature]

    lines = [
        f"{badge} · {_CATEGORY_LABEL[category]} · {score}/100",
        "━━━━━━━━━━━━━━━━",
        f"{'🤖' if visitor.is_automated else '👤'} {visitor.describe()}",
        f"✉️ {user_email or 'no email yet'}",
        f"🕐 {stamp} · session {session_id[:8]} · intent {intent}",
        "",
        "💬 They asked",
        _clip(query, 600),
        "",
        "📌 Why this score",
        *(f"• {s}" for s in signals or ["no strong signals"]),
        "",
        "✅ Do next",
        *(f"{i}. {a}" for i, a in enumerate(actions, 1)),
    ]
    recap = history[-6:]
    if len(recap) > 1:
        lines += ["", "🧵 Conversation"]
        lines += [
            f"{'Visitor' if role == 'visitor' else assistant_name}: {_clip(text, 220)}" for role, text in recap
        ]
    if user_email:
        lines += ["", "↩️ Reply to this message and your text is emailed to them."]
    text = "\n".join(lines)
    if len(text) > TELEGRAM_MAX_MESSAGE_LEN:
        text = text[: TELEGRAM_MAX_MESSAGE_LEN - 14] + "\n…(truncated)"

    rows: list[list[dict]] = []
    if category != "solicitation":
        rows.append([{"text": "✍️ Suggest a reply", "callback_data": f"ho:draft:{category}"}])
    if user_email:
        domain = user_email.rsplit("@", 1)[-1].lower()
        research = [
            {
                "text": "🔎 LinkedIn",
                "url": "https://www.linkedin.com/search/results/all/?keywords=" + quote_plus(user_email.split("@")[0].replace(".", " ") + " " + domain.split(".")[0]),
            }
        ]
        if domain not in _FREE_MAIL and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
            research.insert(0, {"text": f"🌐 {domain}", "url": f"https://{domain}"})
        rows.append(research)
    rows.append([{"text": "✅ Mark handled", "callback_data": "ho:done"}])

    return HandoffBrief(
        text=text,
        score=score,
        temperature=temperature,
        category=category,
        signals=tuple(signals),
        actions=tuple(actions),
        reply_markup={"inline_keyboard": rows},
    )
