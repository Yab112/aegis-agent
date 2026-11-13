# Blog pipeline: ideas → drafts → review → publish

End-to-end flow for automated drafts aligned with your stack, plus a **safe** publish step that never promotes raw `draft` rows.

---

## 1. Supabase schema

Run in order (SQL Editor):

1. [`scripts/supabase_blog_posts.sql`](../scripts/supabase_blog_posts.sql) — `blog_posts` (if not already).
2. [`scripts/supabase_blog_ideas.sql`](../scripts/supabase_blog_ideas.sql) — `blog_ideas` queue + optional `blog_posts.scheduled_publish_at`.

If the draft job fails with `column blog_posts.pipeline_run_key does not exist`, run once:

[`scripts/supabase_blog_posts_pipeline_run_key.sql`](../scripts/supabase_blog_posts_pipeline_run_key.sql)

---

## 2. Tables

### `blog_ideas` (internal)

| Column | Purpose |
|--------|---------|
| `source` | `hackernews`, `github`, … |
| `source_url`, `source_id` | Dedup / attribution |
| `title`, `raw_excerpt`, `reference_jsonb` | Short signal from APIs (not full articles) |
| `normalized_tags`, `topic_key`, `angle` | For the writer |
| `skill_fit` | `high` / `medium` / `low` (ingest keeps high/medium) |
| `status` | `pending` → `consumed` / `skipped` / `failed` |
| `consumed_by_post_id` | Links to `blog_posts` after draft insert |

Unique: one **pending** row per `topic_key`; unique `(source, source_id)` when `source_id` is set.

### `blog_posts`

Existing `draft` → `review` → `published`. **`scheduled_publish_at`** (optional): if set, the publish job only goes live when `now() >= scheduled_publish_at` (UTC).

---

## 3. Scheduler (runs inside the API)

The pipeline no longer depends on GitHub Actions. The FastAPI process runs its own scheduler
([`src/scheduler/`](../src/scheduler/)) and keeps its state in Supabase, so nothing external has to be up for posts to ship.

| Job | Default schedule (UTC) | Env override | What |
|-----|------------------------|--------------|------|
| `blog_ideas` | Sun/Wed 10:00 (`0 10 * * 0,3`) | `SCHEDULER_BLOG_IDEAS_CRON` | Multi-source round-robin (HN, GitHub, Dev.to, Reddit, Stack Overflow) with per-source fallback → Gemini scores vs `BLOG_FOCUS_TAGS` → inserts `blog_ideas` |
| `blog_draft` | Every 5 days 09:00 (`0 9 */5 * *`) | `SCHEDULER_BLOG_DRAFT_CRON` | Picks oldest **pending** idea (skill tier), writes the post, generates a cover via Gemini, uploads to Cloudinary, inserts the post. Status comes from `SCHEDULER_BLOG_DRAFT_STATUS` (default **`published`**). |
| `blog_publish` | Daily 12:00 (`0 12 * * *`) | `SCHEDULER_BLOG_PUBLISH_CRON` | `review` → `published` only (see below) |

### One-time setup

1. Run [`scripts/supabase_scheduler.sql`](../scripts/supabase_scheduler.sql) in the Supabase SQL Editor
   (creates `scheduler_jobs`, `scheduler_job_runs` and the `scheduler_claim_job` lock function).
