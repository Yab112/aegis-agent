# Google APIs setup guide

You need three things: a Google Cloud project, OAuth 2.0 credentials,
and a refresh token. Takes about 15 minutes.

---

## Step 1 — Create a Google Cloud project

1. Go to https://console.cloud.google.com
2. Click **New Project** → name it `aegis-agent`
3. Select the project

---

## Step 2 — Enable required APIs

In the Google Cloud Console:

1. Go to **APIs & Services → Library**
2. Enable these (search each by name):
   - **Google Calendar API**
   - **Gmail API**

---

## Step 3 — Create OAuth 2.0 credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name: `aegis-agent-local`
5. Download the JSON — you'll need `client_id` and `client_secret`

Also configure the OAuth consent screen:
- User type: **External**
- App name: `Aegis Agent`
- Add your Gmail as a test user
- Scopes: `calendar`, `gmail.send`

---

## Step 4 — Get your refresh token (one-time)

Run this script locally (NOT on the server — it opens a browser):

```python
# scripts/get_google_token.py
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",  # the file you downloaded in Step 3
    scopes=SCOPES,
)
creds = flow.run_local_server(port=0)

print("ACCESS TOKEN:", creds.token)
print("REFRESH TOKEN:", creds.refresh_token)
print("CLIENT ID:", creds.client_id)
print("CLIENT SECRET:", creds.client_secret)
```

Run it:
```bash
pip install google-auth-oauthlib
python scripts/get_google_token.py
```

Copy the `REFRESH TOKEN` into your `.env` as `GOOGLE_REFRESH_TOKEN`.
You only need to do this once — the refresh token doesn't expire
unless you revoke it or exceed 50 unused tokens.

---

## Step 5 — Find your calendar ID

Your primary calendar ID is just your Gmail address.
To find others: Calendar Settings → your calendar → **Calendar ID**.

Set `GOOGLE_CALENDAR_ID=youremail@gmail.com` in `.env`.

---

## Env vars summary

```
GOOGLE_CLIENT_ID=your_client_id_from_step3
GOOGLE_CLIENT_SECRET=your_client_secret_from_step3
GOOGLE_REFRESH_TOKEN=your_refresh_token_from_step4
GOOGLE_CALENDAR_ID=youremail@gmail.com
CALENDAR_TIMEZONE=Africa/Addis_Ababa
```

---

## Testing calendar access

```python
from src.tools.calendar_tool import check_availability
slots = check_availability()
print(slots)
# Should print list of available 30-min slots
```
