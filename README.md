# Aegis-Agent — Autonomous Portfolio Concierge

> An agentic RAG system that runs **yabibal.site** — answering client questions, booking Google Meet calls, escalating sensitive leads to WhatsApp, and auto-generating blog drafts. Built entirely on free-tier infrastructure.

**Live site:** [yabibal.site](https://yabibal.site) · **Contact / custom work:** [yabibal.site](https://yabibal.site)

---

## What it does

| Capability | Details |
|---|---|
| **Intelligent Q&A** | Answers questions about Yabibal's projects, skills, and background using RAG over a curated knowledge base |
| **Calendar & Meet booking** | Checks live Google Calendar availability and generates confirmed Google Meet links |
| **WhatsApp handoff** | Captures leads and sends a structured briefing via WhatsApp Cloud API when commercial topics (rates, contracts, hiring) are detected |
| **Auto blog drafts** | Scheduled GitHub Action proposes a topic, writes a full Markdown post with Gemini, and inserts it into Supabase as a draft |
| **Lead CRM** | Logs every visitor email, intent, and conversation turn to Supabase |
| **Session memory** | Remembers context within a conversation so follow-up questions work naturally |
| **Confidence-gated answers** | If retrieval confidence is below 0.72, the agent admits uncertainty instead of hallucinating |

---

## Architecture overview

```
User → yabibal.site (Next.js chat widget)
         ↓
       Koyeb (FastAPI + Hypercorn — always on, no cold start)
         ↓
       LangGraph agent  ──  router_node (Gemini classifies intent)
         ├── RAG node        → Supabase pgvector (BGE-small embeddings)
         ├── Calendar node   → Google Calendar API + Google Meet
         └── Handoff node    → WhatsApp Cloud API
         ↓     ↓     ↓
       observe_node → respond_node → END
         ↓
       Supabase (vectors · leads · sessions · blog_posts)
```

### Intent routing

The router classifies every message into one of three intents using Gemini (with fast keyword short-circuits before the LLM call):

| Intent | Trigger | Action |
|---|---|---|
| `general_qa` | Greetings, skills, projects, background | RAG → Gemini response |
| `book_meeting` | Scheduling, availability | Google Calendar → Meet link |
| `handoff` | Rates, contracts, salary, payment | Capture lead → WhatsApp briefing |

---

## Free-tier stack

| Component | Technology | Cost |
|---|---|---|
| API server | FastAPI + Hypercorn on Koyeb | Free (1 service) |
| Orchestration | Python + LangGraph | Free |
| LLM | Gemini 2.5 Flash (Google AI Studio) | Free |
| Embeddings | HuggingFace Inference API — `BAAI/bge-small-en-v1.5` | Free |
| Vector DB | Supabase pgvector | Free tier |
| Leads / Sessions / Blog | Supabase PostgreSQL | Free tier |
| Calendar + Meet | Google Calendar API + Gmail API | Free |
| Handoff | WhatsApp Cloud API | Free (1 000 msgs/mo) |
| Automation | GitHub Actions (2 workflows) | Free (2 000 min/mo) |
| Frontend | Next.js on Vercel | Free tier |

**Total monthly cost: $0**

---

## Repository structure

```
aegis-agent/
├── src/
│   ├── agent/
│   │   ├── graph.py          # LangGraph state graph — router → tool → observe → respond
│   │   ├── state.py          # AgentState TypedDict
│   │   ├── router.py         # Intent classification node (keyword + Gemini)
│   │   └── nodes.py          # rag_node, calendar_node, handoff_node, observe_node, respond_node
│   ├── rag/
│   │   ├── embedder.py       # HuggingFace BGE-small embedding wrapper
│   │   ├── retriever.py      # Supabase pgvector query logic
│   │   └── chunkers.py       # Per-type chunking strategies
│   ├── tools/
│   │   ├── rag_tool.py       # LangGraph-compatible RAG tool
│   │   ├── calendar_tool.py  # Google Calendar + Meet tool
│   │   ├── handoff_tool.py   # WhatsApp Cloud API handoff
│   │   └── lead_tool.py      # Supabase lead logger + session store
│   ├── blog/
│   │   └── pipeline.py       # Twice-weekly blog draft pipeline (topic → write → insert)
│   └── api/
│       ├── main.py           # FastAPI app — lifespan, CORS, route mounts
│       ├── routes.py         # POST /chat, GET /health
│       ├── blog_routes.py    # GET /blog, GET /blog/{slug}  (public)
│       ├── blog_internal.py  # POST /internal/blog/generate-draft  (auth required)
│       └── logging_config.py # ASGI access log middleware + aegis-access.log
├── scripts/
│   ├── ingest.py             # Run full knowledge-base ingestion pipeline
│   ├── seed_docs.py          # Seed initial knowledge-base documents
│   └── blog_draft.py         # CLI entry-point for the blog draft pipeline
├── tests/
│   ├── test_rag.py
│   ├── test_tools.py
│   └── test_agent.py
├── config/
│   └── settings.py           # Pydantic settings loaded from env vars
├── docs/
│   ├── architecture.md
│   ├── rag_deep_dive.md
│   ├── supabase_setup.md
│   ├── whatsapp_setup.md
│   ├── google_apis_setup.md
│   └── deployment.md
├── public/
│   └── chat.html             # Browser chat tester (GET /chat-ui)
├── .github/
│   └── workflows/
│       ├── ingest.yml        # Auto re-ingest when docs/knowledge_base/** changes
│       └── blog-draft.yml    # Scheduled blog draft generation (every 2 days, 09:00 UTC)
├── .env.example
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Service index with all endpoint links |
| `GET` | `/health` | Health check |
| `POST` | `/chat` | Main chat endpoint |
| `GET` | `/chat-ui` | Browser chat tester (serves `public/chat.html`) |
| `GET` | `/blog` | List published blog posts |
| `GET` | `/blog/{slug}` | Single blog post by slug |
| `POST` | `/internal/blog/generate-draft` | Trigger blog draft generation (auth required) |
| `GET` | `/docs` | Interactive Swagger UI |

---

## GitHub Actions workflows

### `ingest.yml` — Knowledge base re-ingestion

Triggers automatically on any push that modifies `docs/knowledge_base/**`, or manually from the GitHub UI.

```
push to docs/knowledge_base/** ──► checkout → install → python scripts/ingest.py
```

Required secrets: `GEMINI_API_KEY`, `HF_API_TOKEN`, `EMBEDDING_MODEL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`

### `blog-draft.yml` — Scheduled blog draft generation

Runs every other day at 09:00 UTC (or on demand). Uses Gemini to propose a topic, write a full article, and insert it into Supabase as a draft.

```
cron: "0 9 */2 * *" ──► checkout → install → python scripts/blog_draft.py
```

Required secrets: all of the above plus `GEMINI_MODEL`, `OWNER_NAME`, `PORTFOLIO_URL`, `BLOG_DEFAULT_OG_IMAGE`

> Want more automation? Reach out at [yabibal.site](https://yabibal.site) and I'll add it.

---

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/Yab112/aegis-agent
cd aegis-agent
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Edit .env — every required key has a comment pointing to the relevant console

# 3. Set up Supabase schema
# Run the SQL from docs/supabase_setup.md in your Supabase SQL editor

# 4. Ingest your knowledge base
python scripts/ingest.py

# 5. Run locally (choose either)
uvicorn src.api.main:app --reload --port 8000
# or
hypercorn src.api.main:app --reload --bind 0.0.0.0:8000

# 6. Open the browser chat tester
open http://localhost:8000/chat-ui

# 7. Or hit the API directly
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me about your car rental project"}'
```

---

## Docker

```bash
# Build
docker build -t aegis-agent .

# Run (pass env file)
docker run --env-file .env -p 8000:8000 aegis-agent

# Or with docker-compose
docker-compose up
```

---

## Environment variables

See `.env.example` for all required variables. Key groups:

| Group | Variables |
|---|---|
| LLM | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| Embeddings | `HF_API_TOKEN`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` |
| Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` |
| Google Calendar / Gmail | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `GOOGLE_CALENDAR_ID`, `CALENDAR_TIMEZONE` |
| WhatsApp | `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_RECIPIENT_NUMBER` |
| RAG tuning | `RAG_TOP_K`, `RAG_CONFIDENCE_THRESHOLD`, `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP` |
| API | `API_SECRET_KEY`, `ALLOWED_ORIGINS`, `PORT`, `LOG_LEVEL` |
| Portfolio owner | `OWNER_NAME`, `OWNER_ROLE`, `PORTFOLIO_URL`, `ASSISTANT_NAME` |

---

## Docs index

- [Architecture deep dive](docs/architecture.md)
- [RAG system explained](docs/rag_deep_dive.md)
- [Supabase setup](docs/supabase_setup.md)
- [WhatsApp Cloud API setup](docs/whatsapp_setup.md)
- [Google APIs setup](docs/google_apis_setup.md)
- [Deployment to Koyeb](docs/deployment.md)

---

## Contact

Built and maintained by **Yabibal** · [yabibal.site](https://yabibal.site)

Want a custom automation, a new tool node, or to deploy this for your own portfolio? Reach out via the site.