2. Copy the secrets that used to live in GitHub → repository secrets onto the **API host** as environment
   variables: `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `CLOUDINARY_FOLDER=blog`,
   `BLOG_DEFAULT_OG_IMAGE`, `BLOG_FOCUS_TAGS`, `BLOG_GITHUB_SEARCH_QUERY`, `BLOG_GITHUB_TOKEN`,
   `BLOG_PUBLISH_WEBHOOK_URL`, `BLOG_PUBLISH_WEBHOOK_SECRET`, `BLOG_MAX_OUTPUT_TOKENS=8192`.
   (`GEMINI_*`, `SUPABASE_*`, `OWNER_NAME`, `PORTFOLIO_URL` are already there for the chat API.)
3. Deploy. On boot the log prints `scheduler: started jobs=…`.

### How it stays reliable on free-tier hosting

- **Durable slots.** Each job stores the last schedule slot it handled. Every tick (60s, and 5s after boot) asks
  "has the most recent slot been handled?" — so a schedule missed during a restart or sleep runs as soon as the
  process is alive again. Several missed slots collapse into one catch-up run.
- **Exactly once.** A Postgres-side lease (`scheduler_claim_job`) means two instances can never run the same job at
  once, and the draft job's `pipeline_run_key` is derived from the slot, so a repeated slot is a no-op.
- **Retries.** A failed scheduled run retries after 10, then 20 minutes (`SCHEDULER_RETRY_BACKOFF_MINUTES`,
  `SCHEDULER_MAX_ATTEMPTS=3`), then gives up until the next slot. Gemini quota errors are retried inside the run.
- **Keep-awake.** With `SCHEDULER_KEEP_AWAKE=true` (default) the service pings its own `/health/livez` every
  10 minutes so the host does not sleep through a schedule. The URL comes from `PUBLIC_BASE_URL`, or the
  host-provided `RENDER_EXTERNAL_URL` / `KOYEB_PUBLIC_DOMAIN`.
- **Alerts.** With `SCHEDULER_NOTIFY_TELEGRAM=true` (default) you get a Telegram message when a post is published
  and when a job gives up — the replacement for GitHub's failed-workflow emails.
- **First boot** does not back-fill: jobs wait for their next slot unless `SCHEDULER_RUN_MISSED_ON_FIRST_BOOT=true`.
  To publish something right away, trigger the job manually (below).

### Operating it

All control endpoints need `Authorization: Bearer $API_SECRET_KEY` (or `X-Internal-Secret`).

```bash
API=https://your-api-host
# State of every job + the last 20 runs
curl -H "Authorization: Bearer $API_SECRET_KEY" $API/internal/scheduler/status
# Run a job now (background; does not consume its schedule slot)
curl -X POST -H "Authorization: Bearer $API_SECRET_KEY" $API/internal/scheduler/jobs/blog_draft/run
# Pause / resume a schedule
curl -X POST -H "Authorization: Bearer $API_SECRET_KEY" $API/internal/scheduler/jobs/blog_draft/pause
curl -X POST -H "Authorization: Bearer $API_SECRET_KEY" $API/internal/scheduler/jobs/blog_draft/resume
```

`GET /automation/status` is public and sanitized (job titles, schedule, last status, next run) — safe to render on the site.
Run history lives in `scheduler_job_runs`; set `SCHEDULER_ENABLED=false` to turn the scheduler off entirely.

---

## 4. Environment / secrets

**Ideas ingest** (`blog_ideas_ingest.py`):

- Required: `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
- Optional: `GEMINI_MODEL`, `OWNER_NAME`, `PORTFOLIO_URL`, `BLOG_FOCUS_TAGS` (comma-separated focus areas), `BLOG_GITHUB_SEARCH_QUERY`, `BLOG_GITHUB_TOKEN`, `BLOG_HN_LIMIT`, `BLOG_GITHUB_LIMIT`, `BLOG_DEVTO_LIMIT`, `BLOG_REDDIT_LIMIT`, `BLOG_STACKOVERFLOW_LIMIT`, `BLOG_MAX_CANDIDATES`

**Draft** (`blog_draft.py`): optional `BLOG_FALLBACK_GEMINI_IDEA` (`true`/`false`) — if `false` and no pending ideas, job returns `no_pending_ideas` without calling Gemini.

**Draft image generation** (same `blog_draft` job):

- `BLOG_GENERATE_IMAGES` (default `true`) — enable/disable generated covers.
- `BLOG_IMAGE_MODEL` (default `gemini-2.5-flash-image`) — Gemini **native image** model; the pipeline requests image output modality.
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `CLOUDINARY_FOLDER` — upload destination.
- If Cloudinary or image generation fails, pipeline falls back to `BLOG_DEFAULT_OG_IMAGE` and still inserts the post (you may still see `image_prompt` filled from the text step).

