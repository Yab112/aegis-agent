"""Model plumbing for the blog writer: Gemini import, JSON extraction, generation config, retries."""
from __future__ import annotations

import json
import logging
import os
import time
import warnings
from typing import Any, Callable

logger = logging.getLogger("aegis.blog")

with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    import google.generativeai as genai
    from google.generativeai import protos as genai_protos


def _strip_markdown_code_fence(text: str) -> str:
    """Remove leading ```json / ``` wrapper models often add despite instructions."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if not lines:
        return t
    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    elif lines and lines[-1].strip().endswith("```"):
        lines[-1] = lines[-1].rsplit("```", 1)[0].rstrip()
    return "\n".join(lines).strip()


def _strip_outer_markdown_fence(text: str) -> str:
    """If the model wrapped the whole article in ``` / ```markdown, remove one outer fence."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if not lines:
        return t
    first = lines[0].strip()
    if first.startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_balanced_json_object(text: str) -> str:
    """Find first top-level `{` … `}` with string/escape awareness (handles body_md with braces)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("No opening brace in model output")
    depth = 0
    in_string = False
    escape = False
    quote: str | None = None
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif quote and ch == quote:
                in_string = False
                quote = None
            continue
        if ch == '"':
            in_string = True
            quote = '"'
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError(
        "JSON object appears truncated (no closing `}`). "
        "Try raising BLOG_MAX_OUTPUT_TOKENS or shortening the article in the prompt."
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip() if text else ""
    if not raw:
        raise ValueError("Empty model output")
    cleaned = _strip_markdown_code_fence(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    try:
        chunk = _extract_balanced_json_object(cleaned)
        return json.loads(chunk)
    except (json.JSONDecodeError, ValueError) as e:
        preview = raw[:500].replace("\n", "\\n")
        raise ValueError(f"Could not parse JSON from model: {e}; preview={preview!r}") from e


def _blog_generation_config() -> Any:
    """Larger default output so write step is not cut off mid-JSON."""
    max_tokens = int(os.environ.get("BLOG_MAX_OUTPUT_TOKENS", "24000"))
    max_tokens = max(2048, min(max_tokens, 65536))
    try:
        return genai.GenerationConfig(max_output_tokens=max_tokens)
    except AttributeError:
        return genai.types.GenerationConfig(max_output_tokens=max_tokens)


def _stage_retry_settings() -> tuple[int, int]:
    attempts = int(os.environ.get("BLOG_STAGE_RETRY_ATTEMPTS", "3"))
    base_sleep = int(os.environ.get("BLOG_STAGE_RETRY_BASE_SECONDS", "2"))
    attempts = max(1, min(attempts, 6))
    base_sleep = max(1, min(base_sleep, 20))
    return attempts, base_sleep


def _run_stage_with_retry(
    stage: str,
    fn: Callable[[], Any],
    *,
    attempts: int,
    base_sleep: int,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            sleep_s = base_sleep * attempt
            logger.warning(
                "stage=%s failed attempt=%s/%s err=%s; retrying in %ss",
                stage,
                attempt,
                attempts,
                exc,
                sleep_s,
            )
            time.sleep(sleep_s)
    if last_exc is None:
        raise RuntimeError(f"stage {stage} failed without exception")
    raise last_exc
