"""
Tests for the RAG pipeline — chunking, embedding, retrieval.
Run with: pytest tests/test_rag.py -v
"""
import pytest
from unittest.mock import patch, MagicMock
from src.rag.chunkers import chunk_prose, chunk_python, chunk_json, chunk_document


# ──────────────────────────────────────────────────────────────────────────────
# CHUNKER TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestChunkers:
    def test_prose_chunker_returns_non_empty_chunks(self):
        text = "This is a test document. " * 100
        chunks = chunk_prose(text, source="test.md")
        assert len(chunks) > 0
        assert all(c["content"].strip() for c in chunks)

    def test_prose_chunker_attaches_metadata(self):
        chunks = chunk_prose("Hello world " * 50, source="overview.md")
        for chunk in chunks:
            assert chunk["metadata"]["source"] == "overview.md"
            assert chunk["metadata"]["type"] == "prose"
            assert "chunk_index" in chunk["metadata"]

    def test_python_chunker_keeps_functions_intact(self):
        code = '''
def hello():
    """Say hello."""
    return "hello"

def world():
    """Say world."""
    return "world"
'''
        chunks = chunk_python(code, source="main.py")
        assert len(chunks) > 0
        assert all(c["metadata"]["type"] == "code_python" for c in chunks)

    def test_json_chunker_one_chunk_per_key(self):
        import json
        data = json.dumps({
            "name": "Yabibal",
            "skills": ["Python", "React", "AI"],
            "role": "Full-Stack Engineer",
        })
        chunks = chunk_json(data, source="profile.json")
        assert len(chunks) == 3  # One per top-level key

    def test_json_list_chunker(self):
        import json
        data = json.dumps([
            {"project": "car_rental_app", "tech": "Next.js"},
            {"project": "cli_tool", "tech": "Python"},
        ])
        chunks = chunk_json(data, source="projects.json")
        assert len(chunks) == 2

    def test_project_name_injected_by_chunk_document(self, tmp_path):
        md_file = tmp_path / "overview.md"
        md_file.write_text("This is a test project overview. " * 30)
        chunks = chunk_document(md_file, project_name="car_rental_app")
        assert all(c["metadata"]["project_name"] == "car_rental_app" for c in chunks)

    def test_unsupported_extension_falls_back_to_prose(self, tmp_path):
        file = tmp_path / "notes.rst"
        file.write_text("RST document content here. " * 30)
        chunks = chunk_document(file)
        assert len(chunks) > 0


# ──────────────────────────────────────────────────────────────────────────────
# EMBEDDER TESTS (mocked — don't hit HF API in tests)
# ──────────────────────────────────────────────────────────────────────────────

class TestEmbedder:
    @patch("src.rag.embedder._embed_batch")
    def test_embed_texts_returns_correct_count(self, mock_batch):
        mock_batch.return_value = [[0.1] * 384] * 5
        from src.rag.embedder import embed_texts
        result = embed_texts(["text"] * 5)
        assert len(result) == 5
        assert len(result[0]) == 384

    @patch("src.rag.embedder._embed_batch")
    def test_embed_texts_batches_correctly(self, mock_batch):
        mock_batch.return_value = [[0.1] * 384] * 32
        from src.rag.embedder import embed_texts
        # 70 texts should trigger 3 batch calls (32 + 32 + 6)
        embed_texts(["t"] * 70)
        assert mock_batch.call_count == 3

    @patch("src.rag.embedder.embed_texts")
    def test_embed_query_adds_prefix(self, mock_embed):
        mock_embed.return_value = [[0.1] * 384]
        from src.rag.embedder import embed_query
        embed_query("what projects have you built?")
        call_args = mock_embed.call_args[0][0][0]
        assert "Represent this sentence" in call_args


# ──────────────────────────────────────────────────────────────────────────────
# RETRIEVER TESTS (mocked Supabase)
# ──────────────────────────────────────────────────────────────────────────────

class TestRetriever:
    @patch("src.rag.retriever.get_client")
    @patch("src.rag.retriever.embed_query")
    def test_retrieve_returns_chunks_and_confidence(self, mock_embed, mock_client):
        mock_embed.return_value = [0.1] * 384
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value.data = [
            {"content": "Car rental app overview", "metadata": {"project_name": "car_rental_app"}, "similarity": 0.91},
            {"content": "Built with Next.js", "metadata": {"project_name": "car_rental_app"}, "similarity": 0.85},
        ]
        mock_client.return_value.rpc.return_value = mock_rpc

        from src.rag.retriever import retrieve
        chunks, confidence = retrieve("Tell me about the car rental app")
        assert len(chunks) == 2
        assert confidence == pytest.approx(0.91)

    @patch("src.rag.retriever.get_client")
    @patch("src.rag.retriever.embed_query")
    def test_retrieve_empty_returns_zero_confidence(self, mock_embed, mock_client):
        mock_embed.return_value = [0.1] * 384
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value.data = []
        mock_client.return_value.rpc.return_value = mock_rpc

        from src.rag.retriever import retrieve
        chunks, confidence = retrieve("something completely unrelated")
        assert chunks == []
        assert confidence == 0.0
