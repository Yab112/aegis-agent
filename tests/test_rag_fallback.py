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
