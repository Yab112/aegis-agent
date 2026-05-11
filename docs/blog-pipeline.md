# Blog pipeline: ideas → drafts → review → publish

End-to-end flow for automated drafts aligned with your stack, plus a **safe** publish step that never promotes raw `draft` rows.

---

## 1. Supabase schema

Run in order (SQL Editor):

1. [`scripts/supabase_blog_posts.sql`](../scripts/supabase_blog_posts.sql) — `blog_posts` (if not already).
2. [`scripts/supabase_blog_ideas.sql`](../scripts/supabase_blog_ideas.sql) — `blog_ideas` queue + optional `blog_posts.scheduled_publish_at`.

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

## 3. GitHub Actions

| Workflow | File | When | What |
|----------|------|------|------|
| **Blog ideas ingest** | [`.github/workflows/blog-ideas.yml`](../.github/workflows/blog-ideas.yml) | Sun/Wed 10:00 UTC + manual | Multi-source round-robin (HN, GitHub, Dev.to, Reddit, Stack Overflow) with per-source fallback → Gemini scores vs `BLOG_FOCUS_TAGS` → inserts `blog_ideas` |
| **Blog draft** | [`.github/workflows/blog-draft.yml`](../.github/workflows/blog-draft.yml) | Every 5 days 09:00 UTC (`0 9 */5 * *`) + manual | Picks oldest **pending** idea (skill tier), writes post, generates an image prompt + cover image via Gemini, uploads to Cloudinary, then inserts post. If queue empty and `BLOG_FALLBACK_GEMINI_IDEA` is true (default), uses Gemini-only idea. **`BLOG_POST_STATUS_AFTER_WRITE`** (see below) controls `draft` / `review` / **`published`** (live immediately). |
| **Blog publish** | [`.github/workflows/blog-publish.yml`](../.github/workflows/blog-publish.yml) | Daily 12:00 UTC + manual | `review` → `published` only (see below) |

---

## 4. Environment / secrets

**Ideas ingest** (`blog_ideas_ingest.py`):

- Required: `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
- Optional: `GEMINI_MODEL`, `OWNER_NAME`, `PORTFOLIO_URL`, `BLOG_FOCUS_TAGS` (comma-separated focus areas), `BLOG_GITHUB_SEARCH_QUERY`, `BLOG_GITHUB_TOKEN`, `GITHUB_TOKEN` (Actions default helps GitHub API rate limits), `BLOG_HN_LIMIT`, `BLOG_GITHUB_LIMIT`, `BLOG_DEVTO_LIMIT`, `BLOG_REDDIT_LIMIT`, `BLOG_STACKOVERFLOW_LIMIT`, `BLOG_MAX_CANDIDATES`

**Draft** (`blog_draft.py`): optional `BLOG_FALLBACK_GEMINI_IDEA` (`true`/`false`) — if `false` and no pending ideas, job returns `no_pending_ideas` without calling Gemini.

**Draft image generation** (same `blog-draft.yml` schedule):

- `BLOG_GENERATE_IMAGES` (default `true`) — enable/disable generated covers.
- `BLOG_IMAGE_MODEL` (default `gemini-2.5-flash-image`) — Gemini **native image** model; the pipeline requests image output modality.
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `CLOUDINARY_FOLDER` — upload destination.
- If Cloudinary or image generation fails, pipeline falls back to `BLOG_DEFAULT_OG_IMAGE` and still inserts the post (you may still see `image_prompt` filled from the text step).

**Post status (no manual review):** `BLOG_POST_STATUS_AFTER_WRITE`:

| Value | Behavior |
|--------|----------|
| `draft` (default) | Row stays internal until you change status in Supabase. |
| `review` | Queued for **Blog publish** cron (`review` → `published`) with zero editing. |
| `published` | Sets `published_at` immediately; post is public on the next API read. |

The scheduled workflow sets `BLOG_POST_STATUS_AFTER_WRITE=published` so runs are fully hands-off. Override locally with `draft` or `review` if you want a gate.

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

- **`image_prompt` set but site shows default OG image** — the text model always writes the visual brief; the cover still needs (1) Gemini returning **inline image bytes** and (2) **Cloudinary** upload. Check Render/Actions logs for `cover image:` / `cloudinary` / `blog cover: slug=… using fallback`.
- **Dead-letter table** (after running [`scripts/supabase_blog_ideas.sql`](../scripts/supabase_blog_ideas.sql)): `select id, created_at, stage, error_message, slug from public.blog_pipeline_failures order by created_at desc limit 20;` — look for `cover_generation` or `insert_post`.
- **GitHub Actions**: repository **Actions** tab → **Blog draft (scheduled)** → latest run → open **Generate blog draft** step; green checkmark only means the script exited 0 (a post can still use the fallback image).

---

## 5. Human workflow (optional)

**Fully automated (default in `blog-draft.yml`):** set `BLOG_POST_STATUS_AFTER_WRITE=published` so new posts go live without opening Supabase.

**If you use `draft` or `review` instead:**

1. **Ingest** fills `blog_ideas` with `pending` rows.
2. **Draft** job creates `blog_posts` and marks the idea `consumed`.
3. Optionally **edit** in Supabase; set **`status=review`** (if you started from `draft`).
4. Optional: **`scheduled_publish_at`** for a future go-live when using the publish job.
5. **Publish** job promotes **`review` → `published`** and sets **`published_at`** when due.

The publish job **never** promotes raw `draft` rows (only `review` → `published`). Use `BLOG_POST_STATUS_AFTER_WRITE=review` to automate that handoff without editing content.

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
