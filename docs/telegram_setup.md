# Telegram handoff + owner reply → visitor email

When the agent classifies a message as **handoff** (rates, contracts, hiring, etc.), it sends you a briefing via **Telegram Bot API** `sendMessage`.

When you **reply in Telegram to that alert** (same thread), the API receives a webhook, looks up the visitor’s email captured at handoff, and sends your reply to them via **Gmail** (same Google OAuth as Calendar — `gmail.send` scope). The outbound message is **multipart** (plain + **HTML**): optional **logo** in the header, polished **body**, and a **footer** with your name, role, portfolio link, and session reference. By default **Gemini** rewrites your Telegram draft into clearer professional prose using the visitor’s question and intent as context (toggle with `VISITOR_REPLY_EMAIL_POLISH`).

The visitor does **not** get Telegram from this flow; they get **email** only after you reply on Telegram.

**Typical delay:** a few seconds after Telegram delivers the webhook to your API — mostly **Gemini** (when polish is on) plus **Gmail** `users.messages.send`. Cold starts on free hosts add extra latency on the first request.

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

This creates `handoff_telegram_alerts`, which maps each Telegram alert `message_id` to `session_id`, `visitor_email`, `user_query`, and **`intent`** (for email polish context). If you created the table before `intent` existed, run [`scripts/supabase_handoff_telegram_intent.sql`](../scripts/supabase_handoff_telegram_intent.sql) once to add the column.

---

## Step 4 — Webhook (HTTPS required)

1. Pick a long random secret. Telegram’s Bot API only allows **`A–Z`, `a–z`, `0–9`, `_`, `-`** in `secret_token` (no spaces, `&`, `*`, `#`, etc.). If you use anything else, `setWebhook` can fail or updates may not match your app’s `TELEGRAM_WEBHOOK_SECRET` (401). Set:

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

### Webhook returns **401**

That means the app rejected the request: the header did not match your env secret.

- **Same value everywhere**: the string in **`TELEGRAM_WEBHOOK_SECRET`** on the host (e.g. Render) must be **byte-for-byte identical** to the `secret_token` you passed to `setWebhook` (no extra quotes, spaces, or a different secret on staging vs production).  
  **Common mistake:** you ran `setWebhook` in a one-liner with secret `A`, but Render still has old secret `B` — Telegram always sends `A` in `X-Telegram-Bot-Api-Secret-Token`, so the app must use **`A`** in `TELEGRAM_WEBHOOK_SECRET` (or call `setWebhook` again with `B` after changing Render).
- **Unset secret**: if the app has **no** `TELEGRAM_WEBHOOK_SECRET`, it does not check the header. If the app **has** a secret but Telegram was registered **without** `secret_token`, Telegram will not send the header → mismatch. Fix by either setting `secret_token` on `setWebhook` to match env, or clearing the env var if you accept unsigned webhooks (not recommended).
- After changing env on Render, **redeploy** or restart so the new value is loaded.

---

## Step 5 — Google OAuth (Gmail)

The same **`GOOGLE_*`** credentials as Calendar must work, and the OAuth consent / token must include **`gmail.send`** (the project’s `scripts/google_oauth_refresh_token.py` scopes already include it). If you rotated tokens before Gmail was added, run the refresh script again and update **`GOOGLE_REFRESH_TOKEN`**.

---

## How to use it day-to-day

1. Visitor triggers handoff; you get a **Telegram alert**.
2. Use **Reply** on **that alert bubble** (swipe / quote the **notifyYab** “Aegis-Agent Lead Alert” message from the bot). A **new standalone message** is not linked to any visitor — only replies threaded to that alert’s `message_id` are emailed. If you get **several alerts** in a row (e.g. first question, then a follow-up with email), each bubble is separate: use Reply on **one** of them (often the **latest**) so your text is threaded to that message; do **not** reply only to your own previous message.
3. Type your answer as **plain text** (draft for Gemini when `VISITOR_REPLY_EMAIL_POLISH` is true).
4. If we had their **email**, they receive a branded **HTML** Gmail message; you get a short confirmation threaded under your reply on Telegram.

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
| `EMAIL_BRAND_LOGO_URL` | Optional | HTTPS image URL for HTML email header |
| `VISITOR_REPLY_EMAIL_POLISH` | Optional (default on) | `false` / `0` / `off` to send your Telegram text without Gemini rewrite |

Remove any unused Meta Cloud API env vars from deployment if they are still present; the app only reads `TELEGRAM_*` for Telegram handoff.
