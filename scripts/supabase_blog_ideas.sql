-- Blog ideas queue (internal). Run in Supabase → SQL Editor after blog_posts exists.
-- Safe to re-run: IF NOT EXISTS / DROP IF EXISTS patterns.

-- ─── 1. scheduled_publish_at on blog_posts (optional go-live gate) ───────────
alter table public.blog_posts
  add column if not exists scheduled_publish_at timestamptz;

comment on column public.blog_posts.scheduled_publish_at is
  'If set, publish workflow only promotes review → published when now() >= this time; null means ASAP on next publish run.';

-- ─── 2. blog_ideas ────────────────────────────────────────────────────────────
create table if not exists public.blog_ideas (
  id                   uuid primary key default gen_random_uuid(),
  created_at           timestamptz not null default now(),

  source               text not null
    check (source in (
      'stackoverflow', 'github', 'hackernews', 'reddit', 'devto', 'rss', 'manual'
    )),
  source_url           text,
  source_id            text,

  title                text not null,
  raw_excerpt          text not null default '',
  reference_jsonb      jsonb not null default '{}',

  normalized_tags      text[] not null default '{}',
  topic_key            text not null,
  angle                text not null default '',

  skill_fit            text not null default 'medium'
    check (skill_fit in ('high', 'medium', 'low')),
  status               text not null default 'pending'
    check (status in ('pending', 'consumed', 'skipped', 'failed')),
  skip_reason          text,

  consumed_by_post_id  uuid references public.blog_posts (id) on delete set null
);

create index if not exists blog_ideas_status_created
  on public.blog_ideas (status, created_at desc);

create index if not exists blog_ideas_topic_key
  on public.blog_ideas (topic_key);

create unique index if not exists blog_ideas_source_source_id_unique
  on public.blog_ideas (source, source_id)
  where source_id is not null;

create unique index if not exists blog_ideas_topic_key_pending_unique
  on public.blog_ideas (topic_key)
  where status = 'pending';

-- ─── 3. RLS (internal only; service_role bypasses) ────────────────────────────
alter table public.blog_ideas enable row level security;

drop policy if exists "Service role full access blog_ideas" on public.blog_ideas;
create policy "Service role full access blog_ideas"
  on public.blog_ideas
  for all
  to service_role
  using (true)
  with check (true);

grant select, insert, update, delete on public.blog_ideas to service_role;

notify pgrst, 'reload schema';
