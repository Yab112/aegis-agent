-- Run this if `documents` exists in Table Editor but ingest still fails with PGRST205.

notify pgrst, 'reload schema';

grant usage on schema public to anon, authenticated, service_role;
grant select, insert, update, delete on public.documents to service_role;
grant select, insert, update, delete on public.leads to service_role;
grant select, insert, update, delete on public.sessions to service_role;
