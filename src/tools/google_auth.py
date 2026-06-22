"""
Google API credentials for Calendar and Gmail.

Calendar (recommended for production):
  Set GOOGLE_SERVICE_ACCOUNT_JSON — a service-account key does not use refresh tokens
  and keeps working until you revoke the key. Share your calendar with the service
  account email (Make changes to events).

Gmail (Telegram → visitor email):
  OAuth refresh token via GOOGLE_CLIENT_* + GOOGLE_REFRESH_TOKEN. Publish the OAuth
  consent screen to **Production** (not Testing) so tokens are not limited to 7 days.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

from config.settings import get_settings

logger = logging.getLogger("aegis.google_auth")

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
OAUTH_SCOPES = CALENDAR_SCOPES + GMAIL_SCOPES

_oauth_lock = threading.Lock()
_oauth_creds: Credentials | None = None
_service_account_creds: service_account.Credentials | None = None


def _parse_service_account_info(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("{"):
        return json.loads(text)
    path = Path(text)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError(
        "GOOGLE_SERVICE_ACCOUNT_JSON must be inline JSON or a path to a .json key file"
    )


def _discover_service_account_path() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    candidates = (
        root / "config" / "google_service_account.json",
        root / "google_service_account.json",
    )
    for path in candidates:
        if path.is_file():
            return path
    config_dir = root / "config"
    if config_dir.is_dir():
        for path in sorted(config_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("type") == "service_account" and data.get("client_email"):
                return path
    return None


def _service_account_config_raw() -> str | None:
    s = get_settings()
    raw = (s.google_service_account_json or "").strip()
    if raw:
        return raw
    discovered = _discover_service_account_path()
    if discovered:
        logger.info(
            "google_auth: using service account key from %s (set GOOGLE_SERVICE_ACCOUNT_JSON on Render)",
            discovered,
        )
        return str(discovered)
    return None


def service_account_configured() -> bool:
    return _service_account_config_raw() is not None


def get_service_account_email() -> str | None:
    """client_email from the service account key (for calendar sharing instructions)."""
    raw = _service_account_config_raw()
    if not raw:
        return None
    try:
        return str(_parse_service_account_info(raw)["client_email"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def get_calendar_credentials() -> Credentials | service_account.Credentials:
    """
    Prefer service account when configured; otherwise OAuth (same refresh token as Gmail).
    """
    global _service_account_creds

    raw = _service_account_config_raw()
    if raw:
        if _service_account_creds is None:
            info = _parse_service_account_info(raw)
            _service_account_creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=CALENDAR_SCOPES,
            )
            email = info.get("client_email", "?")
            logger.info(
                "google_auth: calendar via service account %s (no refresh token)",
                email,
            )
        return _service_account_creds

    return get_oauth_credentials()


def get_oauth_credentials() -> Credentials:
    """OAuth user credentials with in-process access-token cache + refresh."""
    global _oauth_creds

    s = get_settings()
    with _oauth_lock:
        if _oauth_creds is None:
            _oauth_creds = Credentials(
                token=None,
                refresh_token=s.google_refresh_token,
                client_id=s.google_client_id,
                client_secret=s.google_client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=OAUTH_SCOPES,
            )
        if not _oauth_creds.valid:
            _oauth_creds.refresh(Request())
        return _oauth_creds


def invalidate_oauth_cache() -> None:
    """Clear cached OAuth credentials after env rotation (tests / ops)."""
    global _oauth_creds
    with _oauth_lock:
        _oauth_creds = None
