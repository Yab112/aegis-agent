from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.admin_routes as admin_routes
from config.settings import get_settings
from src.admin import inbox
from src.telegram_bot import commands
from src.telegram_bot.notify import post_written_text


class _Res:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class _Table:
    """Just enough of the Supabase query builder for the admin routes."""

    def __init__(self, db, name):
        self.db, self.name = db, name
        self.rows = db.setdefault(name, [])
        self.filters, self.op, self.payload, self._limit = [], "select", None, None

    def select(self, *_a, **_k):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, k, v):
        self.filters.append(lambda r, k=k, v=v: str(r.get(k)) == str(v))
        return self

    def in_(self, k, vals):
        self.filters.append(lambda r, k=k, vals=vals: r.get(k) in vals)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        hit = [r for r in self.rows if all(f(r) for f in self.filters)]
        if self.op == "insert":
            row = {"id": f"id-{len(self.rows) + 1}", **self.payload}
            self.rows.append(row)
            return _Res([row])
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return _Res(hit)
        if self.op == "delete":
            for r in hit:
                self.rows.remove(r)
            return _Res(hit)
        return _Res(hit[: self._limit] if self._limit else hit, count=len(hit))


class _Client:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Table(self.db, name)


def _app(monkeypatch, db):
    monkeypatch.setattr(inbox, "sb", lambda _s: _Client(db))
    app = FastAPI()
    app.include_router(admin_routes.router)
    app.include_router(admin_routes.public_router)
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {get_settings().api_secret_key}"}


def test_admin_requires_secret(monkeypatch):
    client = _app(monkeypatch, {})
    assert client.get("/admin/posts").status_code == 401
    assert client.get("/admin/posts", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/admin/posts", headers=_auth()).status_code == 200


def test_publish_sets_date_once_and_validates_status(monkeypatch):
    db = {"blog_posts": [{"id": "p1", "slug": "a", "title": "A", "status": "review", "published_at": None}]}
    client = _app(monkeypatch, db)
    monkeypatch.setattr("src.blog.publish_webhook.notify_blog_post_published", lambda **_k: None)
    assert client.patch("/admin/posts/p1", json={"status": "live"}, headers=_auth()).status_code == 422
    r = client.patch("/admin/posts/p1", json={"status": "published", "tags": [" AI ", ""]}, headers=_auth())
    assert r.status_code == 200 and db["blog_posts"][0]["published_at"] and db["blog_posts"][0]["tags"] == ["ai"]
    first = db["blog_posts"][0]["published_at"]
    client.patch("/admin/posts/p1", json={"status": "draft"}, headers=_auth())
    client.patch("/admin/posts/p1", json={"status": "published"}, headers=_auth())
    assert db["blog_posts"][0]["published_at"] == first


def test_public_projects_hide_private_fields(monkeypatch):
    db = {"projects": [
        {"id": "1", "slug": "shown", "title": "Shown", "visible": True, "created_at": "x"},
        {"id": "2", "slug": "hidden", "title": "Secret", "description": "private", "visible": False},
    ]}
    client = _app(monkeypatch, db)
    items = client.get("/projects").json()["items"]
    assert items == [{"slug": "shown", "title": "Shown", "visible": True}, {"slug": "hidden", "visible": False}]


def test_reply_needs_visitor_email(monkeypatch):
    db = {"handoff_telegram_alerts": [{"id": "a1", "session_id": "s", "visitor_email": None, "user_query": "price?"}]}
    client = _app(monkeypatch, db)
    r = client.post("/admin/inbox/a1/reply", json={"text": "hello"}, headers=_auth())
    assert r.status_code == 422 and "email" in r.json()["detail"]
    detail = client.get("/admin/inbox/a1", headers=_auth()).json()
    assert detail["suggested_reply"] and detail["alert"]["status"] == "open"


def test_telegram_commands(monkeypatch):
    sent = []
    monkeypatch.setattr(commands, "telegram_call", lambda method, payload: sent.append(payload) or True)
    db = {
        "handoff_telegram_alerts": [
            {"id": "abcdef12", "status": "open", "visitor_email": "v@x.io", "user_query": "Need a quote", "created_at": None}
        ],
        "blog_posts": [{"id": "p1", "slug": "a", "title": "A post", "status": "review", "created_at": None}],
    }
    monkeypatch.setattr(inbox, "sb", lambda _s: _Client(db))
    s = get_settings()
    assert commands.handle_command(s, "1", "hello") is False
    assert commands.handle_command(s, "1", "/inbox") and "v@x.io" in sent[-1]["text"]
    commands.handle_command(s, "1", "/posts")
    assert sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "bp:pub:p1"
    commands.handle_command(s, "1", "/idea Why queues beat cron for agents")
    assert db["blog_ideas"][0]["source"] == "manual"
    commands.handle_command(s, "1", "/nonsense")
    assert "Unknown command" in sent[-1]["text"]


def test_post_written_message_reports_checks():
    text = post_written_text({
        "title": "T", "post_status": "review", "words": 1800, "sources": 6, "research_grounded": True,
        "quality_gate_passed": True, "rich": {"charts": 2, "tables": 1, "links_unlinked": 3},
    })
    assert "2 charts" in text and "1 table" in text and "3 unverified link(s) removed" in text
