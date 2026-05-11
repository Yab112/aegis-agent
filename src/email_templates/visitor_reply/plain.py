"""Multipart plain-text alternative (mirrors key HTML marketing blocks)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings


def build_branded_visitor_email_plain(
    *,
    settings: "Settings",
    polished_plain_body: str,
    session_short: str,
) -> str:
    lines: list[str] = [
        visitor_reply_preheader_plain(settings),
        "",
        polished_plain_body.strip(),
        "",
        "──────── AT A GLANCE ────────",
        "• You reached out via the portfolio assistant.",
        f"• The reply above is from {settings.owner_name} (via your {settings.assistant_name} session).",
        "• Visit the portfolio for samples, services, and how to continue the conversation.",
        "",
        "──────── NEXT STEP ────────",
        f"Portfolio: {settings.portfolio_url}",
    ]

    sec = (getattr(settings, "email_brand_secondary_cta_url", None) or "").strip()
    if sec:
        lbl = (getattr(settings, "email_brand_secondary_cta_label", None) or "").strip() or "Book a call"
        lines.append(f"{lbl}: {sec}")

    li = (getattr(settings, "email_brand_linkedin_url", None) or "").strip()
    gh = (getattr(settings, "email_brand_github_url", None) or "").strip()
    if li or gh:
        lines.append("")
        lines.append("Links:")
        if li:
            lines.append(f"  LinkedIn: {li}")
        if gh:
            lines.append(f"  GitHub: {gh}")

    lines.extend(
        [
            "",
            "──────── SIGNATURE ────────",
            settings.owner_name,
            settings.owner_role,
            "",
            f"Ref · session {session_short} · {settings.assistant_name}",
            "One-to-one response — not a newsletter signup.",
        ]
    )
    return "\n".join(lines)


def visitor_reply_preheader_plain(settings: "Settings") -> str:
    bits = [f"Personal note from {settings.owner_name}"]
    tag = (getattr(settings, "email_brand_tagline", None) or "").strip()
    if tag:
        bits.append(tag)
    bits.append("Next steps inside.")
    return " · ".join(bits)
