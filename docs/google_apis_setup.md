# Google APIs setup guide

Two auth paths:

| Feature | Auth | Expires? |
|---------|------|----------|
| **Calendar + Meet** (booking) | **Service account** (recommended) | Only if you revoke the key |
| **Gmail** (Telegram → visitor email) | OAuth refresh token | Stable if consent screen is **Production** |

OAuth-only setup works but refresh tokens break when the app stays in **Testing** (~7 days), you rotate OAuth clients, or Google revokes access. Use the service account for calendar so `/chat` booking never depends on a refresh token.

---

## Durable production setup (recommended)

### A — Service account for Calendar + Meet

1. [Google Cloud Console](https://console.cloud.google.com) → **IAM & Admin → Service Accounts → Create**
2. Name: `aegis-calendar` → Create
3. **Keys → Add key → JSON** → save as `config/google_service_account.json` (gitignored)
4. **APIs & Services → Library** → enable **Google Calendar API** (same project)
5. **Google Calendar** (web) → Settings → your calendar → **Share with specific people**
   - Add the service account email (`something@project-id.iam.gserviceaccount.com`)
   - Permission: **Make changes to events**
6. Run helper:
   ```bash
   python scripts/google_service_account_setup.py
   ```
7. **Render** → Environment → add `GOOGLE_SERVICE_ACCOUNT_JSON` = paste the **entire JSON file as one line**

Local `.env` can use a path instead:

```
GOOGLE_SERVICE_ACCOUNT_JSON=config/google_service_account.json
```

Keep `GOOGLE_CALENDAR_ID=youremail@gmail.com` (the calendar you shared).

### B — OAuth for Gmail only

1. **OAuth consent screen** → set **Publishing status** to **Production** (not Testing)
   - Testing mode refresh tokens expire after ~7 days — this is the #1 cause of repeat `invalid_grant` errors
2. **Credentials → OAuth client ID → Desktop app** → download JSON to `config/client_secret_….json`
3. Run:
   ```bash
   python scripts/google_oauth_refresh_token.py
   ```
4. Put printed values in `.env` and Render:
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   GOOGLE_REFRESH_TOKEN=...
   ```

Calendar uses the service account; Gmail uses OAuth. Booking keeps working even if Gmail OAuth needs a refresh later.

---

## Quick start (OAuth only — not recommended for Render)

If you skip the service account, you need OAuth for both Calendar and Gmail. Takes ~15 minutes.

### Step 1 — Create a Google Cloud project

1. Go to https://console.cloud.google.com
2. Click **New Project** → name it `aegis-agent`
3. Select the project

### Step 2 — Enable required APIs

1. Go to **APIs & Services → Library**
2. Enable:
   - **Google Calendar API**
   - **Gmail API**

### Step 3 — OAuth credentials

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Application type: **Desktop app**
3. Download JSON → `config/client_secret_….json`

OAuth consent screen:

- User type: **External**
- App name: `Aegis Agent`
- Scopes: `calendar`, `gmail.send`
- **Publish to Production** when ready (see above)

### Step 4 — Refresh token

```bash
python scripts/google_oauth_refresh_token.py
```

Copy `GOOGLE_REFRESH_TOKEN` (and client id/secret) into `.env` and Render.

### Step 5 — Calendar ID

Primary calendar ID = your Gmail address.

```
GOOGLE_CALENDAR_ID=youremail@gmail.com
CALENDAR_TIMEZONE=Africa/Addis_Ababa
```

---

## Env vars summary (production)

```
# Calendar + Meet — does not expire
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}

# Gmail — publish OAuth app to Production
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...

GOOGLE_CALENDAR_ID=youremail@gmail.com
CALENDAR_TIMEZONE=Africa/Addis_Ababa
```

---

## Testing calendar access

```python
from src.tools.calendar_tool import check_availability
slots = check_availability()
print(slots)
```
