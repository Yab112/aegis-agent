"""
Obtain a new GOOGLE_REFRESH_TOKEN after invalid_grant or OAuth client changes.

Requires: Desktop OAuth 2.0 client in Google Cloud (same id/secret as in .env).
Run from project root with venv active:

  python scripts/google_oauth_refresh_token.py

Then paste the printed GOOGLE_REFRESH_TOKEN into .env and restart the API.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from google_auth_oauthlib.flow import InstalledAppFlow

from config.settings import get_settings
from src.tools.calendar_tool import SCOPES


def main() -> None:
    s = get_settings()
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )
    print(
        "Browser will open. Sign in with the Google account that owns the calendar, "
        "and approve Calendar + Gmail. Use an OAuth client type **Desktop** in Cloud Console.\n"
    )
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        access_type="offline",
    )
    if not creds.refresh_token:
        print(
            "\nNo refresh token returned. Revoke this app at "
            "https://myaccount.google.com/permissions then run this script again."
        )
        raise SystemExit(1)
    print("\n--- Add or replace in .env ---\n")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}\n")


if __name__ == "__main__":
    main()
