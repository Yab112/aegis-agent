"""
Google Calendar + Meet tool.

Checks real-time availability and creates Google Meet links.
Uses OAuth2 refresh token flow — no browser needed after initial setup.

APIs used (all free):
- Google Calendar API v3
- Google Meet (via Calendar event with conferenceData)
- Gmail API (send confirmation email)
"""
from __future__ import annotations

import base64
import logging
from datetime import date, datetime, time as dt_time, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from config.settings import get_settings

settings = get_settings()
logger = logging.getLogger("aegis.calendar")

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]


class CalendarOAuthError(RuntimeError):
    """Google rejected the OAuth client, or the refresh token is invalid/revoked."""


def _message_for_refresh_error(err: RefreshError) -> str:
    """Map Google's error to an actionable message (client id/secret vs refresh token)."""
    raw = str(err).lower()
    if "invalid_grant" in raw:
        return (
            "Google refresh token is expired or revoked (invalid_grant). "
            "Update GOOGLE_REFRESH_TOKEN: from the project root run "
            "`python scripts/google_oauth_refresh_token.py` (venv), sign in, then paste "
            "the printed token into .env. Client id/secret can be correct while the old "
            "refresh token is dead."
        )
    if "invalid_client" in raw:
        return (
            "Google OAuth client rejected (invalid_client). Check GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET in Google Cloud Console → APIs & Services → Credentials, "
            "and that GOOGLE_REFRESH_TOKEN was created with that same OAuth client."
        )
    return (
        "Google OAuth token refresh failed. Verify GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
        "and GOOGLE_REFRESH_TOKEN (see .env.example)."
    )


def _get_credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def _reraise_refresh_as_clear(err: RefreshError) -> None:
    logger.error("calendar: OAuth token refresh failed: %s", err)
    raise CalendarOAuthError(_message_for_refresh_error(err)) from err


def _busy_bounds_from_events(items: list, cal_tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """Event bounds as timezone-aware UTC for overlap checks."""
    out: list[tuple[datetime, datetime]] = []
    for e in items:
        s = e.get("start") or {}
        en = e.get("end") or {}
        if s.get("dateTime"):
            a = datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(en["dateTime"].replace("Z", "+00:00"))
            if a.tzinfo is None:
                a = a.replace(tzinfo=timezone.utc)
            if b.tzinfo is None:
                b = b.replace(tzinfo=timezone.utc)
            out.append((a.astimezone(timezone.utc), b.astimezone(timezone.utc)))
        elif s.get("date"):
            # All-day: Google uses end date exclusive
            ds = date.fromisoformat(s["date"])
            de = date.fromisoformat(en["date"])
            start_local = datetime.combine(ds, dt_time.min, tzinfo=cal_tz)
            end_local = datetime.combine(de, dt_time.min, tzinfo=cal_tz)
            out.append(
                (start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc))
            )
    return out


def _slot_free(
    start_utc: datetime,
    end_utc: datetime,
    busy: list[tuple[datetime, datetime]],
) -> bool:
    return not any(bs < end_utc and be > start_utc for bs, be in busy)


def _utc_from_slot_iso(iso: str) -> datetime:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def drop_past_slots(slots: list[dict], cutoff_utc: datetime | None = None) -> list[dict]:
    """Remove any slot whose end time is not strictly after ``cutoff_utc`` (default: now)."""
    cut = cutoff_utc or datetime.now(timezone.utc)
    if cut.tzinfo is None:
        cut = cut.replace(tzinfo=timezone.utc)
    return [s for s in slots if _utc_from_slot_iso(s["end"]) > cut]


