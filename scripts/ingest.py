"""
Ingest docs/knowledge_base/** into the Supabase vector store (manual / local).

In production the API does this itself: on boot it compares the knowledge-base
fingerprint with the last ingested one and re-ingests when the docs changed
(see src/scheduler, job `kb_ingest`).

Usage:
    python scripts/ingest.py
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def ingest_all() -> dict:
    from config.settings import get_settings
    from src.rag.ingest import run_kb_ingest

    settings = get_settings()
    return run_kb_ingest(settings.supabase_url, settings.supabase_service_key)


if __name__ == "__main__":
    print(json.dumps(ingest_all(), indent=2))
