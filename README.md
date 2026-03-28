# Aegis-Agent — Autonomous Portfolio Concierge

> An agentic RAG system that manages your professional portfolio at **yabibal.site** — answering client questions, booking Google Meet calls, and escalating sensitive leads to WhatsApp. Built entirely on free-tier infrastructure.

---

## What it does

- **Intelligent Q&A** — answers complex client questions by reasoning over your real project history (car rental app, CLI tool, etc.) using RAG
- **Calendar & Meet booking** — checks your live Google Calendar availability and generates Google Meet links
- **Human-forward handoff** — when a query is too sensitive (rates, contracts), it captures the lead and sends a structured briefing to your WhatsApp group
- **Lead CRM** — logs every visitor email, intent, and conversation to Supabase
- **Session memory** — remembers context within a conversation so follow-up questions work naturally
- **Confidence-gated answers** — if retrieval confidence is below 0.72, the agent admits uncertainty instead of hallucinating

---

## Architecture overview

```
User → yabibal.site (Next.js chat widget)
         ↓
       Koyeb (FastAPI — always on, no cold start)
         ↓
       LangGraph agent (Plan → Act → Observe loop)
         ├── RAG Tool       → Supabase pgvector
         ├── Calendar Tool  → Google Calendar API + Gmail API
         └── Handoff Tool   → WhatsApp Cloud API
         ↓
       Supabase (vectors + leads + sessions)
```

---

## Free-tier stack

| Component | Technology | Cost |
|---|---|---|
| Orchestration | Python + LangGraph | Free |
| LLM | Gemini 1.5 Flash | Free (Google AI Studio) |
| Embeddings | HuggingFace Inference API (BGE-small) | Free |
| Vector DB | Supabase pgvector | Free tier |
| Leads / Sessions | Supabase PostgreSQL | Free tier |
| Calendar | Google Calendar API | Free |
| Email confirm | Gmail API | Free |
| Handoff | WhatsApp Cloud API | Free (1000 msgs/mo) |
| Hosting | Koyeb | Free (1 service) |
| Doc re-ingestion | GitHub Actions | Free (2000 min/mo) |
| Frontend | Next.js on Vercel | Free tier |

**Total monthly cost: $0**

---

## Repository structure

```
aegis-agent/
├── src/
│   ├── agent/
│   │   ├── graph.py          # LangGraph state graph definition
│   │   ├── state.py          # AgentState TypedDict
│   │   ├── router.py         # Intent classification node
│   │   └── nodes.py          # observe, reflect, respond nodes
│   ├── rag/
│   │   ├── ingestor.py       # Document loading + chunking pipeline
│   │   ├── embedder.py       # HuggingFace embedding wrapper
│   │   ├── retriever.py      # Supabase pgvector query logic
│   │   └── chunkers.py       # Per-type chunking strategies
│   ├── tools/
│   │   ├── rag_tool.py       # LangGraph-compatible RAG tool
│   │   ├── calendar_tool.py  # Google Calendar + Meet tool
│   │   ├── handoff_tool.py   # WhatsApp Cloud API handoff
│   │   └── lead_tool.py      # Supabase lead logger
│   └── api/
│       ├── main.py           # FastAPI app entrypoint
│       ├── routes.py         # /chat, /health endpoints
│       └── middleware.py     # CORS, rate limiting
├── scripts/
│   ├── ingest.py             # Run full ingestion pipeline
│   └── seed_docs.py          # Seed your knowledge base docs
├── tests/
│   ├── test_rag.py
│   ├── test_tools.py
│   └── test_agent.py
├── config/
│   └── settings.py           # Pydantic settings from env vars
├── docs/
│   ├── architecture.md
│   ├── rag_deep_dive.md
│   ├── supabase_setup.md
│   ├── whatsapp_setup.md
│   ├── google_apis_setup.md
│   └── deployment.md
├── .github/
│   └── workflows/
│       └── ingest.yml        # Auto re-ingest on docs change
├── .env.example
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/yourusername/aegis-agent
cd aegis-agent
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Fill in all values — see docs/setup.md

# 3. Set up Supabase schema
# Run SQL from docs/supabase_setup.md in your Supabase SQL editor

# 4. Ingest your knowledge base
python scripts/ingest.py

# 5. Run locally
uvicorn src.api.main:app --reload --port 8000

# 6. Test the agent
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me about your car rental project", "session_id": "test-001"}'
```

---

## Environment variables

See `.env.example` for all required variables with descriptions.

---

## Docs index

- [Architecture deep dive](docs/architecture.md)
- [RAG system explained](docs/rag_deep_dive.md)
- [Supabase setup](docs/supabase_setup.md)
- [WhatsApp Cloud API setup](docs/whatsapp_setup.md)
- [Google APIs setup](docs/google_apis_setup.md)
- [Deployment to Koyeb](docs/deployment.md)
