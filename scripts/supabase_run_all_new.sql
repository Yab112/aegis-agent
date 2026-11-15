-- yabibal.site: everything new to run in Supabase, in order. Safe to re-run.
-- 1) scheduler state  2) admin console  3) AI news desk

-- Backend scheduler state (replaces the GitHub Actions cron workflows).
-- Run once in Supabase → SQL Editor. Idempotent.
--
--   scheduler_jobs      one row per job: last handled slot, retry state, run lock
--   scheduler_job_runs  append-only history of every run (scheduled or manual)

create table if not exists public.scheduler_jobs (
  job_name          text primary key,
  cron              text not null,
  enabled           boolean not null default true,
  last_slot         timestamptz,
  slot_attempts     integer not null default 0,
  next_attempt_at   timestamptz,
  locked_until      timestamptz,
  lock_owner        text,
  last_started_at   timestamptz,
  last_finished_at  timestamptz,
  last_status       text,
  last_error        text,
  last_result       jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create table if not exists public.scheduler_job_runs (
  id           uuid primary key default gen_random_uuid(),
  job_name     text not null,
  trigger      text not null default 'schedule',
  slot         timestamptz,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  duration_ms  integer,
  status       text not null,
  result       jsonb,
  error        text
);

create index if not exists scheduler_job_runs_job_started_idx
  on public.scheduler_job_runs (job_name, started_at desc);

-- Service-role only: no anon/authenticated policies.
alter table public.scheduler_jobs enable row level security;
alter table public.scheduler_job_runs enable row level security;

-- Atomic run lock: only one instance can hold a job at a time. An expired
-- lease (process died or the host slept mid-run) is reclaimable.
create or replace function public.scheduler_claim_job(
  p_job text,
  p_owner text,
  p_lease_seconds integer
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed integer;
begin
  update public.scheduler_jobs
     set locked_until    = now() + make_interval(secs => p_lease_seconds),
         lock_owner      = p_owner,
         last_started_at = now(),
         last_status     = 'running',
         updated_at      = now()
   where job_name = p_job
     and (locked_until is null or locked_until < now());
  get diagnostics claimed = row_count;
  return claimed > 0;
end;
$$;

revoke all on function public.scheduler_claim_job(text, text, integer) from public, anon, authenticated;
grant execute on function public.scheduler_claim_job(text, text, integer) to service_role;


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


-- AI news desk: trusted sources, collected items, scored keywords, and post categories.
-- Run in Supabase → SQL Editor after supabase_blog_posts.sql. Safe to re-run.

-- ─── 1. Sources (seeded from src/newsroom/registry.py on the first collect) ──
create table if not exists public.news_sources (
  key             text primary key,
  name            text not null,
  url             text not null,
  feed_url        text,
  kind            text not null default 'feed' check (kind in ('feed', 'page', 'json', 'reference')),
  link_pattern    text,
  tier            integer not null default 2 check (tier between 1 and 4),
  grp             text not null default '',
  enabled         boolean not null default true,
  last_checked_at timestamptz,
  last_status     text,
  last_error      text,
  items_total     integer not null default 0,
  created_at      timestamptz not null default now()
);

-- ─── 2. Items: one row per article, paper or repository a source published ───
create table if not exists public.news_items (
  id            uuid primary key default gen_random_uuid(),
  source_key    text not null references public.news_sources (key) on delete cascade,
  tier          integer not null default 2,
  url           text not null unique,
  title         text not null,
  summary       text not null default '',
  published_at  timestamptz not null default now(),
  first_seen_at timestamptz not null default now()
);
create index if not exists news_items_published on public.news_items (published_at desc);
create index if not exists news_items_source_published on public.news_items (source_key, published_at desc);

-- ─── 3. Keywords: what the desk could write about, with every signal kept ────
create table if not exists public.keywords (
  id               uuid primary key default gen_random_uuid(),
  phrase           text not null unique,        -- lower-case key
  display          text not null default '',    -- as written in headlines
  category         text not null default 'ai-news',
  suggested_format text not null default 'analysis' check (suggested_format in ('news', 'analysis', 'explainer', 'course')),
  score            numeric not null default 0,
  signals          jsonb not null default '{}'::jsonb,
  reason           text not null default '',
  status           text not null default 'candidate' check (status in ('candidate', 'writing', 'written', 'dismissed')),
  item_count       integer not null default 0,
  source_count     integer not null default 0,
  sources          jsonb not null default '[]'::jsonb,  -- pages the writer reads, lab sources first
  post_id          uuid references public.blog_posts (id) on delete set null,
  first_seen_at    timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists keywords_status_score on public.keywords (status, score desc);

-- ─── 4. Posts carry their category, target keyword and format ────────────────
alter table public.blog_posts add column if not exists category text;
alter table public.blog_posts add column if not exists keyword text;
alter table public.blog_posts add column if not exists format text;
create index if not exists blog_posts_category on public.blog_posts (category) where status = 'published';

-- ─── 5. Access: backend only ─────────────────────────────────────────────────
do $$
declare t text;
begin
  foreach t in array array['news_sources', 'news_items', 'keywords'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists "Service role full access %s" on public.%I', t, t);
    execute format('create policy "Service role full access %s" on public.%I for all to service_role using (true) with check (true)', t, t);
    execute format('grant select, insert, update, delete on public.%I to service_role', t);
  end loop;
end $$;

notify pgrst, 'reload schema';
