"""Composable HTML fragments for the visitor-reply layout (table-safe, Gmail-friendly)."""
from __future__ import annotations

import html
from typing import TYPE_CHECKING

from src.email_templates.tokens import (
    CARD_RADIUS,
    COLOR_ACCENT,
    COLOR_BAND,
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_ON_ACCENT,
    COLOR_TEXT,
    COLOR_TEXT_FAINT,
    COLOR_TEXT_MUTED,
    FONT_UI,
    MAX_WIDTH_PX,
)

if TYPE_CHECKING:
    from config.settings import Settings


def _check_row(text: str) -> str:
    esc = html.escape(text)
    return f"""
  <tr>
    <td valign="top" style="width:22px;padding:0 10px 10px 0;font-size:14px;line-height:1.5;color:{COLOR_ACCENT};font-family:{FONT_UI};">&#10003;</td>
    <td valign="top" style="padding:0 0 10px 0;font-size:14px;line-height:1.55;color:{COLOR_TEXT_MUTED};font-family:{FONT_UI};">{esc}</td>
  </tr>"""


def section_header(*, settings: "Settings", logo_url: str | None) -> str:
    """Logo / wordmark, optional tagline, intro line."""
    name = html.escape(settings.owner_name)
    tag_raw = (getattr(settings, "email_brand_tagline", None) or "").strip()
    tag_block = ""
    if tag_raw:
        tag_block = (
            f'<p style="margin:12px 0 0 0;font-size:15px;line-height:1.5;color:{COLOR_TEXT};'
            f'font-family:{FONT_UI};font-weight:600;">{html.escape(tag_raw)}</p>'
        )

    if logo_url:
        logo_esc = html.escape(logo_url.strip(), quote=True)
        mark = (
            f'<img src="{logo_esc}" alt="{name}" width="132" style="display:block;border:0;'
            f'max-width:168px;width:132px;height:auto;margin:0 0 4px 0;" />'
        )
    else:
        mark = (
            f'<p style="margin:0 0 4px 0;font-family:{FONT_UI};font-size:21px;font-weight:700;'
            f'color:{COLOR_TEXT};letter-spacing:-0.02em;">{name}</p>'
        )

    top_margin = 12 if tag_block else 8
    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr>
    <td style="padding:28px 32px 18px 32px;font-family:{FONT_UI};">
      {mark}
      {tag_block}
      <p style="margin:{top_margin}px 0 0 0;font-size:13px;line-height:1.5;color:{COLOR_TEXT_MUTED};">
        You reached out through the portfolio assistant — here is my direct follow-up.
      </p>
    </td>
  </tr>
  <tr>
    <td style="height:1px;line-height:1px;font-size:1px;background-color:{COLOR_BORDER};">&nbsp;</td>
  </tr>
</table>"""


def section_body_inner(body_html: str) -> str:
    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr>
    <td style="padding:26px 32px 8px 32px;font-family:{FONT_UI};">
      {body_html}
    </td>
  </tr>
</table>"""


def section_trust_strip(*, settings: "Settings", portfolio_href: str) -> str:
    """Context + next step (transactional email marketing best practice)."""
    bot = settings.assistant_name
    href = html.escape(portfolio_href, quote=True)

    row1 = "You started this from the portfolio site — this email is part of that same conversation."
    row2 = (
        f"The message above is my personal reply (sent through your {bot} session so we stay in context)."
    )
    row3 = "Next step: skim the portfolio for work samples, stack, and the best way to continue the thread."

    rows_html = _check_row(row1) + _check_row(row2) + _check_row(row3)

    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr>
    <td style="padding:20px 32px 22px 32px;background-color:{COLOR_BAND};border-top:1px solid {COLOR_BORDER};font-family:{FONT_UI};">
      <p style="margin:0 0 12px 0;font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:{COLOR_TEXT_MUTED};">
        At a glance
      </p>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
        {rows_html}
      </table>
      <p style="margin:6px 0 0 0;font-size:13px;line-height:1.55;color:{COLOR_TEXT_MUTED};">
        <a href="{href}" style="color:{COLOR_ACCENT};text-decoration:none;font-weight:600;">Open portfolio</a>
        <span style="color:{COLOR_TEXT_FAINT};"> — </span>
        case studies, services, and contact options in one place.
      </p>
    </td>
  </tr>
</table>"""


def _social_links_row(settings: "Settings") -> str:
    li = (getattr(settings, "email_brand_linkedin_url", None) or "").strip()
    gh = (getattr(settings, "email_brand_github_url", None) or "").strip()
    parts: list[str] = []
    if li:
        esc = html.escape(li, quote=True)
        parts.append(
            f'<a href="{esc}" style="color:{COLOR_ACCENT};text-decoration:none;font-weight:500;">LinkedIn</a>'
        )
    if gh:
        esc = html.escape(gh, quote=True)
        parts.append(
            f'<a href="{esc}" style="color:{COLOR_ACCENT};text-decoration:none;font-weight:500;">GitHub</a>'
        )
    if not parts:
        return ""
    joined = '<span style="color:#cbd5e1;padding:0 6px;">|</span>'.join(parts)
    return f"""
