"""
Apply scripts/supabase_init.sql to your Supabase Postgres (DDL is not available via REST).

Requires:
  - SUPABASE_URL=https://<ref>.supabase.co
  - SUPABASE_DB_PASSWORD=<Database password from Supabase → Settings → Database>

Usage:
  python scripts/apply_supabase_schema.py

Uses psql if on PATH; otherwise prints the psql command to run manually.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "")
    pw = os.environ.get("SUPABASE_DB_PASSWORD")
    if not pw:
        print(
            "Missing SUPABASE_DB_PASSWORD in .env — get it from "
            "Supabase Dashboard → Project Settings → Database → Database password.",
            file=sys.stderr,
        )
        return 1
    m = re.search(r"https://([^.]+)\.supabase\.co", url)
    if not m:
        print("SUPABASE_URL must look like https://<project-ref>.supabase.co", file=sys.stderr)
        return 1
    host = f"db.{m.group(1)}.supabase.co"
    sql_file = Path(__file__).resolve().parent / "supabase_init.sql"
    if not sql_file.is_file():
        print(f"Missing {sql_file}", file=sys.stderr)
        return 1

    psql = shutil.which("psql")
    if not psql and Path(r"C:\Program Files\PostgreSQL\17\bin\psql.exe").is_file():
        psql = str(Path(r"C:\Program Files\PostgreSQL\17\bin\psql.exe"))

    env = os.environ.copy()
    env["PGPASSWORD"] = pw
    env["PGSSLMODE"] = "require"

    cmd = [
        psql or "psql",
        "-h",
        host,
        "-p",
        "5432",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-f",
        str(sql_file),
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if not psql:
        print("psql not found. Run this in a terminal (same folder as .env):\n")
        print(f'  set PGPASSWORD=<your-db-password>')
        print(f'  set PGSSLMODE=require')
        print("  " + " ".join(cmd))
        print("\nOr paste scripts/supabase_init.sql into Supabase → SQL Editor → Run.")
        return 1

    print(f"Applying schema via psql to {host} ...", flush=True)
    r = subprocess.run(cmd, env=env, cwd=str(ROOT))
    if r.returncode == 0:
        print("OK — schema applied. Next: python scripts/ingest.py")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
