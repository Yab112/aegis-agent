"""
Shim: visitor reply email lives in ``src.email_templates.visitor_reply``.

Import from ``src.email_templates`` in new code.
"""
from __future__ import annotations

from src.email_templates import (
    build_branded_visitor_email_html,
    build_branded_visitor_email_plain,
    polish_owner_reply_for_visitor_email,
)

__all__ = [
    "build_branded_visitor_email_html",
    "build_branded_visitor_email_plain",
    "polish_owner_reply_for_visitor_email",
]
