-- Run in Supabase → SQL Editor. Paste results if you still get PGRST205.

-- 1) Does the table exist in Postgres?
select schemaname, tablename
from pg_tables
where tablename in ('documents', 'leads', 'sessions')
order by schemaname, tablename;

-- 2) Row counts (should be >= 0 for documents after ingest)
select 'documents' as tbl, count(*) from public.documents
union all
select 'leads', count(*) from public.leads
union all
select 'sessions', count(*) from public.sessions;

-- 3) Nudge PostgREST to reload (safe to run anytime)
notify pgrst, 'reload schema';
