"""Inbox preview line (hidden in body; many clients show first ~90–140 chars)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings


def visitor_reply_preheader(settings: "Settings") -> str:
    name = settings.owner_name.strip()
    bits = [f"Personal note from {name}"]
    tag = (getattr(settings, "email_brand_tagline", None) or "").strip()
    if tag:
        bits.append(tag[:100])
    bits.append("Next steps & portfolio link inside.")
    out = " · ".join(bits)
    return out[:220]
