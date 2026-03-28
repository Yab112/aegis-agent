# Supabase setup guide

**Fast path:** open `scripts/supabase_init.sql` in this repo, copy the whole file, and run it in **Supabase → SQL Editor → New query**. That creates `documents`, `match_documents`, `leads`, `sessions`, RLS, and grants so PostgREST can call the RPC (fixes `PGRST202` / “function … not found in the schema cache”).

### If ingest still says `PGRST205` / table not in schema cache

1. **Expose `public` in the Data API**  
   In **Dashboard → Settings → API** (same project as your `SUPABASE_URL`):
   - Turn **Data API** **on** (do not disable it if you use `supabase-py`).
   - Under **Exposed schemas**, include **`public`**.  
     If you previously removed `public` while hardening the API, PostgREST will **not** expose `public.documents` and you will get PGRST205 until `public` is added back (then Save).

2. **Confirm the table exists in Postgres**  
   Run **`scripts/diagnose_supabase.sql`** in the SQL Editor. You should see `public.documents` in `pg_tables`.

3. **Reload PostgREST cache**  
   Run **`scripts/supabase_patch_postgrest.sql`** or at least `NOTIFY pgrst, 'reload schema';`, wait ~30s, then retry.

4. **Same project**  
   **Project URL** under Settings → API must match `SUPABASE_URL` in `.env`.

5. **`embedding` column type** — `documents.embedding` must be **`text`** (vector literal string), not **`vector(384)`**, or PostgREST may never expose `public.documents` (persistent PGRST205).  
   - New projects: use current `scripts/supabase_init.sql` (text column).  
   - Existing DB with `vector(384)`: run **`scripts/supabase_migrate_embedding_to_text.sql`**, then `python scripts/verify_supabase.py`.

Local check: `python scripts/verify_supabase.py`

If you prefer step-by-step, use the sections below (same content, split up).

Run these SQL statements in your Supabase project's SQL editor
(Dashboard → SQL Editor → New query).

---

## Step 1 — Enable pgvector extension

```sql
create extension if not exists vector;
```

---

## Step 2 — Create the documents table

Store embeddings as **text** (pgvector literal `"[...]"`) so the Data API can expose the table. Cast to `vector(384)` only inside `match_documents`.

```sql
create table documents (
  id          uuid primary key default gen_random_uuid(),
  content     text not null,
  embedding   text not null,
  metadata    jsonb default '{}',
  created_at  timestamptz default now()
);
```

---

## Step 3 — Create the IVFFlat index

Build this AFTER your first ingestion run (index requires data to train on).

```sql
create index on documents
using ivfflat ((embedding::vector(384)) vector_cosine_ops)
with (lists = 100);
```

For queries, set probes at runtime for accuracy vs speed tradeoff:
```sql
set ivfflat.probes = 10;
```

---

## Step 4 — Create the match_documents RPC function

```sql
create or replace function match_documents(
  query_embedding  vector(384),
  match_count      int default 5,
  filter           jsonb default '{}'
)
returns table (
  id          uuid,
  content     text,
  metadata    jsonb,
  similarity  float
)
language plpgsql
as $$
begin
  return query
  select
    documents.id,
    documents.content,
    documents.metadata,
    1 - ((documents.embedding::vector(384)) <=> query_embedding) as similarity
  from documents
  where documents.metadata @> filter
  order by (documents.embedding::vector(384)) <=> query_embedding
  limit match_count;
end;
$$;
```

---

## Step 5 — Create the leads table

```sql
create table leads (
  id           uuid primary key default gen_random_uuid(),
  session_id   uuid,
  email        text unique,          -- unique so repeat visits don't duplicate
  intent       text,
  query        text,
  captured_at  timestamptz default now()
);
```

---

## Step 6 — Create the sessions table

Use a JSON **array** of `{role, content}` objects (matches the Python client):

```sql
create table sessions (
  id          uuid primary key,
  messages    jsonb default '[]'::jsonb,
  user_email  text,
  updated_at  timestamptz default now()
);
```

After creating `match_documents`, grant execute so the API can call it:

```sql
grant execute on function public.match_documents(vector, int, jsonb)
  to anon, authenticated, service_role;
```

---

## Step 7 — Row Level Security (optional but recommended)

```sql
-- Only your service role key can read/write — public cannot
alter table documents  enable row level security;
alter table leads      enable row level security;
alter table sessions   enable row level security;

-- Allow service role full access (your backend uses this key)
create policy "Service role full access" on documents
  for all using (auth.role() = 'service_role');

create policy "Service role full access" on leads
  for all using (auth.role() = 'service_role');

create policy "Service role full access" on sessions
  for all using (auth.role() = 'service_role');
```

---

## Useful admin queries

```sql
-- Count chunks per project
select metadata->>'project_name' as project, count(*) as chunks
from documents
group by metadata->>'project_name'
order by chunks desc;

-- View all leads captured
select email, intent, query, captured_at
from leads
order by captured_at desc;

-- Check index health
select * from pg_indexes where tablename = 'documents';
```

---

## Keys to grab from Supabase dashboard

Go to: **Project Settings → API**

- `SUPABASE_URL` — the Project URL (e.g. `https://xyzxyz.supabase.co`)
- `SUPABASE_SERVICE_KEY` — the `service_role` secret key (NOT the anon key)

Never expose the service key in frontend code. It only lives in your `.env` and Koyeb environment variables.
