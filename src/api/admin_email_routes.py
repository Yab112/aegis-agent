"""Admin: compose and send email with reusable templates; every send is logged. Secret-protected."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from config.settings import Settings, get_settings
from src.admin import inbox
from src.api.blog_internal import verify_internal_secret
from src.mailer import templates
from src.mailer.layout import render_email, variables_for
from src.mailer.provider import provider_status, send_email
from src.mailer.render import missing_variables

router = APIRouter(prefix="/admin/email", tags=["admin"], dependencies=[Depends(verify_internal_secret)])
SettingsDep = Annotated[Settings, Depends(get_settings)]
HINT = "Run scripts/supabase_email.sql in Supabase, then retry."


class Draft(BaseModel):
    to: str = Field(default="", max_length=254)
    first_name: str | None = Field(default=None, max_length=60)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    template_key: str | None = None


class TemplateBody(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    subject: str = Field(default="", max_length=200)
    body_md: str = Field(min_length=1, max_length=20000)


def _guard(exc: Exception) -> HTTPException:
    return HTTPException(503, detail=HINT) if inbox.is_missing_schema(exc) else HTTPException(500, detail=str(exc)[:400])


@router.get("/status", summary="Which provider sends mail, and from what address")
def status() -> dict[str, Any]:
    return provider_status()


@router.get("/templates")
def template_list(settings: SettingsDep) -> dict[str, Any]:
    try:
        return {"items": templates.list_templates(inbox.sb(settings))}
    except Exception as exc:
        raise _guard(exc) from exc


@router.post("/templates", status_code=201)
def template_create(body: TemplateBody, settings: SettingsDep) -> dict[str, Any]:
    key = "".join(c if c.isalnum() else "-" for c in body.name.lower()).strip("-")[:50] or "template"
    try:
        return (inbox.sb(settings).table("email_templates").insert({"key": key, **body.model_dump()}).execute().data or [{}])[0]
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            raise HTTPException(409, detail="A template with this name already exists") from exc
        raise _guard(exc) from exc


@router.patch("/templates/{template_id}")
def template_update(template_id: str, body: TemplateBody, settings: SettingsDep) -> dict[str, Any]:
    rows = inbox.sb(settings).table("email_templates").update({**body.model_dump(), "updated_at": "now()"}).eq("id", template_id).execute().data or []
    if not rows:
        raise HTTPException(404, detail="Template not found")
    return rows[0]


@router.delete("/templates/{template_id}")
def template_delete(template_id: str, settings: SettingsDep) -> dict[str, bool]:
    inbox.sb(settings).table("email_templates").delete().eq("id", template_id).execute()
    return {"ok": True}


@router.post("/preview", summary="Render the email exactly as it will be sent")
def preview(body: Draft, settings: SettingsDep) -> dict[str, Any]:
    to = body.to.strip() or "someone@example.com"
    variables = variables_for(settings, to, body.first_name)
    out = render_email(settings, subject=body.subject, body=body.body, variables=variables)
    return {"subject": out["subject"], "html": out["html"], "unfilled": missing_variables(body.subject + "\n" + body.body, variables)}


@router.post("/send")
def send(body: Draft, settings: SettingsDep) -> dict[str, Any]:
    to = body.to.strip()
    if not inbox.valid_email(to):
        raise HTTPException(422, detail="Enter a valid recipient email address.")
    variables = variables_for(settings, to, body.first_name)
    unfilled = missing_variables(body.subject + "\n" + body.body, variables)
    if unfilled:
        raise HTTPException(422, detail="Fill in or remove: " + ", ".join("{{" + v + "}}" for v in unfilled))
    out = render_email(settings, subject=body.subject, body=body.body, variables=variables)
    result = send_email(to=to, subject=out["subject"], html=out["html"], text=out["text"])
    try:  # the log must never hide the result of the send itself
        inbox.sb(settings).table("sent_emails").insert(
            {
                "to_email": to,
                "subject": out["subject"],
                "body_md": body.body,
                "template_key": body.template_key,
                "provider": result.provider,
                "provider_id": result.provider_id,
                "status": "sent" if result.ok else "failed",
                "error": result.error,
            }
        ).execute()
    except Exception:
        pass
    if not result.ok:
        raise HTTPException(502, detail=result.error or "The email could not be sent.")
    return {"ok": True, "detail": f"Sent to {to} through {result.provider}.", "provider": result.provider}


@router.get("/sent")
def sent_log(settings: SettingsDep, to: str | None = None, limit: int = 50) -> dict[str, Any]:
    try:
        q = inbox.sb(settings).table("sent_emails").select("*")
        if to:
            q = q.eq("to_email", to)
        return {"items": q.order("created_at", desc=True).limit(min(max(limit, 1), 200)).execute().data or []}
    except Exception as exc:
        raise _guard(exc) from exc
