"""
Visitor follow-up email: Gemini polishes owner's Telegram draft + branded HTML shell.
"""
from __future__ import annotations

import html
import logging
import re
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger("aegis.visitor_reply_email")

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    import google.generativeai as genai


def _gemini_response_text(resp: object) -> str:
    """Best-effort text from GenerateContentResponse (handles empty / blocked)."""
    try:
        t = getattr(resp, "text", None)
        if isinstance(t, str) and t.strip():
            return t.strip()
    except Exception:
        pass
    candidates = getattr(resp, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if isinstance(t, str) and t.strip():
                return t.strip()
    return ""


_POLISH_PROMPT = """You are editing an email BODY for a portfolio site visitor.

Context — they previously wrote (visitor message):
\"\"\"{visitor_question}\"\"\"

Handoff intent tag (routing only): {intent}

The business owner drafted this quick reply on Telegram (preserve every fact; do not invent details):
\"\"\"{owner_draft}\"\"\"

Rewrite as 2–5 short paragraphs of professional, warm email body text only.
Rules:
- Plain sentences only. No subject line. No "Dear Sir/Madam". You may start with "Hi," or similar if natural.
- No HTML tags. No markdown. No bullet lists unless essential.
- Keep numbers, dates, links, and names exactly as given.
- If the draft is already fine, lightly tighten wording only.
"""


def polish_owner_reply_for_visitor_email(
    *,
    settings: "Settings",
    owner_draft: str,
    visitor_question: str,
    intent: str | None,
) -> str:
    """Return polished plain-text email body; on failure returns stripped owner_draft."""
    raw = (owner_draft or "").strip()
    if not raw:
        return ""
    if not getattr(settings, "visitor_reply_email_polish", True):
        return raw
    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        prompt = _POLISH_PROMPT.format(
            visitor_question=(visitor_question or "").strip()[:2000],
            intent=(intent or "handoff").strip(),
            owner_draft=raw[:4000],
        )
        resp = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.35,
                "max_output_tokens": 900,
            },
        )
        text = _gemini_response_text(resp)
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        return text or raw
    except Exception as e:
        logger.warning("visitor reply polish failed, using raw draft: %s", e)
        return raw


def _paragraphs_to_html(plain: str) -> str:
    blocks = [p.strip() for p in re.split(r"\n\s*\n+", (plain or "").strip()) if p.strip()]
    if not blocks:
        blocks = [(plain or "").strip() or " "]
    parts = []
    for b in blocks:
        for line in b.split("\n"):
            line = line.strip()
            if line:
                parts.append(f"<p style=\"margin:0 0 12px 0;line-height:1.55;\">{html.escape(line)}</p>")
    return "\n".join(parts) if parts else "<p></p>"


def build_branded_visitor_email_html(
    *,
    settings: "Settings",
    polished_plain_body: str,
    session_short: str,
) -> str:
    """Single-column HTML email with optional logo header and owner footer."""
    logo = (getattr(settings, "email_brand_logo_url", None) or "").strip()
    logo_block = ""
    if logo:
        logo_block = (
            f'<img src="{html.escape(logo, quote=True)}" alt="{html.escape(settings.owner_name, quote=True)}" '
            'style="max-height:52px;display:block;margin:0 auto 8px auto;" />'
        )
    else:
        logo_block = (
            f'<p style="margin:0;font-size:20px;font-weight:600;color:#111;">'
            f"{html.escape(settings.owner_name)}</p>"
        )

    body_html = _paragraphs_to_html(polished_plain_body)

    footer = (
        f"<p style=\"margin:16px 0 0 0;font-size:13px;color:#555;line-height:1.5;\">"
        f"<strong>{html.escape(settings.owner_name)}</strong><br />"
        f"{html.escape(settings.owner_role)}<br />"
        f'<a href="{html.escape(settings.portfolio_url, quote=True)}" style="color:#2563eb;">'
        f"{html.escape(settings.portfolio_url)}</a><br />"
        f"<span style=\"color:#888;\">Ref: session {html.escape(session_short, quote=True)} · "
        f"{html.escape(settings.assistant_name, quote=True)}</span></p>"
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width" /></head>
<body style="margin:0;padding:0;background:#f4f6f8;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f6f8;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" style="max-width:600px;background:#ffffff;border-radius:12px;
        box-shadow:0 1px 3px rgba(0,0,0,.08);overflow:hidden;" cellspacing="0" cellpadding="0">
        <tr><td style="padding:24px 28px 8px 28px;text-align:center;border-bottom:1px solid #e8ecf0;">
          {logo_block}
        </td></tr>
        <tr><td style="padding:24px 28px 8px 28px;font-family:Segoe UI,system-ui,sans-serif;font-size:15px;color:#222;">
          {body_html}
        </td></tr>
        <tr><td style="padding:8px 28px 24px 28px;font-family:Segoe UI,system-ui,sans-serif;border-top:1px solid #e8ecf0;">
          {footer}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def build_branded_visitor_email_plain(
    *,
    settings: "Settings",
    polished_plain_body: str,
    session_short: str,
) -> str:
    """Plain-text alternative for multipart email."""
    lines = [
        polished_plain_body.strip(),
        "",
        "—",
        f"{settings.owner_name}",
        settings.owner_role,
        settings.portfolio_url,
        f"Ref: session {session_short} · {settings.assistant_name}",
    ]
    return "\n".join(lines)
