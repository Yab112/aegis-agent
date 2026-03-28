"""
Quick check that PostgREST sees your tables (same paths as ingest / app).

Usage:
  python scripts/verify_supabase.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client
from config.settings import get_settings

s = get_settings()
ref = s.supabase_url.replace("https://", "").split(".")[0]
print(f"SUPABASE_URL project ref: {ref}")

c = create_client(s.supabase_url, s.supabase_service_key)

for name in ("leads", "sessions", "documents"):
    print(f"Testing public.{name} ...", end=" ")
    try:
        r = c.table(name).select("id").limit(1).execute()
        print("OK", r.data)
    except Exception as e:
        print("FAILED", e)

# If leads/sessions OK but documents fails: old vector(384) column — run
# scripts/supabase_migrate_embedding_to_text.sql in SQL Editor.
print(
    """
If documents fails but leads/sessions work:
  1) Run scripts/supabase_migrate_embedding_to_text.sql (embedding column fix + reload)

If all three fail:
  Dashboard → Settings → API → enable Data API, add "public" to Exposed schemas, Save.
  Then run: notify pgrst, 'reload schema'; in SQL Editor.
"""
)