**Post status (no manual review):** `BLOG_POST_STATUS_AFTER_WRITE`:

| Value | Behavior |
|--------|----------|
| `draft` (default) | Row stays internal until you change status in Supabase. |
| `review` | Queued for the **`blog_publish`** job (`review` → `published`) with zero editing. |
| `published` | Sets `published_at` immediately; post is public on the next API read. |

The scheduled `blog_draft` job uses `SCHEDULER_BLOG_DRAFT_STATUS` (default `published`) so runs are fully hands-off; set it to `draft` or `review` if you want a gate. `BLOG_POST_STATUS_AFTER_WRITE` still applies to manual runs and local scripts.

**Publish webhook (optional):** called when a post transitions to `published` (either direct publish from draft pipeline or review publish job).

- `BLOG_PUBLISH_WEBHOOK_URL` — destination URL.
- `BLOG_PUBLISH_WEBHOOK_SECRET` — optional HMAC secret; signature is sent in `X-Aegis-Signature`.
- `BLOG_PUBLISH_WEBHOOK_TIMEOUT_SECONDS` — request timeout (default 10).
- `BLOG_PUBLISH_WEBHOOK_MAX_ATTEMPTS` — retry attempts (default 3).
- `BLOG_PUBLISH_WEBHOOK_BACKOFF_SECONDS` — linear backoff base (default 2).

Event payload key points:

- `event`: `blog_post_published`
- `source`: `draft_pipeline` or `review_publish_job`
- `post`: id, slug, title, topic_key, published_at, tags, image_url

**Publish** (`blog_publish_reviewed.py`): `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` only (no Gemini).

### Debugging covers and CI

- **`image_prompt` set but site shows default OG image** — the text model always writes the visual brief; the cover still needs (1) Gemini returning **inline image bytes** and (2) **Cloudinary** upload. Check the API host logs for `cover image:` / `cloudinary` / `blog cover: slug=… using fallback`.
- **Dead-letter table** (after running [`scripts/supabase_blog_ideas.sql`](../scripts/supabase_blog_ideas.sql)): `select id, created_at, stage, error_message, slug from public.blog_pipeline_failures order by created_at desc limit 20;` — look for `cover_generation` or `insert_post`.
- **Run history**: `GET /internal/scheduler/status` (or the `scheduler_job_runs` table) shows each run's status, duration, result JSON and error; a `success` run can still have used the fallback image.

---

## 5. Human workflow (optional)

**Fully automated (default):** `SCHEDULER_BLOG_DRAFT_STATUS=published` — new posts go live without opening Supabase.

**If you use `draft` or `review` instead:**

1. **Ingest** fills `blog_ideas` with `pending` rows.
2. **Draft** job creates `blog_posts` and marks the idea `consumed`.
3. Optionally **edit** in Supabase; set **`status=review`** (if you started from `draft`).
4. Optional: **`scheduled_publish_at`** for a future go-live when using the publish job.
5. **Publish** job promotes **`review` → `published`** and sets **`published_at`** when due.

The publish job **never** promotes raw `draft` rows (only `review` → `published`). Use `SCHEDULER_BLOG_DRAFT_STATUS=review` to automate that handoff without editing content.

---

## 6. Local scripts

```bash
python scripts/blog_ideas_ingest.py
python scripts/blog_draft.py
python scripts/blog_publish_reviewed.py
```

---

## 7. Public API

Published posts only: see [blog-api.md](blog-api.md).

---

## 8. Legal / quality notes

- Ingest uses official/public APIs from **HN**, **GitHub**, **Dev.to**, **Reddit**, and **Stack Overflow** with isolated `try/except` per source. One source failing does not stop ingestion.
- Candidates are merged in **round-robin** order to avoid over-dependence on a single source and reduce scraping-pattern risk.
- Drafted posts now include `resource_links` JSON for "learn more" references (inspiration URL + curated docs by tag).
- Snippets are short; do not paste third-party articles wholesale. Keep drafts original with attribution where relevant.
