"""Keyword retrieval over the knowledge-base files that ship with the service.

This is the fallback when the embedding provider is unavailable (quota, outage,
bad key): the assistant keeps answering from the same documents, found by word
overlap instead of vector similarity. It needs no network and no credentials.
"""
from __future__ import annotations

import logging
import math
import re
from functools import lru_cache

logger = logging.getLogger("aegis.rag.lexical")

_STOP = frozenset(
    "a an and are as at be by can did do does for from has have he her his how i in is it its me my of on or "
    "our she that the their them they this to was we were what when where which who why will with you your "
    "about tell show give please yabibal yabibals".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9+#.]*", text.lower()) if t not in _STOP and len(t) > 1]


@lru_cache(maxsize=1)
def _corpus() -> tuple[list[dict], list[dict[str, int]], dict[str, float], float]:
    from src.rag.ingest import collect_chunks

    chunks = collect_chunks()
    freqs: list[dict[str, int]] = []
    doc_freq: dict[str, int] = {}
    total_len = 0
    for c in chunks:
        tf: dict[str, int] = {}
        for t in _tokens(c["content"]):
            tf[t] = tf.get(t, 0) + 1
        freqs.append(tf)
        total_len += sum(tf.values())
        for t in tf:
            doc_freq[t] = doc_freq.get(t, 0) + 1
    n = max(1, len(chunks))
    idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in doc_freq.items()}
    return chunks, freqs, idf, total_len / n


def lexical_retrieve(
    query: str,
    top_k: int = 5,
    metadata_filter: dict | None = None,
) -> tuple[list[dict], float]:
    """BM25 over the local knowledge base. Returns (chunks, confidence) like the vector retriever."""
    chunks, freqs, idf, avg_len = _corpus()
    terms = list(dict.fromkeys(_tokens(query)))
    if not chunks or not terms:
        return [], 0.0

    k1, b = 1.4, 0.75
    scored: list[tuple[float, int, int]] = []
    for i, (chunk, tf) in enumerate(zip(chunks, freqs)):
        if metadata_filter and any(chunk["metadata"].get(k) != v for k, v in metadata_filter.items()):
            continue
        length = sum(tf.values()) or 1
        score = 0.0
        matched = 0
        for t in terms:
            f = tf.get(t, 0)
            if not f:
                continue
            matched += 1
            score += idf.get(t, 0.0) * f * (k1 + 1) / (f + k1 * (1 - b + b * length / avg_len))
        if matched:
            scored.append((score, matched, i))
    if not scored:
        return [], 0.0

    scored.sort(reverse=True)
    top = scored[:top_k]
    best_matched = top[0][1]
    # Confident only when the best passage covers most of what was asked.
    coverage = best_matched / len(terms)
    confidence = round(0.5 + 0.35 * coverage, 4) if coverage >= 0.5 else round(0.3 + 0.3 * coverage, 4)
    out = [
        {"content": chunks[i]["content"], "metadata": chunks[i]["metadata"], "similarity": confidence if n == 0 else round(confidence * 0.95, 4)}
        for n, (_s, _m, i) in enumerate(top)
    ]
    logger.info("rag(lexical): terms=%d rows=%d confidence=%.4f", len(terms), len(out), confidence)
    return out, confidence
