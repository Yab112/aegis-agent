"""Full visitor-reply HTML document."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.email_templates.visitor_reply.body_html import paragraphs_from_plain
from src.email_templates.visitor_reply.preheader import visitor_reply_preheader
from src.email_templates.visitor_reply.sections import (
    section_body_inner,
    section_footer,
    section_header,
    section_trust_strip,
    wrap_outer_card,
)

if TYPE_CHECKING:
    from config.settings import Settings


def build_branded_visitor_email_html(
    *,
    settings: "Settings",
    polished_plain_body: str,
    session_short: str,
) -> str:
    logo = (getattr(settings, "email_brand_logo_url", None) or "").strip() or None
    body_html = paragraphs_from_plain(polished_plain_body)

    inner = (
        section_header(settings=settings, logo_url=logo)
        + section_body_inner(body_html)
        + section_trust_strip(settings=settings, portfolio_href=settings.portfolio_url)
        + section_footer(
            settings=settings,
            session_short=session_short,
            portfolio_href=settings.portfolio_url,
        )
    )

    title = f"{settings.owner_name} — portfolio follow-up"
    pre = visitor_reply_preheader(settings)
    return wrap_outer_card(inner, preheader=pre, title=title)
