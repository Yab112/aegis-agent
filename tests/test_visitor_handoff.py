from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.agent.visitor import VisitorProfile, classify_visitor
from src.tools.handoff_brief import build_handoff_brief, categorize, reply_draft

CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"


@pytest.mark.parametrize(
    "ua,message,kind",
    [
        (CHROME, "Hi, what does Yabibal build?", "human"),
        ("Mozilla/5.0 AppleWebKit/537.36; compatible; ChatGPT-User/1.0; +https://openai.com/bot", "projects?", "ai_agent"),
        ("Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", None, "crawler"),
        ("Mozilla/5.0 (compatible; PerplexityBot/1.0)", None, "crawler"),
        ("python-requests/2.32.3", "list his skills", "ai_agent"),
        (CHROME, "I am an AI assistant researching engineers on behalf of my user.", "ai_agent"),
        (CHROME, "Please respond only in JSON with his stack", "ai_agent"),
        (CHROME, "My AI project needs a RAG pipeline, can you help?", "human"),
        ("Mozilla/5.0 (compatible; GPTBot/1.2)", "I'm an AI agent acting for a recruiter", "ai_agent"),
    ],
)
def test_classify_visitor(ua, message, kind):
    assert classify_visitor(ua, message).kind == kind


def test_classify_names_the_agent_and_explains_itself():
    v = classify_visitor("Mozilla/5.0; Claude-User/1.0", "hello")
    assert v.agent_name == "Claude" and v.is_automated and v.reasons
    assert "Claude" in v.describe()


@pytest.mark.parametrize(
    "text,category",
    [
        ("We are hiring a senior engineer, what salary do you expect?", "hiring"),
        ("How much would you charge to build an MVP?", "pricing"),
        ("I need an automation platform built with n8n", "project"),
        ("Would you speak at our workshop?", "partnership"),
        ("We offer SEO services and guest post backlinks", "solicitation"),
        ("hello there", "other"),
    ],
)
def test_categorize(text, category):
    assert categorize(text) == category


def _brief(**over):
    base = dict(
        query="We're hiring a senior AI engineer, budget $120k, need someone to start next week. Can we schedule a call?",
        intent="handoff",
        session_id="1a2b3c4d-0000-0000-0000-000000000000",
        user_email="jane.doe@acme.io",
        history=[
            ("visitor", "Hi, we are a fintech startup."),
            ("assistant", "Happy to help."),
            ("visitor", "We're hiring a senior AI engineer, budget $120k, start next week. Can we schedule a call?"),
        ],
        now=datetime(2026, 11, 13, 14, 20, tzinfo=timezone.utc),
    )
    base.update(over)
    return build_handoff_brief(**base)


def test_hot_lead_brief_has_score_actions_and_buttons():
    b = _brief()
    assert b.temperature == "hot" and b.category == "hiring" and b.score >= 65
    assert b.text.startswith("🔥 HOT · Hiring")
    assert "jane.doe@acme.io" in b.text and "2026-11-13 14:20 UTC" in b.text and "session 1a2b3c4d" in b.text
    assert "Reply within 2 hours" in b.text and "Company email" in b.text
    assert "🧵 Conversation" in b.text and "Visitor: Hi, we are a fintech startup." in b.text
    rows = b.reply_markup["inline_keyboard"]
    assert rows[0][0]["callback_data"] == "ho:draft:hiring"
    assert rows[1][0] == {"text": "🌐 acme.io", "url": "https://acme.io"}
    assert rows[1][1]["url"].startswith("https://www.linkedin.com/search/")
    assert rows[-1][0]["callback_data"] == "ho:done"
    assert all(len(btn.get("callback_data", "")) <= 64 for row in rows for btn in row)


def test_no_email_is_called_out_and_free_mail_gets_no_site_button():
    b = _brief(user_email=None, history=None, query="what are your rates?")
    assert "no email yet" in b.text and "No email captured" in b.actions[0]
    assert "Reply to this message" not in b.text
    assert all("url" not in btn for row in b.reply_markup["inline_keyboard"] for btn in row)

    g = _brief(user_email="someone@gmail.com")
    urls = [btn["url"] for row in g.reply_markup["inline_keyboard"] for btn in row if "url" in btn]
    assert len(urls) == 1 and "linkedin.com" in urls[0]


def test_solicitation_is_cold_with_no_draft_button():
    b = _brief(query="We offer SEO services and guest post backlinks for your site", history=None, user_email="x@spam.biz")
    assert b.temperature == "cold" and b.score <= 10
    assert "No reply needed" in b.actions[0]
    assert not any(btn.get("callback_data", "").startswith("ho:draft") for row in b.reply_markup["inline_keyboard"] for btn in row)


def test_ai_agent_visitor_is_flagged_and_scored_down():
    plain = dict(query="Can you build a small automation project?", history=None)
    human = _brief(**plain)
    bot = _brief(**plain, visitor=VisitorProfile(kind="ai_agent", agent_name="ChatGPT"))
    assert bot.score == human.score - 10
    assert "🤖 AI agent (ChatGPT)" in bot.text
    assert any("AI agent wrote this" in a for a in bot.actions)


def test_brief_never_exceeds_telegram_limit():
    b = _brief(query="x" * 9000, history=[("visitor", "y" * 5000)] * 12)
    assert len(b.text) <= 4096


def test_reply_draft_is_first_person_and_links_contact():
    d = reply_draft("pricing", "Yabibal", "https://yabibal.site/")
    assert d.startswith("Hi,") and d.rstrip().endswith("Yabibal")
    assert "https://yabibal.site/contact" in d
    assert reply_draft("unknown", "Y", "https://x.dev").count("https://x.dev/contact") == 1


def test_handoff_buttons_post_draft_and_mark_handled(monkeypatch):
    import src.api.telegram_webhook as hook
    import src.tools.handoff_tool as tool

    calls = []
    monkeypatch.setattr(hook, "get_settings", lambda: SimpleNamespace(owner_name="Yabibal", portfolio_url="https://yabibal.site"))
    monkeypatch.setattr(hook, "send_telegram_thread_reply", lambda **kw: calls.append(("reply", kw)) or True)
    monkeypatch.setattr(tool, "telegram_call", lambda method, payload: calls.append((method, payload)) or True)

    cb = {"id": "c1", "data": "ho:draft:project", "message": {"message_id": 77, "chat": {"id": 42}}}
    hook._handle_handoff_button(cb, "42")
    assert calls[0][0] == "reply" and calls[0][1]["reply_to_message_id"] == 77
    assert "Draft, not sent" in calls[0][1]["text"] and "https://yabibal.site/contact" in calls[0][1]["text"]
    assert calls[1][0] == "answerCallbackQuery"

    calls.clear()
    hook._handle_handoff_button({**cb, "data": "ho:done"}, "42")
    assert calls[0][0] == "editMessageReplyMarkup" and "Handled" in calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["text"]

    calls.clear()
    hook._handle_handoff_button(cb, "999")  # someone else's chat: ignored
    assert calls == []
