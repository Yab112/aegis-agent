"""
Copy this service's environment from Render into a local .env (git-ignored, owner-only).

Reads your Render API key from ~/.config/render/api-key (or RENDER_API_KEY).
Prints variable NAMES only, never values.

    python3 scripts/pull_env_from_render.py            # service whose name contains "aegis"
    python3 scripts/pull_env_from_render.py my-service
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request

API = "https://api.render.com/v1"


def main() -> int:
    key_file = pathlib.Path.home() / ".config/render/api-key"
    key = os.environ.get("RENDER_API_KEY") or (key_file.read_text().strip() if key_file.exists() else "")
    if not key:
        print("No Render API key: set RENDER_API_KEY or create ~/.config/render/api-key")
        return 1
    wanted = (sys.argv[1] if len(sys.argv) > 1 else "aegis").lower()

    def get(path: str):
        req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=30))

    services = [s["service"] for s in get("/services?limit=100")]
    match = [s for s in services if wanted in s["name"].lower()]
    if not match:
        print(f"No service matching {wanted!r}. Services on this account: {', '.join(s['name'] for s in services)}")
        return 1
    service = match[0]

    rows, cursor = [], None
    while True:
        page = get(f"/services/{service['id']}/env-vars?limit=100" + (f"&cursor={cursor}" if cursor else ""))
        rows += page
        if len(page) < 100:
            break
        cursor = page[-1]["cursor"]
    values = {r["envVar"]["key"]: r["envVar"]["value"] for r in rows}

    env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
    lines = [f"{k}={v}" for k, v in sorted(values.items()) if "\n" not in v]
    env_path.write_text("\n".join(lines) + "\n")
    os.chmod(env_path, 0o600)

    print(f"Service: {service['name']}")
    print(f"Wrote {len(lines)} variables to {env_path}")
    print("Names:", ", ".join(sorted(values)))
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_DB_PASSWORD"):
        print(f"  {name}: {'present' if values.get(name) else 'MISSING'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
