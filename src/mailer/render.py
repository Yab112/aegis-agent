"""
Email body → HTML. The body is the only thing that changes between emails; the
layout around it is fixed (layout.py).

Body syntax (a small, safe subset of Markdown; anything else is shown as text):
  blank line          new paragraph
  ## Heading          section heading
  - item              bullet list
  > quote             quoted line (e.g. the visitor's question)
  **bold**  *italic*  [label](https://link)
  [[Button label|https://link]]    a call-to-action button, on its own line
  {{first_name}} {{owner_name}} {{site}} {{email}}   filled in per recipient
"""
from __future__ import annotations

import html
import re
from typing import Any

INK = "#1b0f26"
MUTED = "#57496a"
PURPLE = "#6b3f8f"
GREEN = "#0b7a45"

_VAR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_BUTTON = re.compile(r"^\[\[([^|\]]{1,60})\|(https?://[^\]\s]+)\]\]$")
_SAFE_URL = re.compile(r"^(https?://|mailto:)[^\s\"'<>]+$")


def fill_variables(text: str, variables: dict[str, Any]) -> str:
    """Unknown {{names}} are left visible so a missing value is noticed before sending."""
    return _VAR.sub(lambda m: str(variables[m.group(1)]) if variables.get(m.group(1)) else m.group(0), text or "")


def missing_variables(text: str, variables: dict[str, Any]) -> list[str]:
    return sorted({m.group(1) for m in _VAR.finditer(text or "") if not variables.get(m.group(1))})


def _inline(text: str) -> str:
    out = html.escape(text, quote=True)

    def link(m: re.Match[str]) -> str:
        url = html.unescape(m.group(2))
        if not _SAFE_URL.match(url):
            return m.group(0)
        return f'<a href="{html.escape(url, quote=True)}" style="color:{PURPLE};text-decoration:underline;">{m.group(1)}</a>'

    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", out)
    return out


def body_to_html(body: str) -> str:
    """Inline-styled HTML blocks, ready to drop into the layout. All input is escaped."""
    blocks: list[str] = []
    para: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(f'<p style="margin:0 0 16px;font-size:16px;line-height:1.65;color:{INK};">{"<br>".join(_inline(l) for l in para)}</p>')
            para.clear()
        if items:
            lis = "".join(f'<li style="margin:0 0 6px;">{_inline(i)}</li>' for i in items)
            blocks.append(f'<ul style="margin:0 0 16px;padding-left:22px;font-size:16px;line-height:1.6;color:{INK};">{lis}</ul>')
            items.clear()

    for raw in (body or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        button = _BUTTON.match(line)
        if not line:
            flush()
        elif button:
            flush()
            blocks.append(
                '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 22px;"><tr>'
                f'<td style="background:#36d98a;border:2px solid {INK};"><a href="{html.escape(button.group(2), quote=True)}" '
                f'style="display:inline-block;padding:13px 26px;font-size:15px;font-weight:800;letter-spacing:0.02em;color:{INK};text-decoration:none;">'
                f"{html.escape(button.group(1))} &rarr;</a></td>"
                f'<td style="width:5px;background:{INK};font-size:0;line-height:0;">&nbsp;</td></tr>'
                f'<tr><td colspan="2" style="height:5px;background:{INK};font-size:0;line-height:0;">&nbsp;</td></tr></table>'
            )
        elif line.startswith("## "):
            flush()
            blocks.append(
                f'<h2 style="margin:28px 0 12px;padding-bottom:8px;border-bottom:2px solid {INK};font-size:13px;line-height:1.3;'
                f'letter-spacing:0.12em;text-transform:uppercase;color:{INK};">{_inline(line[3:])}</h2>'
            )
        elif line.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(line[2:])
        elif line.startswith(">"):
            flush()
            blocks.append(
                f'<p style="margin:0 0 18px;padding:14px 16px;border-left:4px solid {PURPLE};background:#f1e9f7;'
                f'font-size:15px;line-height:1.6;color:{MUTED};">{_inline(line.lstrip("> "))}</p>'
            )
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "\n".join(blocks)


def body_to_text(body: str) -> str:
    """Plain-text alternative: the same words without the markup."""
    text = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\1: \2", body or "")
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"^## ", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
