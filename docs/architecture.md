# Architecture — Aegis-Agent

## System overview

Aegis-Agent is a stateful agentic system built on LangGraph's
Plan → Act → Observe loop. Unlike a simple chatbot (input → LLM → output),
the agent can use tools, evaluate its own output quality, and re-plan
before responding.

---

## The reasoning loop

```
User message
    ↓
Router node          → classify intent, extract project filter
    ↓
Tool node            → RAG | Calendar | Handoff (one per turn)
    ↓
Observe node         → was the action sufficient?
    ↓ (conditional edge)
Respond node  ←──── if confident and complete
    OR
Handoff node  ←──── if confidence < 0.72 or sensitive query
```

The `iterations` counter guards against infinite loops (max 5 iterations).

---

## Intent classification

The router runs a Gemini micro-call (~100ms) to classify every message
into one of three intents. Hard-coded keyword triggers fire before the LLM
call for known sensitive terms (rates, pricing, contract) — this is
faster and more reliable than relying on the LLM for obvious cases.

| Intent | Trigger | Tool called |
|---|---|---|
| `general_qa` | Questions about projects, skills, experience | RAG tool |
| `book_meeting` | Schedule, call, demo, availability | Calendar tool |
| `handoff` | Rates, contract, hiring, pricing | Handoff tool |

---

## Data flow (ingestion vs query time)

**Ingestion (runs once, then on each docs change via GitHub Actions):**
```
docs/knowledge_base/**
    → chunk_document() (type-aware chunking)
    → embed_texts() (HuggingFace BGE)
    → supabase.upsert() (pgvector table)
```

**Query time (runs on every user message, ~500ms end-to-end):**
```
user message
    → embed_query() (same BGE model, with task prefix)
    → match_documents RPC (pgvector cosine search + metadata filter)
    → confidence gate (threshold 0.72)
    → prompt assembly (system + context + history + question)
    → Gemini 1.5 Flash (temp 0.2, streaming)
    → response + source attribution
```

---

## Session management

Each browser session gets a UUID on first message. Conversation history
is persisted in Supabase's `sessions` table and loaded on each request.
This enables natural follow-up questions like "how long did that take?"
after asking about a specific project.

The last 3 turns (6 messages) are included in the prompt — enough for
context without bloating the token count.

---

## Self-healing design

The system handles its own failure modes without going down:

1. **Low RAG confidence** → handoff to human instead of hallucinating
2. **Empty retrieval** → handoff immediately
3. **HF API down** → retry with exponential backoff (tenacity)
4. **Telegram send fails** → logged silently, user still gets a response
5. **Gemini quota hit** → graceful error message returned to user
6. **Infinite loop guard** → `iterations >= 5` forces respond node

---

## API contract

**POST /chat**

Request:
```json
{
  "message": "Tell me about your car rental app",
  "session_id": "optional-uuid-for-continuity"
}
```

Response:
```json
{
  "response": "The car rental app is a...",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "sources": ["car_rental_app"],
  "intent": "general_qa",
  "confidence": 0.89
}
```

**GET /health**

Response:
```json
{"status": "ok", "agent": "aegis-agent", "version": "1.0.0"}
```

---

## Why LangGraph over a simple chain?

LangGraph gives you:
- **Stateful execution** — the graph state persists between nodes,
  so each node can read what previous nodes did
- **Conditional edges** — routing decisions are explicit and debuggable,
  not buried in prompt engineering
- **Cycle support** — the observe → re-plan loop is a native graph cycle,
  not a hack
- **Streaming** — LangGraph supports `astream_events` for streaming
  both tokens and tool call updates to the frontend

A simple LangChain chain (prompt → LLM → output) can't do any of this.
