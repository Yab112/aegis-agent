"""
Apply a single SQL file to Supabase Postgres (DDL not available via REST).

Requires in .env:
  SUPABASE_URL=https://<ref>.supabase.co
  SUPABASE_DB_PASSWORD=<Database password>

Usage:
  python scripts/apply_supabase_sql.py scripts/supabase_blog_posts_pipeline_run_key.sql
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _host_from_url(url: str) -> str | None:
    m = re.search(r"https://([^.]+)\.supabase\.co", url)
    return f"db.{m.group(1)}.supabase.co" if m else None


def _apply_psycopg2(host: str, password: str, sql_file: Path) -> int:
    try:
        import psycopg2
    except ImportError:
        return -1
    sql = sql_file.read_text(encoding="utf-8")
    print(f"Applying {sql_file.name} via psycopg2 to {host} ...", flush=True)
    conn = psycopg2.connect(
        host=host,
        port=5432,
        user="postgres",
        password=password,
        dbname="postgres",
        sslmode="require",
        connect_timeout=30,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        print("OK — SQL applied.", flush=True)
        return 0
    finally:
        conn.close()


def _apply_psql(host: str, password: str, sql_file: Path) -> int:
    psql = shutil.which("psql")
    if not psql and Path(r"C:\Program Files\PostgreSQL\17\bin\psql.exe").is_file():
        psql = str(Path(r"C:\Program Files\PostgreSQL\17\bin\psql.exe"))
    if not psql:
        return -1
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    env["PGSSLMODE"] = "require"
    cmd = [
        psql,
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
    print(f"Applying {sql_file.name} via psql to {host} ...", flush=True)
    return subprocess.run(cmd, env=env, cwd=str(ROOT)).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a SQL file to Supabase Postgres")
    parser.add_argument(
        "sql_file",
        type=Path,
        help="Path to .sql file (e.g. scripts/supabase_blog_posts_pipeline_run_key.sql)",
    )
    args = parser.parse_args()
    sql_file = args.sql_file if args.sql_file.is_absolute() else ROOT / args.sql_file
    if not sql_file.is_file():
        print(f"Missing file: {sql_file}", file=sys.stderr)
        return 1

    url = os.environ.get("SUPABASE_URL", "")
    pw = os.environ.get("SUPABASE_DB_PASSWORD")
    if not pw:
        print(
            "Missing SUPABASE_DB_PASSWORD in .env — Supabase Dashboard → Settings → Database.",
            file=sys.stderr,
        )
        return 1
    host = _host_from_url(url)
    if not host:
        print("SUPABASE_URL must look like https://<ref>.supabase.co", file=sys.stderr)
        return 1

    rc = _apply_psycopg2(host, pw, sql_file)
    if rc == 0:
        return 0
    if rc > 0:
        return rc

    rc = _apply_psql(host, pw, sql_file)
    if rc == 0:
        return 0
    if rc > 0:
        return rc

    print(
        "Could not connect (install psycopg2-binary or psql).\n"
        "Or paste the SQL into Supabase Dashboard → SQL Editor → Run:\n"
        f"  {sql_file}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
