# Blog API - frontend reference

Read-only HTTP API for published blog posts stored in Supabase. Drafts and review rows are never returned.

Base URL: same as the main Aegis API (no path prefix on the host).

- Production: https://aegis-agent-5omj.onrender.com
- Local: http://127.0.0.1:8000

OpenAPI: GET /docs and GET /openapi.json on that host include these routes under the blog tag.

Versioning: every route below is also available under `/v1` for forward-compatible clients (example: `/v1/blog/search`).

Pipeline (ideas, drafts, publish cron): [blog-pipeline.md](blog-pipeline.md)

CORS: Same as the rest of the API. Your frontend origin (for example https://yabibal.site) must appear in ALLOWED_ORIGINS.

Auth: None. These routes are public.

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | /blog | Paginated list of published posts |
| GET | /blog/topics | Topic/tag list with counts for filter UI |
| GET | /blog/search | Search published posts by text query |
| GET | /blog/{slug} | Single post by URL slug (full Markdown) |

---

## GET /blog

Returns posts with status = published, newest first by published_at.

### Query parameters

| Name | Type | Default | Constraints |
|------|------|---------|-------------|
| page | integer | 1 | >= 1 |
| page_size | integer | 10 | 1-50 |
| topic | string or null | null | Optional. Matches topic_key or tag (case-insensitive). |

Examples:

- GET /blog?page=1&page_size=10
- GET /blog?page=1&page_size=12&topic=fastapi

### Response 200 OK

Content-Type: application/json

| Field | Type | Description |
|-------|------|-------------|
| items | array | List of summary objects (no body_md) |
| page | number | Echo of requested page |
| page_size | number | Echo of requested page size |
| total | number | Count of matching published posts |

Each item in items:

| Field | Type | Description |
|-------|------|-------------|
| slug | string | URL segment for the detail page |
| title | string | Headline |
| description | string | Short line for cards and SEO |
| published_at | string or null | ISO 8601 timestamp |
| tags | string[] | Lowercase tags |
| topic_key | string or null | Stable topic id for grouping/filtering |
| image_url | string or null | Optional cover URL |
| image_alt | string or null | Cover alt text |
| og_image_url | string or null | Optional Open Graph image |

### Example response

```json
{
  "items": [
    {
      "slug": "pivoting-to-python-ai-security-and-orchestration",
      "title": "Pivoting fully into Python and AI",
      "description": "Why I am doubling down on Python and AI...",
      "published_at": "2026-03-25T12:00:00+00:00",
      "tags": ["career", "python", "ai"],
      "topic_key": "python_ai_career_strategy",
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

- Total pages: Math.ceil(total / page_size) (guard total === 0).
- Next page: increment page until items.length === 0 or (page - 1) * page_size + items.length >= total.

---

## GET /blog/topics

Returns normalized topics/tags for filter chips.

### Response 200 OK

```json
{
  "items": [
    { "topic": "ai", "count": 9 },
    { "topic": "fastapi", "count": 5 },
    { "topic": "security", "count": 3 }
  ]
}
```

Sort order: count desc, then topic asc.

---

## GET /blog/search

Searches published posts by query text across title, description, body, tags, and topic key.

### Query parameters

| Name | Type | Default | Constraints |
|------|------|---------|-------------|
| q | string | none | required, min length 2 |
| page | integer | 1 | >= 1 |
| page_size | integer | 10 | 1-50 |
| topic | string or null | null | optional topic/tag filter |

Example: GET /blog/search?q=fastapi&page=1&page_size=10

### Response 200 OK

```json
{
  "items": [
    {
      "slug": "fastapi-routing-patterns",
      "title": "FastAPI routing patterns",
      "description": "Routing and validation tips",
      "published_at": "2026-04-20T09:00:00+00:00",
      "tags": ["fastapi", "python"],
      "topic_key": "fastapi_routing",
      "image_url": null,
      "image_alt": null,
      "og_image_url": null
    }
  ],
  "page": 1,
  "page_size": 10,
  "total": 2,
  "query": "fastapi"
}
```

---

## GET /blog/{slug}

Returns one published post. slug is the same string as item.slug from the list.

Example: GET /blog/pivoting-to-python-ai-security-and-orchestration

### Response 200 OK

| Field | Type | Description |
|-------|------|-------------|
| slug | string | Post slug |
| title | string | Headline |
| description | string | Subtitle or meta description |
| body_md | string | Full article Markdown |
| published_at | string or null | ISO 8601 |
| updated_at | string or null | ISO 8601 |
| tags | string[] | Tags |
| topic_key | string or null | Stable topic id |
| image_url | string or null | Cover |
| image_alt | string or null | Cover alt |
| og_image_url | string or null | Social preview image |
| canonical_url | string or null | Optional canonical URL |
| resource_links | array | Curated links readers can learn from |
| related_posts | array | Up to 6 related published posts |

### Example response

```json
{
  "slug": "human-feedback-rl-and-turing",
  "title": "Human-in-the-loop and RLHF",
  "description": "Notes on RLHF-style loops...",
  "body_md": "## Humans in the loop\n\n...",
  "published_at": "2026-03-27T12:00:00+00:00",
  "updated_at": "2026-03-27T12:00:00+00:00",
  "tags": ["rlhf", "ai"],
  "topic_key": "rlhf_human_feedback_patterns",
  "image_url": null,
  "image_alt": null,
  "og_image_url": null,
  "canonical_url": null,
  "resource_links": [
    {
      "title": "Google AI Studio Docs",
      "url": "https://ai.google.dev/gemini-api/docs",
      "description": "Official docs related to ai."
    }
  ],
  "related_posts": [
    {
      "slug": "how-i-design-evals-for-agentic-rag",
      "title": "How I design evals for agentic RAG",
      "description": "A practical eval loop for retrieval quality.",
      "published_at": "2026-04-15T10:00:00+00:00",
      "tags": ["ai", "rag"],
      "topic_key": "agentic_rag_evals",
      "image_url": null,
      "image_alt": null,
      "og_image_url": null
    }
  ]
}
```

### Errors

404 Not Found - no published post with that slug:

```json
{ "detail": "Post not found" }
```

---

## Frontend checklist

1. Base URL: use your deployed API origin, no trailing slash.
2. List page: GET /blog?page=1&page_size=12 and link each card with slug.
3. Filter chips: load GET /blog/topics; on click, call GET /blog with topic query param.
4. Search: call GET /blog/search?q=<query> for instant post discovery.
5. Detail page: GET /blog/{slug} and render body_md.
6. Related and resources: use related_posts for recommendations and resource_links for Learn more links.
7. SEO: set title, description, OG image, and canonical_url when present.
8. Drafts: unpublished posts are not visible here.

---

## Copy-paste examples

### List + detail (fetch)

```javascript
const API = "https://aegis-agent-5omj.onrender.com";

export async function fetchBlogList(page = 1, pageSize = 10, topic = null) {
  const u = new URL(`${API}/blog`);
  u.searchParams.set("page", String(page));
  u.searchParams.set("page_size", String(pageSize));
  if (topic) u.searchParams.set("topic", topic);
  const res = await fetch(u);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchBlogTopics() {
  const res = await fetch(`${API}/blog/topics`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function searchBlogPosts(query, page = 1, pageSize = 10, topic = null) {
  const u = new URL(`${API}/blog/search`);
  u.searchParams.set("q", query);
  u.searchParams.set("page", String(page));
  u.searchParams.set("page_size", String(pageSize));
  if (topic) u.searchParams.set("topic", topic);
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
  topic_key: string | null;
  image_url: string | null;
  image_alt: string | null;
  og_image_url: string | null;
}

export interface BlogTopicItem {
  topic: string;
  count: number;
}

export interface BlogListResponse {
  items: BlogListItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface BlogTopicsResponse {
  items: BlogTopicItem[];
}

export interface BlogSearchResponse {
  items: BlogListItem[];
  page: number;
  page_size: number;
  total: number;
  query: string;
}

export interface BlogResourceLink {
  title: string;
  url: string;
  description: string;
}

export interface BlogDetailResponse {
  slug: string;
  title: string;
  description: string;
  body_md: string;
  published_at: string | null;
  updated_at: string | null;
  tags: string[];
  topic_key: string | null;
  image_url: string | null;
  image_alt: string | null;
  og_image_url: string | null;
  canonical_url: string | null;
  resource_links: BlogResourceLink[];
  related_posts: BlogListItem[];
}
```

---

Not for frontend: POST /internal/blog/generate-draft is server-only and should not be called from the browser.

---

## Source of truth in code

Response shapes match [src/api/blog_routes.py](../src/api/blog_routes.py).
