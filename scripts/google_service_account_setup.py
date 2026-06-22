"""
Print service-account setup steps for durable Calendar auth (no refresh tokens).

1. Google Cloud Console → IAM & Admin → Service Accounts → Create
2. Keys → Add key → JSON → save as config/google_service_account.json (gitignored)
3. Enable Google Calendar API for the project
4. Share your calendar with the printed email (Make changes to events)
5. Set GOOGLE_SERVICE_ACCOUNT_JSON on Render (paste minified JSON one line)

Run from project root:
  python scripts/google_service_account_setup.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEY_CANDIDATES = (
    ROOT / "config" / "google_service_account.json",
    ROOT / "google_service_account.json",
)


def _is_service_account_key(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("type") == "service_account" and bool(data.get("client_email"))


def find_service_account_key() -> Path | None:
    for path in KEY_CANDIDATES:
        if path.is_file():
            return path
    config_dir = ROOT / "config"
    if config_dir.is_dir():
        for path in sorted(config_dir.glob("*.json")):
            if _is_service_account_key(path):
                return path
    return None


def main() -> None:
    key_path = find_service_account_key()
    if not key_path:
        print(
            "No service account key found.\n\n"
            "Create one in Google Cloud Console:\n"
            "  IAM & Admin → Service Accounts → Create service account\n"
            "  → Keys → Add key → JSON\n"
            f"  → Save as {KEY_CANDIDATES[0]}\n"
            "  → Or keep Google's download name under config/ (e.g. gmail-integration-….json)\n\n"
            "Then run this script again."
        )
        raise SystemExit(1)

    info = json.loads(key_path.read_text(encoding="utf-8"))
    email = info.get("client_email", "(missing client_email)")
    project = info.get("project_id", "?")

    print(f"Service account: {email}")
    print(f"Project:         {project}")
    print(f"Key file:        {key_path}\n")
    print("Next steps:\n")
    print("1. Google Calendar -> Settings -> your calendar -> Share with specific people")
    print(f"   Add: {email}")
    print("   Permission: Make changes to events\n")
    print("2. Local .env — either path or inline JSON:")
    print(f"   GOOGLE_SERVICE_ACCOUNT_JSON={key_path.as_posix()}")
    print("   (or paste minified JSON as one line)\n")
    print("3. Render -> Environment -> add GOOGLE_SERVICE_ACCOUNT_JSON")
    print("   (paste the entire JSON object as a single line)\n")
    print("4. Keep GOOGLE_CLIENT_* + GOOGLE_REFRESH_TOKEN for Gmail (Telegram replies).")
    print("   Publish OAuth consent screen to Production so refresh tokens last.\n")
    print("5. Redeploy. Calendar/Meet use the service account; Gmail still uses OAuth.")


if __name__ == "__main__":
    main()
