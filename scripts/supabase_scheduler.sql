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

notify pgrst, 'reload schema';
