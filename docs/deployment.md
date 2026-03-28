# Deployment guide — Koyeb (free tier)

Koyeb's free tier gives you one always-on service with no cold starts,
unlike Render's free tier which sleeps after 15 minutes of inactivity.
This is critical for a portfolio agent that needs to respond instantly.

---

## Free tier specs

| Resource | Value |
|---|---|
| Services | 1 |
| RAM | 512 MB |
| CPU | Shared |
| Bandwidth | 100 GB/month |
| Sleep | Never |
| Custom domain | Yes (free) |

---

## Step 1 — Push your code to GitHub

```bash
git init
git add .
git commit -m "initial: aegis-agent"
git remote add origin https://github.com/yourusername/aegis-agent
git push -u origin main
```

---

## Step 2 — Create a Koyeb account

Go to https://www.koyeb.com and sign up (free, no credit card required).

---

## Step 3 — Create a new service

1. Click **Create Service → GitHub**
2. Connect your GitHub account and select the `aegis-agent` repo
3. Branch: `main`

**Build settings:**
- Builder: **Dockerfile** (auto-detected)
- Dockerfile path: `Dockerfile`

**Run settings:**
- Port: `8000`
- Health check path: `/health`

---

## Step 4 — Add environment variables

In the Koyeb service settings, add ALL variables from your `.env`:

```
GEMINI_API_KEY
HF_API_TOKEN
EMBEDDING_MODEL
SUPABASE_URL
SUPABASE_SERVICE_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN
GOOGLE_CALENDAR_ID
CALENDAR_TIMEZONE
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_ACCESS_TOKEN
WHATSAPP_RECIPIENT_NUMBER
RAG_TOP_K
RAG_CONFIDENCE_THRESHOLD
RAG_CHUNK_SIZE
RAG_CHUNK_OVERLAP
API_SECRET_KEY
ALLOWED_ORIGINS
OWNER_NAME
OWNER_ROLE
PORTFOLIO_URL
```

---

## Step 5 — Deploy

Click **Deploy**. Koyeb will:
1. Pull your repo
2. Build the Docker image
3. Run the container
4. Start health checks on `/health`

First deploy takes ~3 minutes. Subsequent deploys (on `git push`) are ~90 seconds.

---

## Step 6 — Get your public URL

Koyeb gives you a URL like:
```
https://aegis-agent-yourusername.koyeb.app
```

Set this as your API base URL in your Next.js portfolio frontend.

---

## Step 7 — Add GitHub secrets for the ingestion workflow

In your GitHub repo → **Settings → Secrets → Actions**, add:

```
GEMINI_API_KEY
HF_API_TOKEN
EMBEDDING_MODEL
SUPABASE_URL
SUPABASE_SERVICE_KEY
```

These are the only secrets the ingestion workflow needs. The others
(WhatsApp, Google, etc.) are only needed by the live API on Koyeb.

---

## Verify deployment

```bash
curl https://aegis-agent-yourusername.koyeb.app/health
# → {"status":"ok","agent":"aegis-agent","version":"1.0.0"}

curl -X POST https://aegis-agent-yourusername.koyeb.app/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me about your projects"}'
```

---

## Auto-deploy on push

Koyeb automatically re-deploys when you push to `main`.
The GitHub Actions ingestion workflow runs in parallel when
`docs/knowledge_base/**` files change.
