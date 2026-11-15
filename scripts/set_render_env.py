"""
Set environment variables on this service in Render, then redeploy.

Values are read from the local .env (git-ignored), so they never appear on the command line.
Prints names only.

    python3 scripts/set_render_env.py RESEND_API_KEY EMAIL_FROM
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request

API = "https://api.render.com/v1"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    names = sys.argv[1:]
    if not names:
        print(__doc__)
        return 1
    key_file = pathlib.Path.home() / ".config/render/api-key"
    key = os.environ.get("RENDER_API_KEY") or (key_file.read_text().strip() if key_file.exists() else "")
    if not key:
        print("No Render API key: set RENDER_API_KEY or create ~/.config/render/api-key")
        return 1
    local = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            local[k.strip()] = v.strip()
    missing = [n for n in names if not local.get(n)]
    if missing:
        print("Not in .env:", ", ".join(missing))
        return 1

    def call(method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            API + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json"},
        )
        raw = urllib.request.urlopen(req, timeout=30).read()
        return json.loads(raw) if raw else {}

    services = [s["service"] for s in call("GET", "/services?limit=100")]
    service = next((s for s in services if "aegis" in s["name"].lower()), None)
    if not service:
        print("No aegis service on this Render account")
        return 1
    for name in names:
        call("PUT", f"/services/{service['id']}/env-vars/{name}", {"value": local[name]})
        print(f"set {name} on {service['name']}")
    call("POST", f"/services/{service['id']}/deploys", {})
    print("redeploy started")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
