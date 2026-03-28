# RAG system — deep dive

This document explains every architectural decision in the RAG pipeline
so you can explain it confidently in client conversations.

---

## The five layers

```
Layer 1 → Ingestion & chunking
Layer 2 → Embedding (text → vectors)
Layer 3 → Storage (Supabase pgvector)
Layer 4 → Retrieval (query time)
Layer 5 → Generation (Gemini + prompt assembly)
```

---

## Layer 1 — Chunking strategy

Three document types, three strategies:

| Doc type | Strategy | Chunk size | Overlap |
|---|---|---|---|
| Prose (MD, TXT) | Recursive character split | 512 tokens | 64 tokens |
| Code (PY, JS) | AST-aware language split | 512 tokens | 32 tokens |
| Structured (JSON) | One chunk per top-level key | N/A | N/A |

**Why 512 tokens with 64 overlap?**
- 512 tokens fits comfortably in a BGE embedding without truncation
- 64-token overlap means a sentence straddling two chunks appears in both,
  so retrieval never loses a bridging idea
- Gemini's 1M context can handle 5× chunks of this size easily

**Why AST-aware splitting for code?**
- Naive character splitting cuts functions in half
- A half-function has no semantic meaning — its embedding is noise
- AST splitting keeps `def validate_booking(...)` as one atomic unit
  so the embedding captures "this validates a booking" correctly

**Metadata attached to every chunk:**
```json
{
  "source": "architecture.md",
  "type": "prose",
  "project_name": "car_rental_app",
  "chunk_index": 3
}
```
This metadata is what enables filtered retrieval — the agent can
tell pgvector "only search car_rental_app chunks" instead of
searching your entire knowledge base.

---

## Layer 2 — Embedding model choice

**Model: BAAI/bge-small-en-v1.5**

Why this specific model:
- MTEB leaderboard top-10 for retrieval tasks (as of 2024)
- 384 dimensions — small enough for fast cosine search, large enough
  for rich semantic representation
- Free on HuggingFace Inference API (no GPU required on your end)
- BGE models benefit from a task prefix at query time:
  `"Represent this sentence for searching relevant passages: {query}"`
  This prefix is added automatically in `embedder.py`

**Batching:** Chunks are embedded in batches of 32 with exponential backoff
on rate limit errors. At HF free tier, you can embed roughly 500 chunks
per minute. A typical portfolio knowledge base (20–40 documents) will
ingest in under 3 minutes.

---

## Layer 3 — pgvector index explained

**IVFFlat index (Inverted File with Flat compression):**

```sql
create index on documents
using ivfflat (embedding vector_cosine_ops)
with (lists = 100);
```

- `lists = 100`: divides the vector space into 100 clusters (Voronoi cells)
- At query time, `probes = 10` means we search the 10 most likely clusters
- This gives ~95% recall at ~5ms latency (vs exact search at ~50ms)
- Rule of thumb: `lists ≈ rows / 1000`, so 100 is right for 50k–200k chunks

**When to rebuild the index:**
After ingestion adds more than 20% new chunks, run:
```sql
reindex index documents_embedding_idx;
```

**Cosine vs L2 distance:**
We use cosine (`vector_cosine_ops`) because:
- BGE embeddings are not unit-normalized by default
- Cosine similarity is direction-only — two vectors pointing the same
  direction score 1.0 regardless of magnitude
- L2 (Euclidean) would penalize magnitude differences that don't
  reflect semantic difference

---

## Layer 4 — Query-time retrieval flow

```
User query
    ↓ embed_query() — adds BGE task prefix
Query vector (384-dim)
    ↓ match_documents RPC
      - cosine distance sort
      - metadata JSONB filter (if project detected)
      - top-k = 5
Retrieved chunks + similarity scores
    ↓ confidence gate
      max(similarity) ≥ 0.72 → proceed to generation
      below 0.72 → trigger human handoff
```

**Confidence threshold 0.72:**
- Calibrated empirically: below 0.72, the best match is usually
  a weak semantic match that leads to hallucination
- You can tune this by logging confidence scores in production
  and checking which answers were wrong
- 0.72 on cosine similarity (0.0–1.0 scale) is roughly equivalent
  to "the query and the chunk share most of their meaning"

---

## Layer 5 — Prompt assembly

The prompt sent to Gemini has four sections:

```
SYSTEM: persona + rules (never invent, cite projects, be concise)
CONTEXT: top-5 retrieved chunks, labeled with project and type
HISTORY: last 3 turns of conversation (for follow-up awareness)
USER: the actual question
```

**Temperature 0.2:** Low temperature makes Gemini stick closely
to the retrieved context rather than free-associating. Higher
temperatures would make answers more creative but less grounded.

**max_tokens 1024:** Enough for a thorough technical answer with
code snippets. Gemini 1.5 Flash can generate 1024 tokens in ~1–2 seconds.

---

## Failure modes and mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| Low retrieval confidence | similarity < 0.72 | Trigger WhatsApp handoff |
| HF API rate limit | 429 response | Exponential backoff, up to 5 retries |
| No chunks returned | empty result set | Handoff immediately |
| Gemini quota hit | API error | Log + return graceful error message |
| WhatsApp send failure | Exception caught | Log, don't crash — user still gets response |

---

## How to add new projects to the knowledge base

1. Create a folder: `docs/knowledge_base/your_project_name/`
2. Add your docs (MD, code files, JSON metadata)
3. Push to GitHub — the ingestion workflow fires automatically
4. New chunks are upserted (existing chunks with same content are skipped)

The `project_name` is inferred from the folder name and injected into
every chunk's metadata automatically by `chunk_document()`.
