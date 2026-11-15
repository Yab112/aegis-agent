"""Send one email. Resend when RESEND_API_KEY and EMAIL_FROM are set, otherwise the existing Gmail API path."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("aegis.mailer")

RESEND_URL = "https://api.resend.com/emails"


@dataclass
class SendResult:
    ok: bool
    provider: str
    provider_id: str | None = None
    error: str | None = None


def resend_config() -> tuple[str, str, str | None] | None:
    key = os.environ.get("RESEND_API_KEY", "").strip()
    sender = os.environ.get("EMAIL_FROM", "").strip()  # e.g. "Yabibal <hello@yabibal.site>" on a verified domain
    if not key or not sender:
        return None
    return key, sender, os.environ.get("EMAIL_REPLY_TO", "").strip() or None


def provider_status() -> dict[str, Any]:
    cfg = resend_config()
    return {
        "provider": "resend" if cfg else "gmail",
        "from": cfg[1] if cfg else None,
        "note": None if cfg else "RESEND_API_KEY and EMAIL_FROM are not both set on the API host, so mail goes out through Gmail.",
    }


def send_email(*, to: str, subject: str, html: str, text: str) -> SendResult:
    cfg = resend_config()
    if cfg:
        key, sender, reply_to = cfg
        payload: dict[str, Any] = {"from": sender, "to": [to], "subject": subject, "html": html, "text": text}
        if reply_to:
            payload["reply_to"] = reply_to
        try:
            r = httpx.post(RESEND_URL, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=20)
        except httpx.HTTPError as exc:
            return SendResult(False, "resend", error=f"Could not reach Resend: {exc}")
        if r.status_code >= 400:
            try:
                message = r.json().get("message") or r.text
            except ValueError:
                message = r.text
            logger.warning("resend refused: %s %s", r.status_code, message[:300])
            return SendResult(False, "resend", error=f"Resend refused the email ({r.status_code}): {message[:300]}")
        return SendResult(True, "resend", provider_id=str(r.json().get("id") or ""))

    from src.tools.calendar_tool import CalendarOAuthError, send_gmail_multipart

    try:
        ok = send_gmail_multipart(to_addr=to, subject=subject, text_body=text, html_body=html)
    except CalendarOAuthError:
        return SendResult(False, "gmail", error="Gmail send failed (OAuth). Refresh GOOGLE_REFRESH_TOKEN on the API host.")
    return SendResult(bool(ok), "gmail", error=None if ok else "Gmail API returned an error; check API logs.")
