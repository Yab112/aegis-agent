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