<p style="margin:18px 0 0 0;font-size:13px;line-height:1.5;font-family:{FONT_UI};color:{COLOR_TEXT_MUTED};">
  {joined}
</p>"""


def _cta_buttons_table(
    *,
    portfolio_href: str,
    portfolio_label: str,
    settings: "Settings",
) -> str:
    href_p = html.escape(portfolio_href, quote=True)
    sec_url = (getattr(settings, "email_brand_secondary_cta_url", None) or "").strip()
    sec_lbl_raw = (getattr(settings, "email_brand_secondary_cta_label", None) or "").strip()
    sec_label = sec_lbl_raw or "Book a call"

    primary = f"""
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0;">
  <tr>
    <td style="border-radius:8px;background-color:{COLOR_ACCENT};" bgcolor="{COLOR_ACCENT}">
      <a href="{href_p}" target="_blank" rel="noopener noreferrer"
        style="display:inline-block;padding:12px 22px;font-family:{FONT_UI};font-size:14px;font-weight:600;
        color:{COLOR_ON_ACCENT};text-decoration:none;">
        {html.escape(portfolio_label)}
      </a>
    </td>
  </tr>
</table>"""

    if not sec_url:
        return f'<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td>{primary}</td></tr></table>'

    href_s = html.escape(sec_url, quote=True)
    secondary = f"""
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0;">
  <tr>
    <td style="border-radius:8px;border:2px solid {COLOR_ACCENT};background-color:{COLOR_CARD};">
      <a href="{href_s}" target="_blank" rel="noopener noreferrer"
        style="display:inline-block;padding:10px 20px;font-family:{FONT_UI};font-size:14px;font-weight:600;
        color:{COLOR_ACCENT};text-decoration:none;">
        {html.escape(sec_label)}
      </a>
    </td>
  </tr>
</table>"""

    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0;">
  <tr>
    <td valign="middle" style="padding:0 8px 8px 0;">
      {primary}
    </td>
    <td valign="middle" style="padding:0 0 8px 8px;">
      {secondary}
    </td>
  </tr>
</table>"""


def section_footer(
    *,
    settings: "Settings",
    session_short: str,
    portfolio_href: str,
) -> str:
    name = html.escape(settings.owner_name)
    role = html.escape(settings.owner_role)
    site = html.escape(settings.portfolio_url)
    href = html.escape(portfolio_href, quote=True)
    assistant = html.escape(settings.assistant_name, quote=True)
    sess = html.escape(session_short, quote=True)

    social = _social_links_row(settings)
    ctas = _cta_buttons_table(
        portfolio_href=portfolio_href,
        portfolio_label="View portfolio",
        settings=settings,
    )

    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr>
    <td style="height:1px;line-height:1px;font-size:1px;background-color:{COLOR_BORDER};">&nbsp;</td>
  </tr>
  <tr>
    <td style="padding:22px 32px 28px 32px;background-color:{COLOR_CARD};font-family:{FONT_UI};">
      <p style="margin:0 0 4px 0;font-size:15px;font-weight:700;color:{COLOR_TEXT};">{name}</p>
      <p style="margin:0 0 18px 0;font-size:14px;color:{COLOR_TEXT_MUTED};">{role}</p>
      <p style="margin:0 0 8px 0;font-size:13px;line-height:1.5;color:{COLOR_TEXT_MUTED};">
        <a href="{href}" style="color:{COLOR_ACCENT};text-decoration:none;font-weight:500;">{site}</a>
      </p>
      {ctas}
      {social}
      <p style="margin:18px 0 0 0;font-size:11px;line-height:1.55;color:{COLOR_TEXT_FAINT};">
        Reference: session {sess} · assistant {assistant}<br />
        One-to-one message in response to your inquiry — you are not being added to a newsletter list.
      </p>
    </td>
  </tr>
</table>"""


def wrap_outer_card(inner_tables: str, *, preheader: str, title: str) -> str:
    from src.email_templates.tokens import COLOR_PAGE_BG

    ph = html.escape(preheader)
    ttl = html.escape(title)
    mw = str(MAX_WIDTH_PX)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="color-scheme" content="light only" />
  <title>{ttl}</title>
</head>
<body style="margin:0;padding:0;background-color:{COLOR_PAGE_BG};">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:transparent;">
    {ph}
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
    style="background-color:{COLOR_PAGE_BG};padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
          style="max-width:{mw}px;background-color:{COLOR_CARD};border:1px solid {COLOR_BORDER};
          border-radius:{CARD_RADIUS};overflow:hidden;
          box-shadow:0 4px 24px rgba(15,23,42,0.07);">
          <tr>
            <td style="height:3px;line-height:3px;font-size:3px;background-color:{COLOR_ACCENT};">&nbsp;</td>
          </tr>
          <tr>
            <td style="padding:0;">
              {inner_tables}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
