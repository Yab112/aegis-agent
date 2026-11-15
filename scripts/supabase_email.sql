-- Owner email from the admin: reusable templates and a log of everything sent.
-- Run in Supabase → SQL Editor. Safe to re-run.

create table if not exists public.email_templates (
  id          uuid primary key default gen_random_uuid(),
  key         text not null unique,
  name        text not null,
  subject     text not null default '',
  body_md     text not null default '',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists public.sent_emails (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz not null default now(),
  to_email     text not null,
  subject      text not null,
  body_md      text not null,
  template_key text,
  provider     text not null default '',
  provider_id  text,
  status       text not null default 'sent' check (status in ('sent', 'failed')),
  error        text
);
create index if not exists sent_emails_created on public.sent_emails (created_at desc);
create index if not exists sent_emails_to on public.sent_emails (to_email, created_at desc);

do $$
declare t text;
begin
  foreach t in array array['email_templates', 'sent_emails'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists "Service role full access %s" on public.%I', t, t);
    execute format('create policy "Service role full access %s" on public.%I for all to service_role using (true) with check (true)', t, t);
    execute format('grant select, insert, update, delete on public.%I to service_role', t);
  end loop;
end $$;

notify pgrst, 'reload schema';
