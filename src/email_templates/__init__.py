"""
Reusable transactional email layouts (table + inline CSS for Gmail).

Public API used by the Telegram → visitor relay lives under ``visitor_reply``.
"""

from src.email_templates.visitor_reply.html import build_branded_visitor_email_html
from src.email_templates.visitor_reply.plain import build_branded_visitor_email_plain
from src.email_templates.visitor_reply.polish import polish_owner_reply_for_visitor_email

__all__ = [
    "build_branded_visitor_email_html",
    "build_branded_visitor_email_plain",
    "polish_owner_reply_for_visitor_email",
]
