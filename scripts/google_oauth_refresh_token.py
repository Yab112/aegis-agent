"""
Obtain a new GOOGLE_REFRESH_TOKEN after invalid_grant or OAuth client changes.

Recommended (avoids redirect_uri_mismatch):
  1. Google Cloud Console → Credentials → Create OAuth client → **Desktop app**
  2. Download JSON → save under config/ (e.g. config/client_secret_….json) or scripts/google_oauth_client.json
  3. python scripts/google_oauth_refresh_token.py

Fallback: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env (must match Console;
Web clients need http://127.0.0.1:8080/ under Authorized redirect URIs).

Then paste printed values into .env (local + production) and restart the API.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from google_auth_oauthlib.flow import InstalledAppFlow

from config.settings import get_settings
from src.tools.calendar_tool import SCOPES

# Used only for .env-based Web clients (Desktop JSON ignores this).
OAUTH_LOOPBACK_HOST = "127.0.0.1"
OAUTH_LOOPBACK_PORT = 8080
REDIRECT_URI = f"http://{OAUTH_LOOPBACK_HOST}:{OAUTH_LOOPBACK_PORT}/"

CLIENT_JSON_CANDIDATES = (
    ROOT / "scripts" / "google_oauth_client.json",
    ROOT / "client_secret.json",
    ROOT / "scripts" / "client_secret.json",
)


def _find_client_json() -> Path | None:
    for path in CLIENT_JSON_CANDIDATES:
        if path.is_file():
            return path
    config_dir = ROOT / "config"
    if config_dir.is_dir():
        matches = sorted(config_dir.glob("client_secret*.json"))
        if matches:
            return matches[0]
    return None


def _client_type_hint(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    if "installed" in data:
        return "Desktop (installed)"
    if "web" in data:
        return "Web"
    return "unknown"


def _flow_from_env() -> InstalledAppFlow:
    s = get_settings()
    client_id = (s.google_client_id or "").strip()
    if not client_id or not (s.google_client_secret or "").strip():
        print(
            "Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env.\n"
            "Easiest fix: download a **Desktop app** OAuth JSON from Google Cloud Console\n"
            "and save it as scripts/google_oauth_client.json, then run this script again."
        )
        raise SystemExit(1)
    print(
        f"Using .env OAuth client (id ends with …{client_id[-20:]}).\n"
        f"If you see redirect_uri_mismatch, this client is almost certainly **Web application**.\n"
        "Do NOT keep retrying — fix Console first (see below).\n"
    )
    return InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": s.google_client_secret.strip(),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=SCOPES,
    )


def _run_flow(flow: InstalledAppFlow, *, from_json: bool) -> None:
    print(
        "Browser will open. Sign in with the Google account that owns the calendar "
        "and approve Calendar + Gmail.\n"
    )
    if from_json:
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
        )
    else:
        print(
            f"Redirect URI sent to Google: {REDIRECT_URI}\n"
            "For Web clients, add that EXACT URI in Console → Credentials → your OAuth client\n"
            "→ Authorized redirect URIs → Save → wait ~1 minute → run this script again.\n"
            "Recommended instead: create a **Desktop app** client, save its JSON as\n"
            "scripts/google_oauth_client.json — no redirect URI setup needed.\n"
        )
        creds = flow.run_local_server(
            host=OAUTH_LOOPBACK_HOST,
            port=OAUTH_LOOPBACK_PORT,
            prompt="consent",
            access_type="offline",
        )

    if not creds.refresh_token:
        print(
            "\nNo refresh token returned. Revoke this app at "
            "https://myaccount.google.com/permissions then run this script again."
        )
        raise SystemExit(1)

    print("\n--- Add or replace in .env (and production env) ---\n")
    if creds.client_id:
        print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    if creds.client_secret:
        print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}\n")


def main() -> None:
    client_json = _find_client_json()
    if client_json:
        kind = _client_type_hint(client_json)
        print(
            f"Using OAuth client file: {client_json.name} ({kind})\n"
            "If this file is from a **Desktop app** download, redirect_uri_mismatch should not occur.\n"
        )
        if kind == "Web":
            print(
                "WARNING: JSON is a **Web** client. Either switch to a Desktop download,\n"
                f"or add {REDIRECT_URI} to Authorized redirect URIs in Console.\n"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_json), SCOPES)
        _run_flow(flow, from_json=kind == "Desktop (installed)")
        return

    flow = _flow_from_env()
    _run_flow(flow, from_json=False)


if __name__ == "__main__":
    main()
