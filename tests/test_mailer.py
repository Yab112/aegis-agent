from __future__ import annotations

from types import SimpleNamespace

from src.api.admin_people_routes import build_people
from src.mailer import provider
from src.mailer.layout import render_email, variables_for
from src.mailer.render import body_to_html, body_to_text, fill_variables, missing_variables
from src.mailer.templates import DEFAULT_TEMPLATES

SETTINGS = SimpleNamespace(owner_name="Yabibal", owner_role="Full-Stack & AI Engineer", portfolio_url="https://yabibal.site/", email_brand_logo_url=None)


def test_body_is_escaped_and_formats_blocks():
    html = body_to_html("Hi <b>x</b>\n\n- one\n- **two**\n\n> quoted\n\n## Next\n[site](https://a.io) [bad](javascript:alert(1))\n\n[[Book a call|https://a.io/c]]")
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>" not in html
    assert html.count("<li") == 2 and "<strong>two</strong>" in html and "<h2" in html
    assert 'href="https://a.io"' in html and 'href="javascript' not in html  # unsafe link stays plain text
    assert "Book a call &rarr;" in html and 'href="https://a.io/c"' in html
    assert body_to_text("**Hi** [site](https://a.io)\n\n[[Go|https://b.io]]") == "Hi site (https://a.io)\n\nGo: https://b.io"


def test_variables_fill_and_report_what_is_missing():
    v = variables_for(SETTINGS, "jane.doe@acme.io")
    assert v["first_name"] == "Jane" and v["site"] == "https://yabibal.site"
    assert variables_for(SETTINGS, "x9@acme.io")["first_name"] == "there"
    assert fill_variables("Hi {{first_name}} {{unknown}}", v) == "Hi Jane {{unknown}}"
    assert missing_variables("{{first_name}} {{company}}", v) == ["company"]


def test_layout_wraps_every_default_template():
    for t in DEFAULT_TEMPLATES:
        out = render_email(SETTINGS, subject=t["subject"], body=t["body_md"], variables=variables_for(SETTINGS, "sam@acme.io"))
        assert "{{" not in out["html"] and "{{" not in out["subject"] and "Hi Sam" in out["html"], t["key"]
        assert out["html"].startswith("<!doctype html>") and "Full-Stack &amp; AI Engineer" in out["html"]
        assert "Yabibal" in out["text"] and "<" not in out["text"]


def test_resend_is_used_only_when_key_and_sender_are_set(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert provider.provider_status()["provider"] == "gmail"
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    assert provider.resend_config() is None  # no sender yet
    monkeypatch.setenv("EMAIL_FROM", "Y <hello@yabibal.site>")
    sent = {}

    def fake_post(url, json, headers, timeout):
        sent.update(url=url, json=json, auth=headers["Authorization"])
        return SimpleNamespace(status_code=200, json=lambda: {"id": "abc"}, text="")

    monkeypatch.setattr(provider.httpx, "post", fake_post)
    r = provider.send_email(to="a@b.io", subject="S", html="<p>h</p>", text="t")
    assert r.ok and r.provider == "resend" and r.provider_id == "abc"
    assert sent["json"]["from"] == "Y <hello@yabibal.site>" and sent["json"]["to"] == ["a@b.io"] and sent["auth"] == "Bearer re_test"
    monkeypatch.setattr(provider.httpx, "post", lambda *a, **k: SimpleNamespace(status_code=403, json=lambda: {"message": "domain is not verified"}, text=""))
    bad = provider.send_email(to="a@b.io", subject="S", html="h", text="t")
    assert not bad.ok and "domain is not verified" in bad.error


class _T:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def test_people_are_merged_by_email_across_tables():
    db = {
        "handoff_telegram_alerts": [
            {"visitor_email": "Jane@Acme.io", "user_query": "first", "intent": "hiring", "created_at": "2026-11-01", "status": "replied"},
            {"visitor_email": "jane@acme.io", "user_query": "second", "intent": "hiring", "created_at": "2026-11-10", "status": "open"},
            {"visitor_email": None, "user_query": "anon", "intent": None, "created_at": "2026-11-11", "status": "open"},
        ],
        "leads": [{"email": "bob@x.io", "intent": "pricing", "query": "how much", "captured_at": "2026-10-01"}],
        "sessions": [{"user_email": "jane@acme.io", "updated_at": "2026-11-12"}],
        "sent_emails": [{"to_email": "jane@acme.io", "created_at": "2026-11-13"}],
        "inbox_replies": [],
    }
    out = build_people(SimpleNamespace(table=lambda name: _T(db[name])))
    jane, bob = out["items"]
    assert out["anonymous_conversations"] == 1 and jane["email"] == "jane@acme.io"
    assert jane["conversations"] == 2 and jane["open"] == 1 and jane["last_question"] == "second"
    assert jane["first_seen"] == "2026-11-01" and jane["last_seen"] == "2026-11-12" and jane["emails_sent"] == 1 and jane["chat_sessions"] == 1
    assert bob["intents"] == ["pricing"] and bob["last_question"] == "how much"
