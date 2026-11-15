"""The fixed shell around every email: header, body, signature, footer. Table layout and inline styles for mail clients."""
from __future__ import annotations

import html
from typing import Any

from src.mailer.render import GREEN, INK, MUTED, PURPLE, body_to_html, body_to_text, fill_variables


def variables_for(settings: Any, to_email: str, first_name: str | None = None) -> dict[str, str]:
    guess = (to_email.split("@")[0].replace(".", " ").replace("_", " ").split() or [""])[0]
    return {
        "first_name": (first_name or "").strip() or (guess.capitalize() if guess.isalpha() and len(guess) > 2 else "there"),
        "owner_name": settings.owner_name,
        "site": str(settings.portfolio_url).rstrip("/"),
        "email": to_email,
    }


def render_email(settings: Any, *, subject: str, body: str, variables: dict[str, str]) -> dict[str, str]:
    """Return {subject, html, text} with variables filled in."""
    subject = fill_variables(subject, variables).strip()
    body = fill_variables(body, variables)
    site = str(settings.portfolio_url).rstrip("/")
    host = site.split("//")[-1]
    logo = getattr(settings, "email_brand_logo_url", None)
    owner = html.escape(settings.owner_name)
    role = html.escape(settings.owner_role)
    header = (
        f'<img src="{html.escape(logo, quote=True)}" alt="{owner}" height="34" style="display:block;height:34px;border:0;">'
        if logo
        else f'<span style="font-size:20px;font-weight:800;letter-spacing:0.04em;color:{INK};text-transform:uppercase;">{owner}</span>'
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4efe6;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(body_to_text(body)[:110])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4efe6;"><tr><td align="center" style="padding:28px 14px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#fffcf5;border:1px solid #cfc4b6;">
    <tr><td style="height:4px;background:{PURPLE};font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td style="padding:22px 30px 18px;border-bottom:1px solid #e3dacd;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td>{header}</td>
        <td align="right" style="font-family:'SF Mono',Menlo,Consolas,monospace;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;color:{MUTED};">{role}</td>
      </tr></table>
    </td></tr>
    <tr><td style="padding:30px 30px 8px;">
{body_to_html(body)}
    </td></tr>
    <tr><td style="padding:6px 30px 28px;">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="border-left:3px solid {GREEN};padding-left:12px;">
          <div style="font-size:15px;font-weight:600;color:{INK};">{owner}</div>
          <div style="font-size:13px;color:{MUTED};">{role} &middot; <a href="{html.escape(site, quote=True)}" style="color:{PURPLE};text-decoration:none;">{html.escape(host)}</a></div>
        </td>
      </tr></table>
    </td></tr>
    <tr><td style="padding:16px 30px;background:#f3efe6;border-top:1px solid #e3dacd;font-size:12px;line-height:1.5;color:{MUTED};">
      You are receiving this because you contacted {owner} through <a href="{html.escape(site, quote=True)}" style="color:{MUTED};">{html.escape(host)}</a>. Reply to this email to continue the conversation.
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""
    text = f"{body_to_text(body)}\n\n{settings.owner_name}\n{settings.owner_role} · {site}\n"
    return {"subject": subject, "html": page, "text": text}
