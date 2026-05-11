-- One-liner for projects that already ran supabase_handoff_telegram.sql without ``intent``.
alter table public.handoff_telegram_alerts
  add column if not exists intent text;

notify pgrst, 'reload schema';
