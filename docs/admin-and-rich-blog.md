# Admin console, richer blog posts and Telegram control

## 1. One-time setup

1. Supabase → SQL Editor: run `scripts/supabase_admin.sql` (inbox status, reply history, projects).
2. Portfolio host (Vercel) environment:
   - `ADMIN_PASSWORD` — what you type at `/admin/login`
   - `ADMIN_SESSION_SECRET` — any long random string
   - `AEGIS_ADMIN_API_KEY` — the same value as this API's `API_SECRET_KEY`
3. Deploy both repos. The Telegram webhook URL does not change.

## 2. Blog post format

The body is Markdown plus blocks the site renders as components
(`src/blog/rich_content.py` validates them before a post is stored):

| Block | How it is written | What the pipeline checks |
|---|---|---|
| Chart | `chart` fence + JSON (`type`, `title`, `x`, `series`, `unit`, `caption`, `basis`, `source`) | shape and numbers; `basis: sourced` only if the source URL was retrieved by research, otherwise relabelled illustrative |
| Figure | `figure` fence + JSON (`query`, `caption`, `alt`) | resolved to a Wikimedia Commons image with author, licence and file page; dropped if none fits |
| Diagram | `mermaid` fence | passed through |
| Table | GFM table | counted |
| Footnote | `[^1]` + `[^1]: text [title](url)` | references without a definition removed |
| Link | any Markdown link | kept if it is in the research pack or answers over HTTP, otherwise unlinked |

Writer stages: idea → research (`src/blog/research.py`: source page + Gemini with Google Search
grounding) → meta → body → validation → cover → insert. The research pages are saved as the
post's `resource_links` and shown as "Sources and further reading".

## 3. Admin API (`/admin/*`, secret required)

`GET /admin/overview` · `GET /admin/inbox` · `GET /admin/inbox/{id}` · `POST /admin/inbox/{id}/reply`
· `POST /admin/inbox/{id}/status` · `GET|POST /admin/posts` · `GET|PATCH|DELETE /admin/posts/{id}`
· `POST /admin/posts/generate` · `GET /admin/generation` · `GET|POST /admin/ideas`
· `POST /admin/ideas/{id}/skip` · `GET|POST /admin/projects` · `PATCH|DELETE /admin/projects/{id}`

Public: `GET /projects` (admin-managed project overrides for the portfolio).

## 4. Telegram commands (owner chat only)

`/inbox`, `/reply <id> <text>`, `/posts`, `/write <topic>`, `/write`, `/idea <title>`,
`/publish <slug>`, `/unpublish <slug>`, `/stats`, `/jobs`, `/run <job>`, `/admin`, `/help`.
When a post is written the bot sends its numbers (words, sources, charts, what the checks
removed) with Publish / Discard / Edit-in-admin buttons.

## 5. People, Email and Data (admin)

- **People** (`GET /admin/people`, `/admin/people/{email}`): one row per email address, merged from leads,
  handoffs, chat sessions and the mail you sent.
- **Email** (`/admin/email/*`): `templates` (CRUD), `preview`, `send`, `sent`, `status`.
  The body uses a small Markdown subset plus `[[Button|url]]` and `{{first_name}}`, `{{owner_name}}`,
  `{{site}}`, `{{email}}`; `src/mailer/layout.py` wraps it in the fixed branded layout.
  Sending uses Resend when `RESEND_API_KEY` and `EMAIL_FROM` are set on the API host, else Gmail.
  Inbox and Telegram replies use the same layout and provider.
- **Data** (`GET /admin/data`, `/admin/data/{table}`): read-only rows of an allow-listed set of tables.
- SQL: `scripts/supabase_email.sql`.
