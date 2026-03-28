"""
Retriever: query Supabase pgvector via the match_documents RPC.

Returns top-k chunks sorted by cosine similarity, plus a confidence score
(the similarity of the best match). The LangGraph agent uses the confidence
score to decide whether to answer or trigger a human handoff.
"""
from __future__ import annotations

import logging

from supabase import create_client, Client
from config.settings import get_settings
from src.rag.embedder import embed_query

settings = get_settings()
logger = logging.getLogger("aegis.rag")

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client


def retrieve(
    query: str,
    top_k: int = 5,
    metadata_filter: dict | None = None,
) -> tuple[list[dict], float]:
    """
    Retrieve top-k chunks relevant to query.

    Args:
        query: raw user question (prefix added inside embed_query)
        top_k: number of chunks to return
        metadata_filter: JSONB containment filter, e.g. {"project_name": "car_rental_app"}

    Returns:
        (chunks, confidence_score)
        chunks: list of {"content": str, "metadata": dict, "similarity": float}
        confidence_score: similarity of the top result (0.0–1.0)
    """
    query_embedding = embed_query(query)
    client = get_client()

    host = settings.supabase_url.replace("https://", "").split("/")[0]
    logger.info("rag: Supabase RPC match_documents host=%s top_k=%s", host, top_k)

    result = client.rpc(
        "match_documents",
        {
            "query_embedding": query_embedding,
            "match_count": top_k,
            "filter": metadata_filter or {},
        },
    ).execute()

    rows = result.data or []

    if not rows:
        logger.info("rag: match_documents returned 0 rows")
        return [], 0.0

    chunks = [
        {
            "content": row["content"],
            "metadata": row["metadata"],
            "similarity": row["similarity"],
        }
        for row in rows
    ]

    confidence = rows[0]["similarity"]  # Best match similarity
    logger.info(
        "rag: match_documents rows=%d best_similarity=%.4f",
        len(rows),
        confidence,
    )
    return chunks, confidence