def _working_day_morning_slots_local() -> list[dt_time]:
    """Starts every 30 minutes from 09:00 through 17:30 (last block ends 18:00)."""
    times: list[dt_time] = []
    for mins in range(9 * 60, 18 * 60, 30):
        times.append(dt_time(mins // 60, mins % 60))
    return times


def check_availability(days_ahead: int = 14, max_collect: int = 72) -> list[dict]:
    """
    Collect free 30-minute slots (weekdays, 09:00–18:00 local) up to ``max_collect``.

    Uses ``settings.calendar_timezone`` for business hours (previous code used UTC hours,
    which skewed results). Callers should use :func:`diversify_slots` or
    :func:`filter_slots_by_weekday` to pick a user-facing subset.
    """
    creds = _get_credentials()
    service = build("calendar", "v3", credentials=creds)
    cal_tz = ZoneInfo(settings.calendar_timezone)

    logger.info(
        "calendar: events.list calendarId=%s (Google Calendar API v3)",
        settings.google_calendar_id,
    )

    now_utc = datetime.now(timezone.utc)
    time_max = now_utc + timedelta(days=days_ahead)

    try:
        events_result = service.events().list(
            calendarId=settings.google_calendar_id,
            timeMin=now_utc.isoformat().replace("+00:00", "Z"),
            timeMax=time_max.isoformat().replace("+00:00", "Z"),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except RefreshError as e:
        _reraise_refresh_as_clear(e)

    busy = _busy_bounds_from_events(events_result.get("items", []), cal_tz)

    available_slots: list[dict] = []
    local_today = now_utc.astimezone(cal_tz).date()
    last_day = time_max.astimezone(cal_tz).date()
    day = local_today

    while day <= last_day and len(available_slots) < max_collect:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        for t0 in _working_day_morning_slots_local():
            start_local = datetime.combine(day, t0, tzinfo=cal_tz)
            end_local = start_local + timedelta(minutes=30)
            start_utc = start_local.astimezone(timezone.utc)
            end_utc = end_local.astimezone(timezone.utc)
            if end_utc <= now_utc or start_utc >= time_max:
                continue
            if _slot_free(start_utc, end_utc, busy):
                available_slots.append({
                    "start": start_utc.isoformat(),
                    "end": end_utc.isoformat(),
                })
        day += timedelta(days=1)

    # Defense in depth: never offer slots that already ended (clock skew / old processes).
    cutoff = datetime.now(timezone.utc)
    before = len(available_slots)
    available_slots = drop_past_slots(available_slots, cutoff)
    if before != len(available_slots):
        logger.info(
            "calendar: after past-slot filter: %d slots (removed %d)",
            len(available_slots),
            before - len(available_slots),
        )

    first_hint = ""
    if available_slots:
        try:
            st = _utc_from_slot_iso(available_slots[0]["start"]).astimezone(cal_tz)
            first_hint = f" first_local={st.strftime('%a %b %d %H:%M')} {settings.calendar_timezone}"
        except Exception:
            first_hint = ""

    logger.info(
        "calendar: computed %d future free slots (30m, tz=%s, local_today=%s, now_utc=%s%s)",
        len(available_slots),
        settings.calendar_timezone,
        local_today,
        cutoff.isoformat().replace("+00:00", "Z"),
        first_hint,
    )
    return available_slots


def diversify_slots(slots: list[dict], tz: ZoneInfo, max_n: int = 5) -> list[dict]:
    """
    Prefer one early slot per local calendar day (spreads across Mon/Tue/… when possible).
    If fewer than ``max_n`` distinct days have openings, backfill with the next free
    slots in time order so the user still gets up to ``max_n`` options.
    """
    if not slots:
        return []
    by_day: dict[date, list[dict]] = {}
    for s in slots:
        start = datetime.fromisoformat(s["start"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        d = start.astimezone(tz).date()
        by_day.setdefault(d, []).append(s)

    picked: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for d in sorted(by_day.keys()):
        first = by_day[d][0]
        key = (first["start"], first["end"])
        if key not in seen:
            seen.add(key)
            picked.append(first)
        if len(picked) >= max_n:
            return picked

    for s in slots:
        if len(picked) >= max_n:
            break
        key = (s["start"], s["end"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(s)
    return picked[:max_n]


def filter_slots_by_weekday(slots: list[dict], tz: ZoneInfo, weekday: int) -> list[dict]:
    """``weekday``: 0=Monday … 6=Sunday. Preserve chronological order, cap at 5."""
    out: list[dict] = []
    for s in slots:
        start = datetime.fromisoformat(s["start"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start.astimezone(tz).weekday() == weekday:
            out.append(s)
        if len(out) >= 5:
            break
    return out


def create_meet_link(slot: dict, attendee_email: str | None = None) -> str | None:
    """
    Create a Google Calendar event with a Meet link for the given slot.
    Optionally invite the attendee by email.

    Returns the Google Meet URL or None on failure.
    """
    creds = _get_credentials()
    service = build("calendar", "v3", credentials=creds)
    logger.info(
        "calendar: events.insert + Meet conferenceData calendarId=%s",
        settings.google_calendar_id,
    )

    attendees = []
    if attendee_email:
        attendees.append({"email": attendee_email})

    event = {
        "summary": f"Intro call with {settings.owner_name}",
        "description": (
            f"Discovery call arranged via {settings.portfolio_url}.\n"
            f"Agenda: discuss your project requirements with {settings.owner_name}."
        ),
        "start": {"dateTime": slot["start"], "timeZone": settings.calendar_timezone},
        "end": {"dateTime": slot["end"], "timeZone": settings.calendar_timezone},
        "attendees": attendees,
        "conferenceData": {
            "createRequest": {
                "requestId": f"aegis-{slot['start']}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 10},
            ],
        },
    }

    try:
        created = service.events().insert(
            calendarId=settings.google_calendar_id,
            body=event,
            conferenceDataVersion=1,
            sendUpdates="all" if attendee_email else "none",
        ).execute()

        meet_link = created.get("hangoutLink")
        logger.info("calendar: Meet link created ok=%s", bool(meet_link))
        return meet_link
    except RefreshError as e:
        _reraise_refresh_as_clear(e)
    except Exception as e:
        logger.exception("calendar: events.insert failed: %s", e)
        return None


def send_gmail_plain_text(*, to_addr: str, subject: str, body: str) -> bool:
    """
    Send a plain-text email from the OAuth-connected Google account (gmail.send scope).
    Used for visitor follow-up when the owner replies to a Telegram handoff thread.
    """
    to_addr = (to_addr or "").strip()
    if not to_addr:
        return False
    try:
        creds = _get_credentials()
        service = build("gmail", "v1", credentials=creds)
        msg = EmailMessage()
        msg["To"] = to_addr
        msg["Subject"] = (subject or "Message from portfolio").strip() or "Message from portfolio"
        msg.set_content(body or "")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        logger.info("gmail: sent plain text to=%s", to_addr[:3] + "…")
        return True
    except RefreshError as e:
        _reraise_refresh_as_clear(e)
    except Exception as e:
        logger.exception("gmail: send failed: %s", e)
        return False
