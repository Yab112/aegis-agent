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
- Do not add a closing sign-off with the owner name (no "Best, …" / "Regards, …"); the email footer already includes their name.
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
    """Turn plain body into paragraphs; double newline = extra vertical space."""
    blocks = [p.strip() for p in re.split(r"\n\s*\n+", (plain or "").strip()) if p.strip()]
    if not blocks:
        blocks = [(plain or "").strip() or " "]
    font = (
        "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
        "Arial,sans-serif"
    )
    parts: list[str] = []
    for bi, b in enumerate(blocks):
        inner: list[str] = []
        for line in b.split("\n"):
            line = line.strip()
            if line:
                inner.append(
                    f'<p style="margin:0 0 14px 0;font-family:{font};font-size:16px;'
                    f"line-height:1.65;color:#1e293b;\">{html.escape(line)}</p>"
                )
        if inner:
            mb = "22px" if bi < len(blocks) - 1 else "0"
            parts.append(
                f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
                f'style="margin:0 0 {mb} 0;"><tr><td style="padding:0 0 0 16px;border-left:3px solid #7c3aed;">'
                + "".join(inner)
                + "</td></tr></table>"
            )
    return "\n".join(parts) if parts else "<p></p>"


def build_branded_visitor_email_html(
    *,
    settings: "Settings",
    polished_plain_body: str,
    session_short: str,
) -> str:
    """Table-based HTML email: purple accent, logo, readable body, CTA-style footer."""
    logo = (getattr(settings, "email_brand_logo_url", None) or "").strip()
    name = html.escape(settings.owner_name)
    role = html.escape(settings.owner_role)
    site = html.escape(settings.portfolio_url, quote=True)
    assistant = html.escape(settings.assistant_name, quote=True)
    sess = html.escape(session_short, quote=True)

    font = (
        "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
        "Arial,sans-serif"
    )

    if logo:
        logo_esc = html.escape(logo, quote=True)
        logo_block = f"""<img src="{logo_esc}" alt="{name}" width="140" height="auto" style="display:block;margin:0 auto 14px auto;max-width:180px;height:auto;border:0;" />"""
    else:
        logo_block = f"""<p style="margin:0 0 6px 0;font-family:{font};font-size:22px;font-weight:700;color:#4c1d95;letter-spacing:-0.02em;">{name}</p>"""

    body_html = _paragraphs_to_html(polished_plain_body)

    # Bulletproof button + footer (Gmail-friendly).
    footer = f"""
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:8px 0 0 0;">
  <tr>
    <td style="border-radius:8px;background-color:#5b21b6;background-image:linear-gradient(135deg,#6d28d9 0%,#4c1d95 100%);" bgcolor="#5b21b6">
      <a href="{site}" target="_blank" rel="noopener noreferrer" style="display:inline-block;padding:14px 28px;font-family:{font};font-size:14px;font-weight:600;color:#ffffff;text-decoration:none;">View portfolio</a>
    </td>
  </tr>
</table>
<p style="margin:22px 0 0 0;font-family:{font};font-size:15px;line-height:1.5;color:#334155;">
  <strong style="color:#4c1d95;">{name}</strong><br />
  <span style="color:#64748b;font-size:14px;">{role}</span>
</p>
<p style="margin:14px 0 0 0;font-family:{font};font-size:12px;line-height:1.5;color:#94a3b8;">
  Ref · session {sess} · {assistant}<br />
  You received this because you reached out via the site.
</p>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="color-scheme" content="light" />
  <meta name="supported-color-schemes" content="light" />
  <title>Message from {name}</title>
</head>
<body style="margin:0;padding:0;background-color:#ede9fe;">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{name} — reply to your inquiry</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#ede9fe;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;">
          <tr>
            <td style="border-radius:16px 16px 0 0;overflow:hidden;background-color:#ffffff;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td height="5" style="font-size:5px;line-height:5px;background-color:#7c3aed;background-image:linear-gradient(90deg,#8b5cf6 0%,#5b21b6 50%,#4c1d95 100%);">&nbsp;</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="background-color:#faf5ff;border-left:1px solid #e9d5ff;border-right:1px solid #e9d5ff;padding:28px 32px 24px 32px;text-align:center;">
              {logo_block}
              <p style="margin:0;font-family:{font};font-size:11px;font-weight:600;letter-spacing:0.14em;text-transform:uppercase;color:#7c3aed;">Personal reply</p>
            </td>
          </tr>
          <tr>
            <td style="background-color:#ffffff;border-left:1px solid #e9d5ff;border-right:1px solid #e9d5ff;padding:8px 32px 36px 32px;">
              {body_html}
            </td>
          </tr>
          <tr>
            <td style="background-color:#fafafa;border:1px solid #e9d5ff;border-top:1px solid #ede9fe;padding:28px 32px 32px 32px;border-radius:0 0 16px 16px;">
              {footer}
            </td>
          </tr>
        </table>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;">
          <tr>
            <td style="padding:16px 8px 0 8px;text-align:center;font-family:{font};font-size:11px;color:#78716c;">
              Sent on behalf of {name}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


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
        "────────────────────────────",
        settings.owner_name,
        settings.owner_role,
        settings.portfolio_url,
        "",
        f"Ref · session {session_short} · {settings.assistant_name}",
    ]
    return "\n".join(lines)
