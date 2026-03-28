# WhatsApp Cloud API setup guide

Free tier: 1,000 business-initiated messages per month — far more than enough
for lead notifications on a portfolio site.

---

## Step 1 — Create a Meta Developer account

1. Go to https://developers.facebook.com
2. Sign in with your Facebook account (or create one)
3. Click **My Apps → Create App**
4. App type: **Business**
5. App name: `Aegis Agent`

---

## Step 2 — Add WhatsApp to your app

1. Inside your app dashboard, click **Add a Product**
2. Find **WhatsApp** and click **Set up**
3. You'll land on the WhatsApp Getting Started page

---

## Step 3 — Get your Phone Number ID and temporary token

On the WhatsApp Getting Started page:

- Copy the **Phone Number ID** → `WHATSAPP_PHONE_NUMBER_ID`
- Copy the **Temporary access token** (valid 24h — we'll make it permanent next)

---

## Step 4 — Generate a permanent access token

The temporary token expires in 24 hours. For production you need a permanent one.

1. Go to **Business Settings → System Users**
2. Create a system user named `aegis-agent`
3. Assign it **Full Control** over your WhatsApp Business Account
4. Click **Generate Token**
5. Select your app and these permissions:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
6. Copy the generated token → `WHATSAPP_ACCESS_TOKEN`

---

## Step 5 — Add your personal number as a recipient

On the free tier, you can only send messages to numbers you've verified.

1. In **WhatsApp → Getting Started**, find **To** field
2. Click **Manage phone number list**
3. Add your personal WhatsApp number (with country code, e.g. `+251912345678`)
4. Verify it via the WhatsApp message you receive

Set this as `WHATSAPP_RECIPIENT_NUMBER=+251XXXXXXXXX` in `.env`.

---

## Step 6 — Test it

```python
from src.tools.handoff_tool import send_whatsapp_briefing

result = send_whatsapp_briefing(
    query="What is your rate for a 3-month contract?",
    intent="handoff",
    session_id="test-session-001",
    user_email="client@example.com",
)
print("Sent:", result)
```

You should receive a WhatsApp message within a few seconds.

---

## Env vars summary

```
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_ACCESS_TOKEN=your_permanent_system_user_token
WHATSAPP_RECIPIENT_NUMBER=+251XXXXXXXXX
```

---

## Free tier limits

| Limit | Value |
|---|---|
| Business-initiated messages | 1,000/month |
| User-initiated conversations | Unlimited |
| Message templates required | Only for business-initiated |

Since Aegis-Agent sends plain text (not templates), the 1,000/month
limit applies. That's roughly 33 lead alerts per day — well above
what a portfolio site will generate.
