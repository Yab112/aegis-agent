-- Admin console: inbox status + reply history, and portfolio projects.
-- Run in Supabase → SQL Editor after supabase_init.sql, supabase_handoff_telegram.sql
-- and supabase_blog_posts.sql. Safe to re-run.

-- ─── 1. Inbox: every handoff alert is a conversation the owner can answer ────
alter table public.handoff_telegram_alerts
  add column if not exists status text not null default 'open';

alter table public.handoff_telegram_alerts
  add column if not exists handled_at timestamptz;

alter table public.handoff_telegram_alerts
  drop constraint if exists handoff_telegram_alerts_status_check;
alter table public.handoff_telegram_alerts
  add constraint handoff_telegram_alerts_status_check
  check (status in ('open', 'replied', 'handled', 'spam'));

create index if not exists handoff_telegram_alerts_status_created
  on public.handoff_telegram_alerts (status, created_at desc);

-- Every message the owner sent to a visitor, from the admin page or from Telegram.
create table if not exists public.inbox_replies (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  alert_id    uuid references public.handoff_telegram_alerts (id) on delete cascade,
  session_id  text not null default '',
  channel     text not null default 'admin' check (channel in ('admin', 'telegram')),
  to_email    text not null,
  body        text not null,          -- what the owner typed
  sent_body   text not null default '', -- what was emailed (after polish)
  delivered   boolean not null default false
);

create index if not exists inbox_replies_alert_created
  on public.inbox_replies (alert_id, created_at);

alter table public.inbox_replies enable row level security;
drop policy if exists "Service role full access inbox_replies" on public.inbox_replies;
create policy "Service role full access inbox_replies"
  on public.inbox_replies for all to service_role using (true) with check (true);
grant select, insert, update, delete on public.inbox_replies to service_role;

-- ─── 2. Projects: edit, hide, feature and reorder the portfolio from the admin ─
-- A row whose slug matches a project built into the site overrides it;
-- any other row is shown as a new project.
create table if not exists public.projects (
  id            uuid primary key default gen_random_uuid(),
  slug          text not null unique,
  title         text not null,
  description   text not null default '',
  dates         text not null default '',
  href          text,
  image         text,
  technologies  text[] not null default '{}',
  links         jsonb not null default '[]'::jsonb,  -- [{ "type": "Website", "href": "https://…" }]
  visible       boolean not null default true,
  featured      boolean not null default false,
  active        boolean not null default true,
  client        boolean not null default false,
  sort_order    integer not null default 100,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create or replace function public.set_projects_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists projects_set_updated_at on public.projects;
create trigger projects_set_updated_at
  before update on public.projects
  for each row execute function public.set_projects_updated_at();

alter table public.projects enable row level security;
drop policy if exists "Service role full access projects" on public.projects;
create policy "Service role full access projects"
  on public.projects for all to service_role using (true) with check (true);
grant select, insert, update, delete on public.projects to service_role;

notify pgrst, 'reload schema';
