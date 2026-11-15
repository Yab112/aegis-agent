"""Admin: the people who contacted you (one row per email), and a read-only look at every table. Secret-protected."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from config.settings import Settings, get_settings
from src.admin import inbox
from src.api.blog_internal import verify_internal_secret

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_internal_secret)])
SettingsDep = Annotated[Settings, Depends(get_settings)]

# table → (what it holds, column to sort newest-first by). Only these can be browsed.
TABLES: dict[str, tuple[str, str | None]] = {
    "leads": ("Visitors who left an email in the chat", "captured_at"),
    "handoff_telegram_alerts": ("Conversations the site agent handed to you", "created_at"),
    "sessions": ("Chat sessions with the site agent", "updated_at"),
    "inbox_replies": ("Replies you sent from the inbox or Telegram", "created_at"),
    "sent_emails": ("Emails sent from the admin", "created_at"),
    "email_templates": ("Your email templates", "updated_at"),
    "blog_posts": ("Blog posts in every status", "created_at"),
    "blog_ideas": ("The old idea queue", "created_at"),
    "blog_pipeline_failures": ("Writer steps that failed", "created_at"),
    "keywords": ("Scored keywords", "updated_at"),
    "news_sources": ("Trusted sources", None),
    "news_items": ("Articles collected from sources", "published_at"),
    "projects": ("Project edits made in the admin", "updated_at"),
    "scheduler_jobs": ("Scheduled jobs and their state", None),
    "scheduler_job_runs": ("Every job run", "started_at"),
    "documents": ("Knowledge the chat agent answers from", "created_at"),
}
HIDDEN_COLUMNS = {"embedding"}  # large and meaningless to read


def _rows(client: Any, table: str, cols: str, limit: int = 1000) -> list[dict[str, Any]]:
    try:
        return client.table(table).select(cols).limit(limit).execute().data or []
    except Exception as exc:
        if inbox.is_missing_schema(exc):
            return []
        raise


def _person(people: dict[str, dict[str, Any]], email: Any) -> dict[str, Any] | None:
    if not inbox.valid_email(email):
        return None
    key = str(email).strip().lower()
    return people.setdefault(
        key,
        {"email": key, "first_seen": None, "last_seen": None, "conversations": 0, "chat_sessions": 0, "emails_sent": 0, "intents": [], "last_question": "", "open": 0},
    )


def _seen(p: dict[str, Any], ts: Any) -> None:
    if not ts:
        return
    ts = str(ts)
    p["first_seen"] = min(p["first_seen"] or ts, ts)
    p["last_seen"] = max(p["last_seen"] or ts, ts)


def build_people(client: Any) -> dict[str, Any]:
    """Everyone with an email, merged across leads, handoffs, chat sessions and mail you sent."""
    people: dict[str, dict[str, Any]] = {}
    anonymous = 0
    try:
        alerts = client.table("handoff_telegram_alerts").select("visitor_email,user_query,intent,created_at,status").limit(1000).execute().data or []
    except Exception as exc:
        if not inbox.is_missing_schema(exc):
            raise
        alerts = [{**r, "status": "open"} for r in _rows(client, "handoff_telegram_alerts", "visitor_email,user_query,intent,created_at")]
    for a in sorted(alerts, key=lambda r: str(r.get("created_at") or "")):
        p = _person(people, a.get("visitor_email"))
        if not p:
            anonymous += 1
            continue
        p["conversations"] += 1
        p["open"] += 1 if a.get("status") == "open" else 0
        p["last_question"] = str(a.get("user_query") or "")[:300]
        if a.get("intent") and a["intent"] not in p["intents"]:
            p["intents"].append(a["intent"])
        _seen(p, a.get("created_at"))
    for lead in _rows(client, "leads", "email,intent,query,captured_at"):
        p = _person(people, lead.get("email"))
        if p:
            if lead.get("intent") and lead["intent"] not in p["intents"]:
                p["intents"].append(lead["intent"])
            p["last_question"] = p["last_question"] or str(lead.get("query") or "")[:300]
            _seen(p, lead.get("captured_at"))
    for s in _rows(client, "sessions", "user_email,updated_at"):
        p = _person(people, s.get("user_email"))
        if p:
            p["chat_sessions"] += 1
            _seen(p, s.get("updated_at"))
    for table, col in (("sent_emails", "to_email"), ("inbox_replies", "to_email")):
        for m in _rows(client, table, f"{col},created_at"):
            p = _person(people, m.get(col))
            if p:
                p["emails_sent"] += 1
                p["last_emailed"] = max(p.get("last_emailed") or "", str(m.get("created_at") or ""))
    items = sorted(people.values(), key=lambda p: str(p["last_seen"] or ""), reverse=True)
    return {"items": items, "anonymous_conversations": anonymous}


@router.get("/people", summary="Everyone who left an email, newest first")
def people(settings: SettingsDep) -> dict[str, Any]:
    return build_people(inbox.sb(settings))


@router.get("/people/{email}", summary="One person's conversations, chat sessions and the mail you sent them")
def person(email: str, settings: SettingsDep) -> dict[str, Any]:
    client = inbox.sb(settings)
    key = email.strip().lower()
    alerts = [a for a in inbox.list_alerts(settings, limit=500) if str(a.get("visitor_email") or "").lower() == key]
    sessions = client.table("sessions").select("id,updated_at,messages").ilike("user_email", key).order("updated_at", desc=True).limit(10).execute().data or []
    mail: list[dict[str, Any]] = []
    for table, cols in (("sent_emails", "created_at,subject,body_md,status,provider"), ("inbox_replies", "created_at,body,sent_body,delivered,channel")):
        try:
            mail += client.table(table).select(cols).eq("to_email", key).order("created_at", desc=True).limit(30).execute().data or []
        except Exception as exc:
            if not inbox.is_missing_schema(exc):
                raise
    mail.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)
    return {
        "email": key,
        "conversations": alerts,
        "sessions": [{"id": s["id"], "updated_at": s.get("updated_at"), "messages": (s.get("messages") or [])[-12:]} for s in sessions],
        "mail": mail,
    }


@router.get("/data", summary="Every table with its row count")
def data_tables(settings: SettingsDep) -> dict[str, Any]:
    client = inbox.sb(settings)
    out = []
    for name, (about, _order) in TABLES.items():
        try:
            count = client.table(name).select("*", count="exact").limit(1).execute().count or 0
        except Exception:
            count = None  # table not created yet
        out.append({"table": name, "about": about, "rows": count})
    return {"items": out}


@router.get("/data/{table}", summary="Rows of one table, newest first (read-only)")
def data_rows(table: str, settings: SettingsDep, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if table not in TABLES:
        raise HTTPException(404, detail="Unknown table")
    limit, offset = min(max(limit, 1), 200), max(offset, 0)
    q = inbox.sb(settings).table(table).select("*", count="exact")
    order = TABLES[table][1]
    if order:
        q = q.order(order, desc=True)
    try:
        res = q.range(offset, offset + limit - 1).execute()
    except Exception as exc:
        if inbox.is_missing_schema(exc):
            raise HTTPException(503, detail=f"The table {table} has not been created yet.") from exc
        raise
    rows = []
    for r in res.data or []:
        clean: dict[str, Any] = {}
        for k, v in r.items():
            if k in HIDDEN_COLUMNS:
                continue
            if isinstance(v, str) and len(v) > 400:
                v = v[:400] + "…"
            elif isinstance(v, (dict, list)):
                text = str(v)
                v = text if len(text) <= 400 else text[:400] + "…"
            clean[k] = v
        rows.append(clean)
    columns = list(rows[0].keys()) if rows else []
    return {"table": table, "about": TABLES[table][0], "total": res.count or 0, "columns": columns, "rows": rows}
