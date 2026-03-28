-- Run in Supabase SQL Editor if you still see PGRST205 / "table not in schema cache"
-- after creating tables (PostgREST stale cache).
notify pgrst, 'reload schema';
