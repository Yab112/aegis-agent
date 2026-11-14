from __future__ import annotations

import src.rag.retriever as retriever
from src.rag.lexical import lexical_retrieve


def test_lexical_retrieval_finds_the_right_document():
    chunks, confidence = lexical_retrieve("How does BillShare settle with Telebirr?", top_k=3)
    assert chunks and any("Telebirr" in c["content"] and "bill-splitting" in c["content"] for c in chunks)
    assert confidence >= 0.72  # high enough for the agent to answer
    assert set(chunks[0]) == {"content", "metadata", "similarity"}


def test_lexical_retrieval_is_unsure_about_things_not_in_the_docs():
    chunks, confidence = lexical_retrieve("quantum chromodynamics lattice gauge renormalisation", top_k=3)
    assert confidence < 0.72


def test_lexical_retrieval_handles_empty_and_stopword_queries():
    assert lexical_retrieve("", top_k=3) == ([], 0.0)
    assert lexical_retrieve("what is the", top_k=3) == ([], 0.0)


def test_chat_retrieval_survives_an_embedding_outage(monkeypatch):
    def out_of_credits(_query):
        raise RuntimeError("HuggingFace inference API error 402: Purchase pre-paid credits")

    monkeypatch.setattr(retriever, "embed_query", out_of_credits)
    chunks, confidence = retriever.retrieve("What does Yabibal build day to day?", top_k=4)
    assert chunks and confidence > 0
    assert len(chunks) <= 4


def test_changing_embedding_provider_changes_every_chunk_id():
    from src.rag.ingest import chunk_id

    a = chunk_id("general/about.md", 0, "text")
    assert a == chunk_id("general/about.md", 0, "text", "")  # default provider keeps existing ids
    assert a != chunk_id("general/about.md", 0, "text", "gemini:models/gemini-embedding-001")


def test_default_provider_is_gemini_and_needs_no_hugging_face_token(monkeypatch):
    from config.settings import Settings

    monkeypatch.delenv("HF_API_TOKEN", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    s = Settings(_env_file=None)
    assert s.embedding_provider == "gemini" and s.hf_api_token == ""


def test_vector_search_only_matches_rows_from_the_current_model_else_keyword(monkeypatch):
    import src.rag.ingest as ingest

    seen = {}

    class _Rpc:
        def execute(self):
            return type("R", (), {"data": []})()  # nothing embedded by this model yet

    class _Client:
        def rpc(self, name, params):
            seen.update(params)
            return _Rpc()

    monkeypatch.setattr(ingest, "_embedding_tag", lambda: "gemini:models/gemini-embedding-001")
    monkeypatch.setattr(retriever, "embed_query", lambda q: [0.0] * 384)
    monkeypatch.setattr(retriever, "get_client", lambda: _Client())

    chunks, confidence = retriever.retrieve("How does BillShare settle with Telebirr?", top_k=3)
    assert seen["filter"] == {"embedding": "gemini:models/gemini-embedding-001"}
    assert chunks and confidence > 0  # answered by keyword search, not by mismatched vectors


def test_knowledge_sync_runs_once_even_without_scheduler_tables():
    from types import SimpleNamespace

    from src.scheduler.jobs import JobDef
    from src.scheduler.service import Scheduler
    from src.scheduler.store import InMemorySchedulerStore

    store = InMemorySchedulerStore()

    def missing(_name):
        raise RuntimeError("relation scheduler_jobs does not exist")

    store.get_job = missing
    calls = []
    job = JobDef(name="kb", title="KB", description="d", cron=None,
                 run=lambda ctx: calls.append(ctx.run_key) or {"fingerprint": "v1", "embedded": 3, "removed": 0, "chunks": 3},
                 fingerprint=lambda: "v1")
    settings = SimpleNamespace(scheduler_run_missed_on_first_boot=False, scheduler_max_attempts=3,
                               scheduler_retry_backoff_minutes=10, portfolio_url="https://x")
    sched = Scheduler(store, {"kb": job}, settings)
    sched.tick()
    sched.tick()
    assert calls == ["stateless-kb"]
