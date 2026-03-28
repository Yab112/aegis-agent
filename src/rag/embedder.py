"""
Embedding wrapper around HuggingFace Inference API.

Model: BAAI/bge-small-en-v1.5
- 384 dimensions
- MTEB top-10 for retrieval
- Free tier on HuggingFace Inference API
- ~50ms per batch of 32 chunks
"""
from __future__ import annotations
import logging
import time
import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import get_settings

logger = logging.getLogger("aegis.rag")

settings = get_settings()

# Legacy api-inference.huggingface.co returns 410; use Router + hf-inference provider.
def _hf_feature_extraction_url(model_id: str) -> str:
    return (
        "https://router.huggingface.co/hf-inference/models/"
        f"{model_id}/pipeline/feature-extraction"
    )


HF_API_URL = _hf_feature_extraction_url(settings.embedding_model)
BATCH_SIZE = 32  # Stay well within HF free tier limits


class HFTransientError(Exception):
    """Rate limit or temporary HF outage — safe to retry."""

    pass


def _embed_batch_once(texts: list[str]) -> list[list[float]]:
    """Single HF request; raises HFTransientError only when a retry might help."""
    headers = {"Authorization": f"Bearer {settings.hf_api_token}"}
    # Router API: inputs only; old "options" / wait_for_model not used on router.
    payload: dict = {"inputs": texts}

    response = httpx.post(HF_API_URL, headers=headers, json=payload, timeout=60)

    if response.status_code == 429:
        logger.warning("HuggingFace rate limit (429), will retry")
        raise HFTransientError("HuggingFace rate limit (429)")

    if response.status_code in (502, 503, 504):
        logger.warning("HuggingFace temporary error %s, will retry", response.status_code)
        raise HFTransientError(f"HuggingFace HTTP {response.status_code}")

    if response.status_code >= 400:
        snippet = (response.text or "")[:800]
        logger.error(
            "HuggingFace inference failed %s: %s",
            response.status_code,
            snippet,
        )
        raise RuntimeError(
            f"HuggingFace inference API error {response.status_code}: {snippet}"
        )

    embeddings = response.json()

    # BGE model returns nested lists — normalise shape
    if isinstance(embeddings[0][0], list):
        # Mean-pool token embeddings if returned
        embeddings = [
            [sum(col) / len(col) for col in zip(*token_embs)]
            for token_embs in embeddings
        ]

    return embeddings


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(HFTransientError),
    reraise=True,
)
def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Call HF Inference API for a single batch. Retries only on 429 / 5xx."""
    return _embed_batch_once(texts)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts in batches of BATCH_SIZE.
    Returns a flat list of 384-dim vectors in the same order as input.
    """
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        embeddings = _embed_batch(batch)
        all_embeddings.extend(embeddings)

        # Polite delay between batches to avoid rate limits
        if i + BATCH_SIZE < len(texts):
            time.sleep(0.5)

    return all_embeddings


def embed_query(query: str) -> list[float]:
    """
    Embed a single query string.
    BGE models work best with a task prefix for retrieval queries.
    """
    logger.info(
        "rag: HuggingFace embeddings POST model=%s",
        settings.embedding_model,
    )
    prefixed = f"Represent this sentence for searching relevant passages: {query}"
    return embed_texts([prefixed])[0]
