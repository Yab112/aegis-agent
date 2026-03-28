-- Aegis blog: posts stored in Supabase (Markdown body + frontmatter-style columns).
-- Run in: Supabase Dashboard → SQL Editor → New query → Run.
-- Safe on existing projects: uses IF NOT EXISTS / DROP POLICY IF EXISTS.

-- ─── 1. Table ────────────────────────────────────────────────────────────────
create table if not exists public.blog_posts (
  id              uuid primary key default gen_random_uuid(),

  -- URL slug (unique); e.g. "rag-chunking-strategies"
  slug            text not null,

  title           text not null,
  description     text not null,
  body_md         text not null default '',

  -- draft → review (optional) → published
  status          text not null default 'draft'
                    check (status in ('draft', 'review', 'published')),

  tags            text[] not null default '{}',

  -- Optional cover / OG (match your blog.md frontmatter)
  image_url       text,
  image_alt       text,
  og_image_url    text,
  canonical_url   text,

  -- For dedup / pipeline: stable key from your "trend → idea" step (normalized topic)
  topic_key       text,

  published_at    timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),

  constraint blog_posts_slug_unique unique (slug)
);

create index if not exists blog_posts_published_at_desc
  on public.blog_posts (published_at desc nulls last)
  where status = 'published';

create index if not exists blog_posts_status_created
  on public.blog_posts (status, created_at desc);

create index if not exists blog_posts_topic_key
  on public.blog_posts (topic_key)
  where topic_key is not null;

-- Keep updated_at fresh on row change
create or replace function public.set_blog_posts_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists blog_posts_set_updated_at on public.blog_posts;
create trigger blog_posts_set_updated_at
  before update on public.blog_posts
  for each row
  execute function public.set_blog_posts_updated_at();

-- ─── 2. RLS ───────────────────────────────────────────────────────────────────
alter table public.blog_posts enable row level security;

-- Anyone with anon key can read published posts only (for yabibal.site frontend).
drop policy if exists "Public read published blog posts" on public.blog_posts;
create policy "Public read published blog posts"
  on public.blog_posts
  for select
  to anon, authenticated
  using (status = 'published');

-- Backend / scripts use service_role (bypasses RLS by default in Supabase, but explicit is fine).
drop policy if exists "Service role full access blog_posts" on public.blog_posts;
create policy "Service role full access blog_posts"
  on public.blog_posts
  for all
  to service_role
  using (true)
  with check (true);

-- ─── 3. Grants ────────────────────────────────────────────────────────────────
grant select on public.blog_posts to anon, authenticated;
grant select, insert, update, delete on public.blog_posts to service_role;

notify pgrst, 'reload schema';
