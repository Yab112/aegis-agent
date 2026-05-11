# Telegram handoff + owner reply → visitor email

When the agent classifies a message as **handoff** (rates, contracts, hiring, etc.), it sends you a briefing via **Telegram Bot API** `sendMessage`.

When you **reply in Telegram to that alert** (same thread), the API receives a webhook, looks up the visitor’s email captured at handoff, and sends your reply text to them via **Gmail** (same Google OAuth as Calendar — `gmail.send` scope).

The visitor does **not** get Telegram from this flow; they get **email** only after you reply on Telegram.

---

## What the alert contains

- **Time** (UTC)
- **Session** id (short prefix)
- **Intent**
- **Email** (if the visitor shared one in the chat before handoff)
- **Question** (their latest message)
- A short note that **replying in-thread** emails your answer to the visitor (when email exists)

---

## Step 1 — Create a bot

1. Open Telegram and talk to [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, follow prompts, copy the **HTTP API token**.

```bash
TELEGRAM_BOT_TOKEN=123456789:AA...your_token_here
```

---

## Step 2 — Get your chat ID

1. Start a chat with your new bot (tap **Start** or send any message).
2. Open in a browser (replace `TOKEN`):

   `https://api.telegram.org/bot<TOKEN>/getUpdates`

3. In the JSON, find `"chat":{"id": ...` — that number is **`TELEGRAM_CHAT_ID`** (may be negative for groups).

```bash
TELEGRAM_CHAT_ID=123456789
```

---

## Step 3 — Supabase table

Run in **Supabase → SQL Editor** (once per project):

[`scripts/supabase_handoff_telegram.sql`](../scripts/supabase_handoff_telegram.sql)

This creates `handoff_telegram_alerts`, which maps each Telegram alert `message_id` to `session_id`, `visitor_email`, and `user_query` for the webhook.

---

## Step 4 — Webhook (HTTPS required)

1. Pick a long random secret. Telegram’s `secret_token` **must not** contain characters like `&`, `*`, or spaces — use **letters and digits** (and maybe `_-`) only. Set:

   ```bash
   TELEGRAM_WEBHOOK_SECRET=YourSafeAlphanumericSecretHere
   ```

2. Point Telegram at your public API (replace host and token):

   ```bash
   curl -fsS -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     --data-urlencode "url=https://YOUR_API_HOST/v1/telegram/webhook" \
     --data-urlencode "secret_token=$TELEGRAM_WEBHOOK_SECRET"
   ```

3. **Render / Koyeb**: use your service URL + `/v1/telegram/webhook`. Telegram rejects non-HTTPS webhook URLs.

If `TELEGRAM_WEBHOOK_SECRET` is set, every update must include header **`X-Telegram-Bot-Api-Secret-Token`** with that exact value (Telegram adds it when `secret_token` was set on `setWebhook`).

---

## Step 5 — Google OAuth (Gmail)

The same **`GOOGLE_*`** credentials as Calendar must work, and the OAuth consent / token must include **`gmail.send`** (the project’s `scripts/google_oauth_refresh_token.py` scopes already include it). If you rotated tokens before Gmail was added, run the refresh script again and update **`GOOGLE_REFRESH_TOKEN`**.

---

## How to use it day-to-day

1. Visitor triggers handoff; you get a **Telegram alert**.
2. In Telegram, use **Reply** on that alert (threaded reply).
3. Type your answer as **plain text** (your message body is emailed as-is).
4. If we had their **email**, they receive a Gmail message from your connected Google account. You get a short confirmation reply under your message on Telegram.

If there was **no email** on the alert, the bot replies on Telegram asking you to handle it manually or get an email from the visitor on the site.

---

## Optional: quick Python check

```python
from src.tools.handoff_tool import send_telegram_briefing

r = send_telegram_briefing(
    query="Test handoff from setup doc",
    intent="handoff",
    session_id="00000000-0000-0000-0000-000000000001",
    user_email="you@example.com",
)
assert r.ok
```

You should get a Telegram message within a few seconds.

---

## Environment summary

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Yes | Chat that receives alerts (your DM with the bot) |
| `TELEGRAM_WEBHOOK_SECRET` | Strongly recommended | Same value passed to `setWebhook` `secret_token` |

Remove any unused Meta Cloud API env vars from deployment if they are still present; the app only reads `TELEGRAM_*` for Telegram handoff.
