# Blog API — frontend reference

Read-only HTTP API for **published** blog posts stored in Supabase. Drafts and `review` rows are **never** returned.

**Base URL:** same as the main Aegis API (no path prefix on the host).

- **Production:** `https://aegis-agent-5omj.onrender.com`
- **Local:** `http://127.0.0.1:8000`

**OpenAPI:** `GET /docs` and `GET /openapi.json` on that host include these routes under the **blog** tag.

**Pipeline (ideas, drafts, publish cron):** [blog-pipeline.md](blog-pipeline.md)

**CORS:** Same as the rest of the API — your frontend origin (e.g. `https://yabibal.site`) must appear in server **`ALLOWED_ORIGINS`**. See [frontend-api.md](frontend-api.md#cors).

**Auth:** None. These routes are public.

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/blog` | Paginated list of published posts |
| `GET` | `/blog/{slug}` | Single post by URL slug (full Markdown) |

---

## `GET /blog`

Returns posts with **`status = published`**, newest first by `published_at`.

### Query parameters

| Name | Type | Default | Constraints |
|------|------|---------|-------------|
| `page` | integer | `1` | ≥ 1 |
| `page_size` | integer | `10` | 1–50 |

Example: `GET /blog?page=1&page_size=10`

### Response `200 OK`

`Content-Type: application/json`

| Field | Type | Description |
|-------|------|-------------|
| `items` | array | List of summary objects (no `body_md`) |
| `page` | number | Echo of requested page |
| `page_size` | number | Echo of requested page size |
| `total` | number | Count of all published posts (for pagination UI) |

Each **item** in `items`:

| Field | Type | Description |
|-------|------|-------------|
| `slug` | string | URL segment for the detail page, e.g. `/blog/my-post` on your site → fetch `/blog/my-post` from API |
| `title` | string | Headline |
| `description` | string | Short line for cards, SEO meta description |
| `published_at` | string \| null | ISO 8601 timestamp from DB |
| `tags` | string[] | Lowercase tags |
| `image_url` | string \| null | Optional cover URL |
| `image_alt` | string \| null | Cover alt text |
| `og_image_url` | string \| null | Optional Open Graph image |

### Example response

```json
{
  "items": [
    {
      "slug": "pivoting-to-python-ai-security-and-orchestration",
      "title": "Pivoting fully into Python and AI",
      "description": "Why I am doubling down on Python and AI…",
      "published_at": "2026-03-25T12:00:00+00:00",
      "tags": ["career", "python", "ai"],
      "image_url": null,
      "image_alt": null,
      "og_image_url": null
    }
  ],
  "page": 1,
  "page_size": 10,
  "total": 2
}
```

### Pagination UI

- **Total pages:** `Math.ceil(total / page_size)` (guard `total === 0`).
- **Next page:** increment `page` until `items.length === 0` or `(page - 1) * page_size + items.length >= total`.

---

## `GET /blog/{slug}`

Returns one **published** post. `{slug}` is the same string as `item.slug` from the list.

Example: `GET /blog/pivoting-to-python-ai-security-and-orchestration`

### Response `200 OK`

| Field | Type | Description |
|-------|------|-------------|
| `slug` | string | Post slug |
| `title` | string | Headline |
| `description` | string | Subtitle / meta description |
| `body_md` | string | **Full article in Markdown** — render with your MD component |
| `published_at` | string \| null | ISO 8601 |
| `updated_at` | string \| null | ISO 8601 |
| `tags` | string[] | Tags |
| `image_url` | string \| null | Cover |
| `image_alt` | string \| null | Cover alt |
| `og_image_url` | string \| null | Social preview image |
| `canonical_url` | string \| null | If set, prefer for `<link rel="canonical">` |

### Example response

```json
{
  "slug": "human-feedback-rl-and-turing",
  "title": "Human-in-the-loop and RLHF",
  "description": "Notes on RLHF-style loops…",
  "body_md": "## Humans in the loop\n\n…",
  "published_at": "2026-03-27T12:00:00+00:00",
  "updated_at": "2026-03-27T12:00:00+00:00",
  "tags": ["rlhf", "ai"],
  "image_url": null,
  "image_alt": null,
  "og_image_url": null,
  "canonical_url": null
}
```

### Errors

**`404 Not Found`** — no published post with that slug (drafts are invisible):

```json
{ "detail": "Post not found" }
```

---

## Frontend checklist

1. **Base URL** — Use your deployed API origin, **no** trailing slash: `` `${API}/blog` ``.
2. **List page** — `GET /blog?page=1&page_size=12`; link each card to your route using `slug`.
3. **Detail page** — `GET /blog/${encodeURIComponent(slug)}`; render `body_md` (e.g. `react-markdown`, `marked`, MDX pipeline).
4. **SEO** — Set `<title>` from `title`, meta description from `description`, OG image from `og_image_url` or fallback `image_url`; use `canonical_url` when present.
5. **Drafts** — You will not see unpublished posts here; publish in Supabase (`status`, `published_at`) when ready.

---

## Copy-paste examples

### List + detail (`fetch`)

```javascript
const API = "https://aegis-agent-5omj.onrender.com";

export async function fetchBlogList(page = 1, pageSize = 10) {
  const u = new URL(`${API}/blog`);
  u.searchParams.set("page", String(page));
  u.searchParams.set("page_size", String(pageSize));
  const res = await fetch(u);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchBlogPost(slug) {
  const res = await fetch(`${API}/blog/${encodeURIComponent(slug)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

### TypeScript types

```typescript
export interface BlogListItem {
  slug: string;
  title: string;
  description: string;
  published_at: string | null;
  tags: string[];
  image_url: string | null;
  image_alt: string | null;
  og_image_url: string | null;
}

export interface BlogListResponse {
  items: BlogListItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface BlogDetailResponse {
  slug: string;
  title: string;
  description: string;
  body_md: string;
  published_at: string | null;
  updated_at: string | null;
  tags: string[];
  image_url: string | null;
  image_alt: string | null;
  og_image_url: string | null;
  canonical_url: string | null;
}
```

---

## Not for frontend

**`POST /internal/blog/generate-draft`** creates drafts via Gemini (server secret). It is documented in [frontend-api.md](frontend-api.md) for operators only — **do not call from the browser.**

---

## Source of truth in code

Response shapes match [`src/api/blog_routes.py`](../src/api/blog_routes.py).
