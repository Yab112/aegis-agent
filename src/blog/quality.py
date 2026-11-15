"""Quality gate: length, structure, banned phrases and similarity to recent posts."""
from __future__ import annotations

import os
import re

from supabase import Client

from src.blog.rich_content import prose_word_count


def _quality_gate_settings() -> tuple[bool, int, int, float, bool, list[str]]:
    enabled = os.environ.get("BLOG_QUALITY_GATE_ENABLED", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    min_words = int(os.environ.get("BLOG_QUALITY_MIN_WORDS", "900"))
    min_headings = int(os.environ.get("BLOG_QUALITY_MIN_HEADINGS", "3"))
    dup_threshold = float(os.environ.get("BLOG_DUPLICATE_SIM_THRESHOLD", "0.72"))
    block_on_fail = os.environ.get("BLOG_QUALITY_BLOCK_ON_FAIL", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    banned_raw = os.environ.get(
        "BLOG_BANNED_PHRASES",
        "as an ai language model,here is the article,in conclusion,to summarize",
    )
    banned = [x.strip().lower() for x in banned_raw.split(",") if x.strip()]
    min_words = max(200, min(min_words, 2500))
    min_headings = max(2, min(min_headings, 10))
    dup_threshold = max(0.4, min(dup_threshold, 0.95))
    return enabled, min_words, min_headings, dup_threshold, block_on_fail, banned


def _tokenize_for_similarity(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", text.lower())
    return set(words)


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a.intersection(b))
    union = len(a.union(b))
    if union == 0:
        return 0.0
    return inter / union


def _max_similarity_against_recent_posts(client: Client, candidate_body: str) -> float:
    candidate_tokens = _tokenize_for_similarity(candidate_body)
    if not candidate_tokens:
        return 0.0
    rows_res = (
        client.table("blog_posts")
        .select("body_md")
        .order("created_at", desc=True)
        .limit(30)
        .execute()
    )
    max_sim = 0.0
    for row in rows_res.data or []:
        body = str(row.get("body_md") or "")
        sim = _jaccard_similarity(candidate_tokens, _tokenize_for_similarity(body))
        if sim > max_sim:
            max_sim = sim
    return max_sim


def _evaluate_quality_gate(
    client: Client, *, title: str, body_md: str, min_words_override: int | None = None
) -> tuple[bool, str, float]:
    enabled, min_words, min_headings, dup_threshold, _, banned_phrases = _quality_gate_settings()
    if not enabled:
        return True, "disabled", 0.0

    if min_words_override:
        min_words = min_words_override  # a news brief is short by design
    word_count = prose_word_count(body_md)
    if word_count < min_words:
        return False, f"too_short:{word_count}<{min_words}", 0.0

    heading_count = sum(
        1 for line in body_md.splitlines() if line.strip().startswith("##")
    )
    if heading_count < min_headings:
        return False, f"insufficient_headings:{heading_count}<{min_headings}", 0.0

    lowered = body_md.lower()
    for phrase in banned_phrases:
        if phrase and phrase in lowered:
            return False, f"banned_phrase:{phrase}", 0.0

    dup_score = _max_similarity_against_recent_posts(client, body_md)
    if dup_score >= dup_threshold:
        return False, f"duplicate_similarity:{dup_score:.3f}>={dup_threshold}", dup_score

    if len(title.strip()) < 10:
        return False, "title_too_short", dup_score
    return True, "ok", dup_score
