"""
Ingestion pipeline — run this to populate your Supabase vector store.

Usage:
    python scripts/ingest.py

Structure your knowledge base in docs/knowledge_base/ like this:
    docs/knowledge_base/
    ├── car_rental_app/
    │   ├── overview.md
    │   ├── architecture.md
    │   └── api.py
    ├── cli_tool/
    │   ├── overview.md
    │   └── main.py
    └── general/
        ├── about.md
        ├── skills.json
        └── tech_stack.json
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client
from config.settings import get_settings
from src.rag.chunkers import chunk_document
from src.rag.embedder import embed_texts

settings = get_settings()
client = create_client(settings.supabase_url, settings.supabase_service_key)

KNOWLEDGE_BASE_DIR = Path(__file__).parent.parent / "docs" / "knowledge_base"
SUPPORTED_EXTENSIONS = {".md", ".txt", ".py", ".js", ".ts", ".json", ".rst"}


def ingest_all():
    print(f"Scanning {KNOWLEDGE_BASE_DIR}...")

    all_chunks = []
    file_count = 0

    for project_dir in sorted(KNOWLEDGE_BASE_DIR.iterdir()):
        if not project_dir.is_dir():
            continue

        project_name = project_dir.name
        print(f"\nProject: {project_name}")

        for file_path in sorted(project_dir.rglob("*")):
            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if file_path.name.startswith("."):
                continue

            try:
                chunks = chunk_document(file_path, project_name=project_name)
                print(f"  {file_path.name}: {len(chunks)} chunks")
                all_chunks.extend(chunks)
                file_count += 1
            except Exception as e:
                print(f"  ERROR {file_path.name}: {e}")

    print(f"\nTotal: {len(all_chunks)} chunks from {file_count} files")
    print("Embedding... (this may take a few minutes on free tier)")

    texts = [c["content"] for c in all_chunks]
    embeddings = embed_texts(texts)

    def _embedding_to_text(vec: list[float]) -> str:
        """Store pgvector literal in TEXT so PostgREST exposes public.documents (see supabase_init.sql)."""
        return "[" + ",".join(str(x) for x in vec) + "]"

    print("Writing to Supabase...")
    rows = [
        {
            "id": str(uuid.uuid4()),
            "content": chunk["content"],
            "embedding": _embedding_to_text(embedding),
            "metadata": chunk["metadata"],
        }
        for chunk, embedding in zip(all_chunks, embeddings)
    ]

    # Upsert in batches of 50
    batch_size = 50
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        client.table("documents").upsert(batch).execute()
        print(f"  Upserted {min(i + batch_size, len(rows))}/{len(rows)}")

    print(f"\nDone. {len(rows)} chunks stored in Supabase.")


if __name__ == "__main__":
    ingest_all()
