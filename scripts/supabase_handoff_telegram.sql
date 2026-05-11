-- Maps each Telegram "lead alert" message to a session + optional visitor email so
-- owner replies (reply-in-thread) can be emailed to the visitor. Run after supabase_init.sql.

create table if not exists public.handoff_telegram_alerts (
  id                    uuid primary key default gen_random_uuid(),
  created_at            timestamptz not null default now(),
  telegram_chat_id      text not null,
  telegram_message_id   bigint not null,
  session_id            text not null,
  visitor_email         text,
  user_query              text not null default '',
  intent                  text,
  constraint handoff_telegram_alerts_chat_msg_unique
    unique (telegram_chat_id, telegram_message_id)
);

create index if not exists handoff_telegram_alerts_session
  on public.handoff_telegram_alerts (session_id);

alter table public.handoff_telegram_alerts enable row level security;

drop policy if exists "Service role full access handoff_telegram_alerts"
  on public.handoff_telegram_alerts;
create policy "Service role full access handoff_telegram_alerts"
  on public.handoff_telegram_alerts
  for all
  to service_role
  using (true)
  with check (true);

grant select, insert, update, delete on public.handoff_telegram_alerts to service_role;

-- Existing projects that created this table before ``intent`` existed:
alter table public.handoff_telegram_alerts
  add column if not exists intent text;

notify pgrst, 'reload schema';
