# WhatsApp AI Receptionist

AI-powered WhatsApp bot that handles appointment scheduling for service-based businesses. Clients message on WhatsApp, the bot handles the conversation, checks real-time availability, and books directly into Google Calendar.

Built for nutritionists, dentists, physiotherapists, salons — any business that runs on appointments.

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![Tests](https://img.shields.io/badge/tests-42%20passed-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

## How it works

```
Client sends WhatsApp message
        │
        ▼
┌─────────────────┐
│  FastAPI webhook │ ◄── validates HMAC signature
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│   Claude AI     │ ◄───│  Knowledge   │
│  (conversation) │     │  base + config│
└────────┬────────┘     └──────────────┘
         │
         │ extracts structured intent
         ▼
   ┌─────┴──────┐
   │            │
   ▼            ▼
┌──────┐  ┌──────────┐
│ Book │  │ Cancel/  │
│      │  │ Modify   │
└──┬───┘  └────┬─────┘
   │           │
   ▼           ▼
┌─────────────────┐
│ Google Calendar  │ ◄── real-time availability check
└────────┬────────┘
         │
         ▼
   Confirmation via WhatsApp
```

## Features

- **Conversational booking** — natural language via WhatsApp, powered by Claude
- **Google Calendar integration** — real-time availability checks, slot locking, event creation
- **Full booking lifecycle** — create, cancel, and modify appointments
- **Audio messages** — voice messages transcribed via OpenAI Whisper
- **Smart date resolution** — "tomorrow", "next Wednesday", "next week" resolved to concrete dates
- **Appointment reminders** — automated WhatsApp reminders 24h before appointments
- **Optional payments** — Mercado Pago integration with checkout links and webhook confirmation
- **Per-client configuration** — YAML config + knowledge base per business, no code changes needed
- **Resilient** — Redis for production, in-memory fallback for development. Dual backend for history, locks, and pending state

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/martin-minghetti/whatsapp-ai-receptionist.git
cd whatsapp-ai-receptionist
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required:
- `ANTHROPIC_API_KEY` — [Get one here](https://console.anthropic.com/)
- `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` + `WHATSAPP_APP_SECRET` — [Meta Developer Portal](https://developers.facebook.com/)
- `WHATSAPP_VERIFY_TOKEN` — any string you choose (must match webhook config)

For booking (optional):
- `GOOGLE_SERVICE_ACCOUNT_JSON` — base64-encoded Google service account credentials
- `GOOGLE_CALENDAR_ID` + `GOOGLE_CALENDAR_OWNER_EMAIL`

Edit `config.yaml` with your business details and `knowledge/client.txt` with your knowledge base.

### 3. Run

```bash
uvicorn core.main:app --reload
```

### 4. Expose for WhatsApp

Use [ngrok](https://ngrok.com/) for local development:

```bash
ngrok http 8000
```

Set the webhook URL in [Meta Developer Portal](https://developers.facebook.com/) → WhatsApp → Configuration:
- Callback URL: `https://your-ngrok-url.ngrok.io/webhook`
- Verify token: same as your `WHATSAPP_VERIFY_TOKEN`

## Configuration

### config.yaml

```yaml
client:
  name: "Dr. Smith - Dentist"
  timezone: "America/New_York"

modules:
  booking: true      # Enable appointment scheduling
  payments: false    # Enable Mercado Pago payments
  reminders: true    # Enable 24h reminders

booking:
  business_hours:
    start: "09:00"
    end: "18:00"
  services:
    - name: "Cleaning"
      duration_minutes: 30
      price: 15000
    - name: "Consultation"
      duration_minutes: 45
      price: 20000
  locations:
    - name: "Main Office"
      address: "123 Main St"
      days: ["monday", "tuesday", "wednesday", "thursday", "friday"]
```

### knowledge/client.txt

Free-text knowledge base about the business. The AI uses this to answer questions. Write it like you'd explain the business to a new receptionist.

## Testing

```bash
pytest tests/ -v
```

42 tests covering all modules — webhook handling, AI intent extraction, calendar operations, payment flows, reminders, and configuration.

## Deploy

### Railway (recommended)

The repo includes `railway.toml` ready to go:

```bash
railway up
```

Set environment variables in Railway dashboard. Add a cron job for reminders:
```
curl -X POST https://your-app.railway.app/internal/send-reminders \
  -H "X-Internal-Secret: $INTERNAL_SECRET"
```

### Other platforms

Any platform that runs Python + FastAPI works. The app starts with:

```bash
uvicorn core.main:app --host 0.0.0.0 --port $PORT
```

## Architecture

```
whatsapp-ai-receptionist/
├── core/
│   ├── main.py          # FastAPI app, webhook handlers, intent routing
│   ├── ai.py            # Claude integration, system prompt, intent extraction
│   ├── whatsapp.py      # WhatsApp Cloud API client
│   ├── transcribe.py    # Whisper audio transcription
│   ├── history.py       # Conversation history (Redis / in-memory)
│   └── phone.py         # Phone number normalization
├── config/
│   └── loader.py        # YAML config with ${ENV_VAR} substitution
├── modules/
│   ├── booking/
│   │   └── calendar.py  # Google Calendar with slot locking
│   └── payments/
│       └── mercadopago.py  # Mercado Pago checkout + webhook
├── reminders/
│   └── scheduler.py     # 24h reminder sender
├── knowledge/
│   └── client.txt       # Business knowledge base
├── config.yaml          # Per-client configuration
└── tests/               # 42 tests, full coverage
```

### Key design decisions

See [DECISIONS.md](DECISIONS.md) for detailed rationale on technology choices.

**Intent extraction over function calling** — Claude generates a natural response with a JSON intent block appended. The system extracts the intent and routes to the appropriate handler. This keeps the conversation natural while enabling structured actions.

**Dual Redis/in-memory backend** — Every stateful component (history, locks, pending payments) works with Redis in production and falls back to in-memory for development. No Redis required to run locally.

**Config-driven, not code-driven** — New clients are onboarded by editing `config.yaml` and `knowledge/client.txt`. No code changes needed. The system prompt is dynamically generated from config.

## License

MIT
