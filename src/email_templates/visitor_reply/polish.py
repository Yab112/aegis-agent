"""Gemini polish for owner Telegram draft → visitor email body."""
from __future__ import annotations

import logging
import re
import warnings
from typing import TYPE_CHECKING

from src.email_templates.visitor_reply.gemini_text import gemini_response_text

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger("aegis.email_templates.visitor_polish")

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    import google.generativeai as genai


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
        text = gemini_response_text(resp)
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        return text or raw
    except Exception as e:
        logger.warning("visitor reply polish failed, using raw draft: %s", e)
        return raw
