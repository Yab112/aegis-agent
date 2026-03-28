"""
Chunking strategies per document type.

- Prose (MD, TXT)  → RecursiveCharacterTextSplitter (512 tok, 64 overlap)
- Code (PY, JS)    → language-aware splitter (per function/class)
- Structured (JSON)→ one chunk per top-level key/object
"""
from __future__ import annotations
import json
from pathlib import Path
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    Language,
)
from config.settings import get_settings

settings = get_settings()


def chunk_prose(text: str, source: str, doc_type: str = "prose") -> list[dict]:
    """Split narrative text with overlap to preserve cross-sentence context."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        separators=["\n\n", "\n", ". ", " "],
    )
    raw_chunks = splitter.split_text(text)
    return [
        {
            "content": chunk,
            "metadata": {
                "source": source,
                "type": doc_type,
                "chunk_index": i,
            },
        }
        for i, chunk in enumerate(raw_chunks)
        if chunk.strip()
    ]


def chunk_python(code: str, source: str) -> list[dict]:
    """Split Python by function/class boundaries using LangChain's AST splitter."""
    splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.PYTHON,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=32,
    )
    raw_chunks = splitter.split_text(code)
    return [
        {
            "content": chunk,
            "metadata": {
                "source": source,
                "type": "code_python",
                "chunk_index": i,
            },
        }
        for i, chunk in enumerate(raw_chunks)
        if chunk.strip()
    ]


def chunk_javascript(code: str, source: str) -> list[dict]:
    """Split JavaScript by function/class boundaries."""
    splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.JS,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=32,
    )
    raw_chunks = splitter.split_text(code)
    return [
        {
            "content": chunk,
            "metadata": {
                "source": source,
                "type": "code_js",
                "chunk_index": i,
            },
        }
        for i, chunk in enumerate(raw_chunks)
        if chunk.strip()
    ]


def chunk_json(data: str, source: str) -> list[dict]:
    """
    Structured JSON (tech stack, project metadata) → one chunk per top-level item.
    Each chunk is a human-readable key: value string.
    """
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return chunk_prose(data, source, doc_type="json_fallback")

    chunks = []
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            chunks.append({
                "content": json.dumps(item, indent=2),
                "metadata": {
                    "source": source,
                    "type": "structured",
                    "chunk_index": i,
                },
            })
    elif isinstance(obj, dict):
        for i, (key, value) in enumerate(obj.items()):
            chunks.append({
                "content": f"{key}: {json.dumps(value)}",
                "metadata": {
                    "source": source,
                    "type": "structured",
                    "chunk_index": i,
                },
            })
    return chunks


def chunk_document(file_path: str | Path, project_name: str | None = None) -> list[dict]:
    """
    Route a file to the correct chunker based on extension,
    then inject project_name into every chunk's metadata.
    """
    path = Path(file_path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    source = path.name
    ext = path.suffix.lower()

    if ext in (".md", ".txt", ".rst"):
        chunks = chunk_prose(text, source)
    elif ext == ".py":
        chunks = chunk_python(text, source)
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        chunks = chunk_javascript(text, source)
    elif ext == ".json":
        chunks = chunk_json(text, source)
    else:
        # Fallback: treat as prose
        chunks = chunk_prose(text, source, doc_type="unknown")

    # Inject project name into every chunk
    if project_name:
        for chunk in chunks:
            chunk["metadata"]["project_name"] = project_name

    return chunks
