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
| **Blog ideas ingest** | [`.github/workflows/blog-ideas.yml`](../.github/workflows/blog-ideas.yml) | Sun/Wed 10:00 UTC + manual | HN + GitHub search → Gemini scores vs `BLOG_FOCUS_TAGS` → inserts `blog_ideas` |
| **Blog draft** | [`.github/workflows/blog-draft.yml`](../.github/workflows/blog-draft.yml) | Mon/Fri 09:00 UTC + manual | Picks oldest **pending** idea (skill tier), writes draft; if queue empty and `BLOG_FALLBACK_GEMINI_IDEA` is true (default), uses Gemini-only idea |
| **Blog publish** | [`.github/workflows/blog-publish.yml`](../.github/workflows/blog-publish.yml) | Daily 12:00 UTC + manual | `review` → `published` only (see below) |

---

## 4. Environment / secrets

**Ideas ingest** (`blog_ideas_ingest.py`):

- Required: `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
- Optional: `GEMINI_MODEL`, `OWNER_NAME`, `PORTFOLIO_URL`, `BLOG_FOCUS_TAGS` (comma-separated focus areas), `BLOG_GITHUB_SEARCH_QUERY`, `BLOG_GITHUB_TOKEN`, `GITHUB_TOKEN` (Actions default helps GitHub API rate limits), `BLOG_HN_LIMIT`, `BLOG_GITHUB_LIMIT`

**Draft** (`blog_draft.py`): same as before; optional `BLOG_FALLBACK_GEMINI_IDEA` (`true`/`false`) — if `false` and no pending ideas, job returns `no_pending_ideas` without calling Gemini.

**Publish** (`blog_publish_reviewed.py`): `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` only (no Gemini).

---

## 5. Human workflow (recommended)

1. **Ingest** fills `blog_ideas` with `pending` rows.
2. **Draft** job creates `blog_posts` with `status=draft` and marks the idea `consumed`.
3. You **edit** the post in Supabase (or a future admin UI).
4. Set **`status=review`** when you are happy (optional: set **`scheduled_publish_at`** for a future go-live).
5. **Publish** job promotes **`review` → `published`** and sets **`published_at`**.

**Never** auto-publish `draft` rows.

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

- Ingest uses **HN Firebase API** and **GitHub Search API**; snippets are short. Do not paste third-party articles wholesale; drafts should be **original** with optional “inspired by” links.
- Add Stack Overflow / Reddit / Dev.to collectors later using their **official APIs** and the same Gemini scoring pattern in [`src/blog/idea_ingest.py`](../src/blog/idea_ingest.py).
