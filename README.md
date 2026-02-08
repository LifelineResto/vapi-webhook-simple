# Vapi Webhook Handler - Simple Version

A minimal Flask app that receives Vapi webhooks and:
1. Extracts customer data from structured outputs
2. Saves to Google Sheets
3. Sends SMS notifications

## Environment Variables Required

```
GOOGLE_CREDS_JSON=<your google service account JSON>
TWILIO_ACCOUNT_SID=<your twilio SID>
TWILIO_AUTH_TOKEN=<your twilio token>
TWILIO_PHONE_NUMBER=<your twilio number>
NOTIFICATION_PHONE=<phone to receive SMS>
PORT=5000
```

## Deployment to Railway

1. Create new Railway project
2. Connect this GitHub repo
3. Add all environment variables
4. Deploy
5. Copy the Railway URL and update Vapi assistant webhook URL

## Vapi Configuration

Set your assistant's webhook URL to: `https://your-railway-url.up.railway.app/webhook`
