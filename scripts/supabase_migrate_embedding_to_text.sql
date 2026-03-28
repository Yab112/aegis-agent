-- Run in SQL Editor if documents.embedding is still type vector(384) and REST returns PGRST205.
-- Converts stored vectors to text literals; updates match_documents to cast in SQL.

drop function if exists public.match_documents(vector, int, jsonb);

alter table public.documents
  alter column embedding type text using (embedding::text);

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

grant execute on function public.match_documents(vector, int, jsonb)
  to anon, authenticated, service_role;

notify pgrst, 'reload schema';
