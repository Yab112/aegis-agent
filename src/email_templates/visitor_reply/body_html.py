"""Plain visitor message → safe HTML paragraphs (no rails / sidebars)."""
from __future__ import annotations

import html
import re

from src.email_templates.tokens import COLOR_TEXT, FONT_UI


def paragraphs_from_plain(plain: str) -> str:
    """
    Double newline = paragraph gap. Single newlines inside a block become separate <p>
    (common when Gemini returns one sentence per line).
    """
    blocks = [p.strip() for p in re.split(r"\n\s*\n+", (plain or "").strip()) if p.strip()]
    if not blocks:
        blocks = [(plain or "").strip() or " "]

    chunks: list[str] = []
    for bi, b in enumerate(blocks):
        paras: list[str] = []
        for line in b.split("\n"):
            line = line.strip()
            if not line:
                continue
            paras.append(
                f'<p style="margin:0 0 14px 0;padding:0;font-family:{FONT_UI};font-size:16px;'
                f"line-height:1.65;color:{COLOR_TEXT};\">{html.escape(line)}</p>"
            )
        if not paras:
            continue
        margin_bottom = "0" if bi == len(blocks) - 1 else "20px"
        chunks.append(
            f'<div style="margin:0 0 {margin_bottom} 0;">{"".join(paras)}</div>'
        )
    return "\n".join(chunks) if chunks else f'<p style="margin:0;font-family:{FONT_UI};">&nbsp;</p>'
