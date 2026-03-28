-- Aegis-Agent: full Supabase schema for RAG + leads + sessions.
-- Run in: Supabase Dashboard → SQL Editor → New query → Run (entire file).
-- Requires: empty project or drop existing objects first if you need a clean slate.

-- ─── 1. pgvector ─────────────────────────────────────────────────────────────
create extension if not exists vector;

-- ─── 2. documents (vector store) ─────────────────────────────────────────────
-- embedding is TEXT (pgvector literal "[f1,f2,...]") so PostgREST exposes the table.
-- vector(384) columns are often invisible to the REST API (persistent PGRST205).
create table if not exists public.documents (
  id          uuid primary key default gen_random_uuid(),
  content     text not null,
  embedding   text not null,
  metadata    jsonb default '{}',
  created_at  timestamptz default now()
);

-- ─── 3. match_documents RPC (used by src/rag/retriever.py) ───────────────────
create or replace function public.match_documents(
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

-- PostgREST must be able to execute this RPC (fixes PGRST202 when missing grants)
grant execute on function public.match_documents(vector, int, jsonb)
  to anon, authenticated, service_role;

-- ─── 4. leads ────────────────────────────────────────────────────────────────
create table if not exists public.leads (
  id           uuid primary key default gen_random_uuid(),
  session_id   text,
  email        text unique,
  intent       text,
  query        text,
  captured_at  timestamptz default now()
);

-- ─── 5. sessions (chat memory) ─────────────────────────────────────────────────
-- jsonb column (not jsonb[]): Python client sends a JSON array of {role, content}
create table if not exists public.sessions (
  id          uuid primary key,
  messages    jsonb default '[]'::jsonb,
  user_email  text,
  updated_at  timestamptz default now()
);

-- ─── 6. IVFFlat index (run AFTER first ingestion when you have rows) ─────────
-- create index if not exists documents_embedding_ivfflat
--   on public.documents
--   using ivfflat ((embedding::vector(384)) vector_cosine_ops)
--   with (lists = 100);

-- ─── 7. RLS (optional; service_role bypasses RLS, but good for defense in depth)
alter table public.documents enable row level security;
alter table public.leads enable row level security;
alter table public.sessions enable row level security;

drop policy if exists "Service role full access" on public.documents;
create policy "Service role full access" on public.documents
  for all using (auth.role() = 'service_role');

drop policy if exists "Service role full access" on public.leads;
create policy "Service role full access" on public.leads
  for all using (auth.role() = 'service_role');

drop policy if exists "Service role full access" on public.sessions;
create policy "Service role full access" on public.sessions
  for all using (auth.role() = 'service_role');

-- ─── 8. PostgREST schema cache (fixes PGRST205 right after DDL) ───────────────
notify pgrst, 'reload schema';

grant usage on schema public to anon, authenticated, service_role;
grant select, insert, update, delete on public.documents to service_role;
grant select, insert, update, delete on public.leads to service_role;
grant select, insert, update, delete on public.sessions to service_role;
