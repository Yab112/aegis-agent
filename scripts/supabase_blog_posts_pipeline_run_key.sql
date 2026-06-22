-- Idempotent: CI dedup via BLOG_PIPELINE_RUN_KEY (blog-draft workflow).
-- Run once in Supabase → SQL Editor if blog draft fails with:
--   column blog_posts.pipeline_run_key does not exist

alter table public.blog_posts
  add column if not exists pipeline_run_key text;

create unique index if not exists blog_posts_pipeline_run_key_unique
  on public.blog_posts (pipeline_run_key)
  where pipeline_run_key is not null;

notify pgrst, 'reload schema';
