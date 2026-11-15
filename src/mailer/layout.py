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


DARK = "#1a0b2e"
MINT = "#36d98a"
CREAM = "#fffcf5"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"


def _link_row(settings: Any, site: str, host: str) -> str:
    links = [(host, site)]
    for label, attr in (("GitHub", "email_brand_github_url"), ("LinkedIn", "email_brand_linkedin_url")):
        url = getattr(settings, attr, None)
        if url:
            links.append((label, url))
    return " &nbsp;&middot;&nbsp; ".join(
        f'<a href="{html.escape(url, quote=True)}" style="color:{PURPLE};text-decoration:none;font-weight:600;">{html.escape(label)}</a>'
        for label, url in links
    )


def render_email(settings: Any, *, subject: str, body: str, variables: dict[str, str]) -> dict[str, str]:
    """Return {subject, html, text} with variables filled in. Only `body` differs between emails."""
    subject = fill_variables(subject, variables).strip()
    body = fill_variables(body, variables)
    site = str(settings.portfolio_url).rstrip("/")
    host = site.split("//")[-1]
    owner = html.escape(settings.owner_name)
    role = html.escape(settings.owner_role)
    tagline = html.escape(getattr(settings, "email_brand_tagline", None) or "Full-stack and AI builds for serious products")
    logo = getattr(settings, "email_brand_logo_url", None)
    mark = (
        f'<img src="{html.escape(logo, quote=True)}" alt="{owner}" height="30" style="display:block;height:30px;border:0;">'
        if logo
        else f'<span style="font-family:{FONT};font-size:19px;font-weight:800;letter-spacing:0.06em;color:{INK};text-transform:uppercase;">{owner}</span>'
    )
    initial = html.escape((settings.owner_name or "Y")[:1].upper())
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only"><title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#ece5d8;font-family:{FONT};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(body_to_text(body)[:110])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ece5d8;"><tr><td align="center" style="padding:32px 12px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;">

    <tr><td style="padding:0 4px 14px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td>{mark}</td>
        <td align="right" style="font-family:{MONO};font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:{MUTED};">{role}</td>
      </tr></table>
    </td></tr>

    <tr><td style="background:{DARK};padding:34px 34px 30px;">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="width:10px;height:10px;background:{MINT};font-size:0;line-height:0;">&nbsp;</td>
        <td style="padding-left:10px;font-family:{MONO};font-size:11px;letter-spacing:0.16em;text-transform:uppercase;color:#cdb8e6;">A note from {owner}</td>
      </tr></table>
      <div style="margin-top:14px;font-size:27px;line-height:1.2;font-weight:800;letter-spacing:-0.01em;color:#ffffff;">{html.escape(subject)}</div>
    </td></tr>
    <tr><td style="height:5px;background:{MINT};font-size:0;line-height:0;">&nbsp;</td></tr>

    <tr><td style="background:{CREAM};padding:34px 34px 10px;border-left:1px solid #d8cebf;border-right:1px solid #d8cebf;">
{body_to_html(body)}
    </td></tr>

    <tr><td style="background:{CREAM};padding:10px 34px 32px;border-left:1px solid #d8cebf;border-right:1px solid #d8cebf;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e3dacd;"><tr>
        <td width="56" style="padding-top:20px;vertical-align:top;">
          <table role="presentation" cellpadding="0" cellspacing="0"><tr>
            <td width="44" height="44" align="center" style="width:44px;height:44px;background:{PURPLE};color:#ffffff;font-size:20px;font-weight:800;">{initial}</td>
          </tr></table>
        </td>
        <td style="padding-top:20px;vertical-align:top;">
          <div style="font-size:16px;font-weight:700;color:{INK};">{owner}</div>
          <div style="margin-top:2px;font-size:13px;color:{MUTED};">{role}</div>
          <div style="margin-top:8px;font-size:13px;">{_link_row(settings, site, host)}</div>
        </td>
      </tr></table>
    </td></tr>

    <tr><td style="background:{DARK};padding:20px 34px;">
      <div style="font-size:13px;line-height:1.5;color:#e9e0f5;font-weight:600;">{tagline}</div>
      <div style="margin-top:8px;font-size:12px;line-height:1.55;color:#a996c2;">
        You are receiving this because you contacted {owner} through
        <a href="{html.escape(site, quote=True)}" style="color:#cdb8e6;">{html.escape(host)}</a>. Reply to this email to continue the conversation.
      </div>
    </td></tr>

  </table>
</td></tr></table>
</body></html>"""
    text = f"{body_to_text(body)}\n\n{settings.owner_name}\n{settings.owner_role} · {site}\n"
    return {"subject": subject, "html": page, "text": text}
