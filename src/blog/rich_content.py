"""
Rich blog blocks: the contract between the writer model and the site renderer.

The article body stays Markdown. On top of GFM (tables, footnotes) the writer may
emit three fenced blocks, which the portfolio renders as real components:

    ```chart     JSON spec  -> SVG chart (line | area | bar | scatter)
    ```figure    JSON spec  -> image with caption, credit, licence and source link
    ```mermaid   diagram source (passed through untouched)

This module validates what the model wrote *before* it is stored, so the site
never receives a chart it cannot draw, an image without a source, or a dead link.
Nothing here calls a model; network access is injected so it is unit-testable.
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import requests

logger = logging.getLogger("aegis.blog.rich")

CHART_TYPES = ("line", "area", "bar", "scatter")
MAX_SERIES = 4
MAX_POINTS = 60
HTTP_UA = "aegis-blog-pipeline/2.0 (static site blog generator)"

_FENCE_RE = re.compile(
    r"^```[ \t]*(chart|figure)[ \t]*\n(.*?)\n```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_MD_LINK_RE = re.compile(r"(?<!\!)\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]*(.*)$", re.MULTILINE)
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class RichReport:
    """What the validator did to a body. Stored with pipeline results and shown in admin."""

    charts: int = 0
    charts_dropped: int = 0
    charts_marked_illustrative: int = 0
    figures: int = 0
    figures_dropped: int = 0
    tables: int = 0
    diagrams: int = 0
    footnotes: int = 0
    footnotes_without_source: int = 0
    links_unlinked: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def normalize_url(url: str) -> str:
    """Compare URLs loosely: scheme/host case, trailing slash and fragment do not matter."""
    u = (url or "").strip()
    try:
        p = urlparse(u)
    except ValueError:
        return u
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").rstrip("/")
    q = f"?{p.query}" if p.query else ""
    return f"{host}{path}{q}"


def _clean_str(value: Any, limit: int) -> str:
    if value is None:
        return ""
    s = str(value).replace("\r", " ").replace("\n", " ").strip()
    return s[:limit]


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    elif isinstance(value, str):
        try:
            v = float(value.replace(",", "").replace("%", "").strip())
        except ValueError:
            return None
    else:
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _tidy(v: float) -> int | float:
    return int(v) if v == int(v) else round(v, 6)


def normalize_chart(raw: Any, allowed: set[str]) -> tuple[dict[str, Any] | None, str]:
    """
    Return (spec, note). ``spec`` is None when the chart cannot be drawn.

    A chart may only claim ``basis: "sourced"`` when its source URL is one the
    research step actually retrieved; otherwise it is relabelled illustrative,
    which the site prints under the chart.
    """
    if not isinstance(raw, dict):
        return None, "chart: not a JSON object"
    ctype = _clean_str(raw.get("type"), 20).lower()
    if ctype not in CHART_TYPES:
        return None, f"chart: unsupported type {ctype!r}"
    title = _clean_str(raw.get("title"), 160)
    if not title:
        return None, "chart: missing title"
    series_in = raw.get("series")
    if not isinstance(series_in, list) or not series_in:
        return None, "chart: missing series"
    if len(series_in) > MAX_SERIES:
        return None, f"chart: more than {MAX_SERIES} series"

    series_out: list[dict[str, Any]] = []
    x_out: list[Any] = []
    if ctype == "scatter":
        for s in series_in:
            if not isinstance(s, dict) or not isinstance(s.get("data"), list):
                return None, "chart: bad scatter series"
            pts = []
            for pt in s["data"][:MAX_POINTS * 4]:
                if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                    return None, "chart: scatter points must be [x, y]"
                px, py = _to_number(pt[0]), _to_number(pt[1])
                if px is None or py is None:
                    return None, "chart: non-numeric scatter point"
                pts.append([_tidy(px), _tidy(py)])
            if len(pts) < 2:
                return None, "chart: scatter series needs 2+ points"
            series_out.append({"name": _clean_str(s.get("name"), 60) or "Series", "data": pts})
    else:
        x_in = raw.get("x")
        if not isinstance(x_in, list) or not (2 <= len(x_in) <= MAX_POINTS):
            return None, "chart: x must list 2-60 labels"
        x_out = [x if isinstance(x, (int, float)) and not isinstance(x, bool) else _clean_str(x, 40) for x in x_in]
        for s in series_in:
            if not isinstance(s, dict) or not isinstance(s.get("data"), list):
                return None, "chart: bad series"
            if len(s["data"]) != len(x_out):
                return None, "chart: series length differs from x"
            vals = [_to_number(v) for v in s["data"]]
            if any(v is None for v in vals):
                return None, "chart: non-numeric value"
            series_out.append(
                {"name": _clean_str(s.get("name"), 60) or "Series", "data": [_tidy(v) for v in vals]}  # type: ignore[arg-type]
            )

    spec: dict[str, Any] = {"type": ctype, "title": title}
    if x_out:
        spec["x"] = x_out
    spec["series"] = series_out
    for key, limit in (("x_label", 60), ("y_label", 60), ("unit", 12), ("caption", 400)):
        val = _clean_str(raw.get(key), limit)
        if val:
            spec[key] = val

    note = ""
    src = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    src_url = _clean_str(src.get("url"), 600)
    src_label = _clean_str(src.get("label"), 120)
    basis = _clean_str(raw.get("basis"), 20).lower()
    if basis == "sourced" and src_url and normalize_url(src_url) in allowed:
        spec["basis"] = "sourced"
        spec["source"] = {"label": src_label or urlparse(src_url).netloc, "url": src_url}
    else:
        if basis == "sourced":
            note = f"chart {title!r}: source not in research pack, marked illustrative"
        spec["basis"] = "illustrative"
    return spec, note


# ── Figures (Wikimedia Commons) ───────────────────────────────────────────────

def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", value or ""))).strip()


def search_commons_images(query: str, limit: int = 6, timeout: int = 15) -> list[dict[str, Any]]:
    """
    Search Wikimedia Commons for freely licensed images.

    Each candidate carries what a caption needs: the author, the licence and the
    file page, so every figure on the blog links back to where it came from.
    """
    q = (query or "").strip()
    if not q:
        return []
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{q} filetype:bitmap|drawing",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime|size",
        "iiurlwidth": "1400",
    }
    resp = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params=params,
        headers={"User-Agent": HTTP_UA},
        timeout=timeout,
    )
    resp.raise_for_status()
    pages = ((resp.json() or {}).get("query") or {}).get("pages") or {}
    out: list[dict[str, Any]] = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 999)):
        info = (page.get("imageinfo") or [{}])[0]
        mime = str(info.get("mime") or "")
        if mime not in ("image/jpeg", "image/png", "image/svg+xml", "image/webp"):
            continue
        if int(info.get("width") or 0) < 500:
            continue
        meta = info.get("extmetadata") or {}
        licence = _strip_html(str((meta.get("LicenseShortName") or {}).get("value") or ""))
        page_url = str(info.get("descriptionurl") or "")
        src = str(info.get("thumburl") or info.get("url") or "").split("?", 1)[0]  # drop tracking params
        if not licence or not page_url or not src:
            continue
        out.append(
            {
                "src": src,
                "source_url": page_url,
                "credit": _strip_html(str((meta.get("Artist") or {}).get("value") or ""))[:140] or "Wikimedia Commons",
                "license": licence[:60],
                "title": str(page.get("title") or "").removeprefix("File:"),
                "description": _strip_html(str((meta.get("ImageDescription") or {}).get("value") or ""))[:300],
                "width": int(info.get("thumbwidth") or info.get("width") or 0),
                "height": int(info.get("thumbheight") or info.get("height") or 0),
            }
        )
    return out


FigureResolver = Callable[[dict[str, Any]], dict[str, Any] | None]


def normalize_figure(raw: Any, resolver: FigureResolver | None) -> dict[str, Any] | None:
    """A figure survives only if it resolves to a real image with a credit and a source page."""
    if not isinstance(raw, dict):
        return None
    caption = _clean_str(raw.get("caption"), 400)
    alt = _clean_str(raw.get("alt"), 240) or caption
    resolved: dict[str, Any] | None = None
    if _clean_str(raw.get("src"), 800).startswith("https://") and _clean_str(raw.get("source_url"), 800):
        resolved = raw  # already resolved (e.g. edited in admin)
    elif resolver is not None:
        try:
            resolved = resolver(raw)
        except Exception as exc:  # network / quota: a missing figure is fine
            logger.warning("figure resolve failed: %s", exc)
            resolved = None
    if not resolved:
        return None
    src = _clean_str(resolved.get("src"), 800)
    source_url = _clean_str(resolved.get("source_url"), 800)
    if not src.startswith("https://") or not source_url.startswith("http"):
        return None
    fig: dict[str, Any] = {
        "src": src,
        "alt": alt or "Figure",
        "caption": caption,
        "credit": _clean_str(resolved.get("credit"), 140),
        "license": _clean_str(resolved.get("license"), 60),
        "source_url": source_url,
    }
    for dim in ("width", "height"):
        try:
            if int(resolved.get(dim) or 0) > 0:
                fig[dim] = int(resolved[dim])
        except (TypeError, ValueError):
            pass
    return fig


# ── Links and footnotes ───────────────────────────────────────────────────────

def url_is_live(url: str, timeout: int = 7) -> bool:
    try:
        r = requests.get(url, headers={"User-Agent": HTTP_UA}, timeout=timeout, stream=True, allow_redirects=True)
        ok = r.status_code < 400
        r.close()
        return ok
    except requests.RequestException:
        return False


def _split_fences(md: str) -> list[tuple[bool, str]]:
    """Split into (is_code, text) so link rewriting never touches fenced code."""
    parts: list[tuple[bool, str]] = []
    buf: list[str] = []
    in_code = False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            if in_code:
                buf.append(line)
                parts.append((True, "\n".join(buf)))
                buf = []
                in_code = False
            else:
                if buf:
                    parts.append((False, "\n".join(buf)))
                buf = [line]
                in_code = True
        else:
            buf.append(line)
    if buf:
        parts.append((in_code, "\n".join(buf)))
    return parts


def process_body(
    body_md: str,
    *,
    allowed_urls: list[str],
    figure_resolver: FigureResolver | None = None,
    url_checker: Callable[[str], bool] | None = None,
    max_url_checks: int = 25,
) -> tuple[str, RichReport]:
    """
    Validate and canonicalise a model-written body.

    - chart blocks: parsed, checked, re-emitted as canonical JSON (or removed)
    - figure blocks: resolved to a licensed image with credit (or removed)
    - links: kept when they are in the research pack or answer over HTTP;
      otherwise the link is removed and the text kept
    - footnotes: counted, and definitions left with no link are reported
    """
    report = RichReport()
    allowed = {normalize_url(u) for u in allowed_urls if u}

    def replace_block(m: re.Match[str]) -> str:
        kind, payload = m.group(1), m.group(2)
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as exc:
            report.notes.append(f"{kind}: invalid JSON ({exc.msg})")
            if kind == "chart":
                report.charts_dropped += 1
            else:
                report.figures_dropped += 1
            return ""
        if kind == "chart":
            spec, note = normalize_chart(raw, allowed)
            if spec is None:
                report.charts_dropped += 1
                report.notes.append(note)
                return ""
            if note:
                report.notes.append(note)
                report.charts_marked_illustrative += 1
            report.charts += 1
            return "```chart\n" + json.dumps(spec, indent=2, ensure_ascii=False) + "\n```"
        fig = normalize_figure(raw, figure_resolver)
        if fig is None:
            report.figures_dropped += 1
            report.notes.append(f"figure dropped: {_clean_str(raw.get('query') if isinstance(raw, dict) else '', 80)!r}")
            return ""
        report.figures += 1
        return "```figure\n" + json.dumps(fig, indent=2, ensure_ascii=False) + "\n```"

    body = _FENCE_RE.sub(replace_block, body_md.replace("\r\n", "\n"))

    checks = 0
    verdicts: dict[str, bool] = {}

    def link_ok(url: str) -> bool:
        nonlocal checks
        key = normalize_url(url)
        if key in allowed:
            return True
        if key in verdicts:
            return verdicts[key]
        if url_checker is None:
            verdicts[key] = True
        elif checks >= max_url_checks:
            verdicts[key] = False
        else:
            checks += 1
            verdicts[key] = bool(url_checker(url))
        return verdicts[key]

    def relink(m: re.Match[str]) -> str:
        if link_ok(m.group(2)):
            return m.group(0)
        report.links_unlinked += 1
        report.notes.append(f"link removed (unverified): {m.group(2)[:120]}")
        return m.group(1)

    rebuilt: list[str] = []
    for is_code, text in _split_fences(body):
        if is_code:
            if text.lstrip().startswith("```mermaid"):
                report.diagrams += 1
            rebuilt.append(text)
        else:
            rebuilt.append(_MD_LINK_RE.sub(relink, text))
    body = "\n".join(rebuilt)

    prose = "\n".join(t for is_code, t in _split_fences(body) if not is_code)
    defs = {m.group(1): m.group(2) for m in _FOOTNOTE_DEF_RE.finditer(prose)}
    refs = set(_FOOTNOTE_REF_RE.findall(_FOOTNOTE_DEF_RE.sub("", prose)))
    report.footnotes = len(defs)
    report.footnotes_without_source = sum(1 for text in defs.values() if "http" not in text)
    orphan_refs = refs - set(defs)
    if orphan_refs:
        # A reference with no definition renders as literal "[^3]" — remove it.
        for ref in orphan_refs:
            body = re.sub(rf"\[\^{re.escape(ref)}\](?!:)", "", body)
        report.notes.append(f"removed {len(orphan_refs)} footnote reference(s) with no definition")

    report.tables = len(re.findall(r"^\|.*\|[ \t]*\n\|[ \t]*:?-{2,}", prose, re.MULTILINE))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, report


def prose_word_count(body_md: str) -> int:
    """Words a reader reads: fenced blocks (code, chart JSON, diagrams) do not count."""
    prose = "\n".join(t for is_code, t in _split_fences(body_md) if not is_code)
    return len(re.findall(r"\b\w+\b", prose))


_ADVICE_HEADING = re.compile(
    r"^(what (to|you should) do next|next steps?|recommendations?|action items?|key takeaways?|takeaways?|"
    r"conclusions?|final thoughts|in summary|summary|wrapping up|what should you do)\b",
    re.IGNORECASE,
)


def drop_advice_sections(body_md: str) -> tuple[str, list[str]]:
    """
    Remove `##` sections that tell the reader what to do or merely wrap up ("What to do next",
    "Key takeaways", "Conclusion"). The site reports news; it does not hand out instructions.
    Footnote definitions that follow a removed section are kept. Returns (body, removed headings).
    """
    parts = _split_fences(body_md)
    out: list[str] = []
    removed: list[str] = []
    skipping = False
    for is_code, text in parts:
        if is_code:
            if not skipping:
                out.append(text)
            continue
        kept: list[str] = []
        for line in text.split("\n"):
            heading = re.match(r"^##\s+(.*?)\s*#*\s*$", line)
            if heading:
                title = re.sub(r"^[\d.\s:)-]+", "", heading.group(1)).strip(" *_")
                skipping = bool(_ADVICE_HEADING.match(title))
                if skipping:
                    removed.append(heading.group(1))
                    continue
            if skipping and not _FOOTNOTE_DEF_RE.match(line):
                continue
            kept.append(line)
        out.append("\n".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip(), removed

