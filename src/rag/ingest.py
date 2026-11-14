"""Knowledge-base ingestion: docs/knowledge_base/** → chunks → embeddings → Supabase.

Idempotent: every chunk gets a deterministic id derived from its file and
content, so re-running upserts in place, and rows that no longer correspond to
any chunk (edited or deleted files, or rows written by older random-id runs)
are removed afterwards.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("aegis.rag.ingest")

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parents[2] / "docs" / "knowledge_base"
SUPPORTED_EXTENSIONS = {".md", ".txt", ".py", ".js", ".ts", ".json", ".rst"}
_NAMESPACE = uuid.UUID("6f1c2a0e-5b7d-4c8e-9a3f-2d1e0b9c8a71")


def kb_files(base: Path = KNOWLEDGE_BASE_DIR) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(
        p
        for p in base.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith(".")
    )


def kb_fingerprint(base: Path = KNOWLEDGE_BASE_DIR) -> str:
    """Hash of every knowledge-base file's path and bytes; changes when the docs change."""
    h = hashlib.sha256()
    for p in kb_files(base):
        h.update(p.relative_to(base).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _embedding_tag() -> str:
    """Empty for the original provider (keeps existing ids); otherwise names the provider and model."""
    try:
        from config.settings import get_settings

        s = get_settings()
        provider = (s.embedding_provider or "huggingface").strip().lower()
        return "" if provider == "huggingface" else f"{provider}:{s.gemini_embedding_model}"
    except Exception:
        return ""


def chunk_id(rel_path: str, occurrence: int, content: str, embedding_tag: str = "") -> str:
    # The id covers the embedding model too: change provider and every chunk gets a new id,
    # so it is re-embedded and the vectors from the old model are pruned.
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    suffix = f"#{embedding_tag}" if embedding_tag else ""
    return str(uuid.uuid5(_NAMESPACE, f"{rel_path}#{occurrence}#{digest}{suffix}"))


def collect_chunks(base: Path = KNOWLEDGE_BASE_DIR) -> list[dict[str, Any]]:
    from src.rag.chunkers import chunk_document

    out: list[dict[str, Any]] = []
    tag = _embedding_tag()
    for path in kb_files(base):
        rel = path.relative_to(base).as_posix()
        project = rel.split("/", 1)[0] if "/" in rel else None
        try:
            chunks = chunk_document(path, project_name=project)
        except Exception as exc:
            logger.warning("kb ingest: skipping %s (%s)", rel, exc)
            continue
        seen: dict[str, int] = {}
        for chunk in chunks:
            content = chunk["content"]
            n = seen.get(content, 0)  # identical chunks in one file still get distinct ids
            seen[content] = n + 1
            out.append({"id": chunk_id(rel, n, content, tag), "content": content, "metadata": chunk["metadata"]})
    return out


def run_kb_ingest(supabase_url: str, supabase_service_key: str, base: Path = KNOWLEDGE_BASE_DIR) -> dict[str, Any]:
    from supabase import create_client

    from src.rag.embedder import embed_texts

    # Switching embedding provider must trigger a re-ingest even if the docs did not change.
    fingerprint = kb_fingerprint(base) + (f"+{_embedding_tag()}" if _embedding_tag() else "")
    chunks = collect_chunks(base)
    if not chunks:
        # Never wipe the vector store because the docs folder is missing from a build.
        raise RuntimeError(f"No knowledge-base chunks found under {base}")

    client = create_client(supabase_url, supabase_service_key)

    existing: set[str] = set()
    page = 0
    while True:
        res = client.table("documents").select("id").range(page * 1000, page * 1000 + 999).execute()
        rows = res.data or []
        existing.update(str(r["id"]) for r in rows)
        if len(rows) < 1000:
            break
        page += 1

    wanted = {c["id"] for c in chunks}
    new = [c for c in chunks if c["id"] not in existing]
    stale = sorted(existing - wanted)

    # A build that lost most of the docs (e.g. an ignore rule) must not gut the store.
    # The very first run (no shared ids yet) is exempt: it replaces legacy random-id rows.
    if (existing & wanted) and len(stale) > len(wanted) and os.getenv("KB_INGEST_ALLOW_LARGE_PRUNE", "").lower() not in ("1", "true", "yes"):
        raise RuntimeError(
            f"Refusing to remove {len(stale)} rows while only {len(wanted)} chunks remain. "
            "Check that docs/knowledge_base is complete in this build, or set KB_INGEST_ALLOW_LARGE_PRUNE=true."
        )

    if new:
        embeddings = embed_texts([c["content"] for c in new])
        rows = [
            {
                "id": c["id"],
                "content": c["content"],
                "embedding": "[" + ",".join(str(x) for x in vec) + "]",
                "metadata": c["metadata"],
            }
            for c, vec in zip(new, embeddings)
        ]
        for i in range(0, len(rows), 50):
            client.table("documents").upsert(rows[i : i + 50]).execute()

    # Remove only after the new rows are in, so retrieval never sees an empty store.
    for i in range(0, len(stale), 100):
        client.table("documents").delete().in_("id", stale[i : i + 100]).execute()

    logger.info("kb ingest: chunks=%s embedded=%s removed=%s", len(chunks), len(new), len(stale))
    return {
        "fingerprint": fingerprint,
        "chunks": len(chunks),
        "embedded": len(new),
        "unchanged": len(chunks) - len(new),
        "removed": len(stale),
    }
