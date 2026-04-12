import json as json_lib
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, time as dt_time

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

from config.loader import load_config
from core.ai import extract_booking_intent, get_ai_response, load_knowledge
from core.history import get_history
from core.transcribe import transcribe_audio
from core.whatsapp import WhatsAppClient, validate_webhook_signature
from modules.booking.calendar import CalendarClient, Slot
from modules.payments.mercadopago import MPClient, validate_mp_signature
from reminders.scheduler import send_reminders

logger = logging.getLogger(__name__)

CONFIG = load_config()
KNOWLEDGE = load_knowledge()
HISTORY = get_history()

WA = WhatsAppClient(
    phone_number_id=os.environ["WHATSAPP_PHONE_NUMBER_ID"],
    access_token=os.environ["WHATSAPP_ACCESS_TOKEN"],
)
APP_SECRET = os.environ["WHATSAPP_APP_SECRET"]
VERIFY_TOKEN = os.environ["WHATSAPP_VERIFY_TOKEN"]
INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")
MP_WEBHOOK_SECRET = os.environ.get("MP_WEBHOOK_SECRET", "")

_calendar_client: CalendarClient | None = None
_mp_client: MPClient | None = None
_pending_redis = None

# In-memory fallback for pending operations (used when Redis is not available)
_pending_modifications: dict[str, dict] = {}
_pending_cancellations: dict[str, list[dict]] = {}
_message_locks: dict[str, float] = {}  # phone -> lock expiry timestamp

# Keywords that indicate the user wants to modify an existing booking
_MODIFICATION_KEYWORDS = {"change", "modify", "move", "reschedule", "switch", "postpone", "cambiar", "modificar", "mover", "reprogramar"}


def _get_mp_client() -> MPClient | None:
    if not CONFIG.get("modules", {}).get("payments"):
        return None
    global _mp_client
    if _mp_client is None:
        sandbox = CONFIG.get("payments", {}).get("sandbox", True)
        _mp_client = MPClient(sandbox=sandbox)
    return _mp_client


def _get_pending_payment_redis():
    global _pending_redis
    if _pending_redis is None:
        redis_url = os.environ.get("REDIS_URL")
        if redis_url:
            import redis
            _pending_redis = redis.from_url(redis_url, decode_responses=True)
    return _pending_redis


def _acquire_message_lock(phone: str, ttl: int = 15) -> bool:
    """Try to acquire a processing lock for this phone. Returns True if acquired."""
    r = _get_pending_payment_redis()
    if r:
        key = f"msg_lock:{phone}"
        return bool(r.set(key, "1", nx=True, ex=ttl))
    # In-memory fallback
    import time
    now = time.time()
    expiry = _message_locks.get(phone, 0)
    if now < expiry:
        return False  # Lock still held
    _message_locks[phone] = now + ttl
    return True


def _release_message_lock(phone: str):
    r = _get_pending_payment_redis()
    if r:
        r.delete(f"msg_lock:{phone}")
    else:
        _message_locks.pop(phone, None)


def _save_pending_modification(phone: str, event: dict, ttl: int = 600):
    """Store event pending modification (will be deleted when new booking is confirmed)."""
    r = _get_pending_payment_redis()
    if r:
        r.setex(f"pending_modification:{phone}", ttl, json_lib.dumps(event))
    else:
        _pending_modifications[phone] = event


def _get_pending_modification(phone: str) -> dict | None:
    r = _get_pending_payment_redis()
    if r:
        raw = r.get(f"pending_modification:{phone}")
        if raw:
            return json_lib.loads(raw)
        return None
    return _pending_modifications.get(phone)


def _delete_pending_modification(phone: str):
    r = _get_pending_payment_redis()
    if r:
        r.delete(f"pending_modification:{phone}")
    else:
        _pending_modifications.pop(phone, None)


def _save_pending_cancellation(phone: str, events: list[dict], ttl: int = 600):
    """Store events pending cancellation for this phone (10 min TTL)."""
    r = _get_pending_payment_redis()
    if r:
        r.setex(f"pending_cancellation:{phone}", ttl, json_lib.dumps(events))
    else:
        _pending_cancellations[phone] = events


def _get_pending_cancellation(phone: str) -> list[dict] | None:
    """Get events pending cancellation for this phone."""
    r = _get_pending_payment_redis()
    if r:
        raw = r.get(f"pending_cancellation:{phone}")
        if raw:
            return json_lib.loads(raw)
        return None
    return _pending_cancellations.get(phone)


def _delete_pending_cancellation(phone: str):
    r = _get_pending_payment_redis()
    if r:
        r.delete(f"pending_cancellation:{phone}")
    else:
        _pending_cancellations.pop(phone, None)


def _save_pending_payment(phone: str, data: dict, ttl: int = 1800):
    """Store pending payment keyed by phone (used as external_reference in MP)."""
    r = _get_pending_payment_redis()
    if r:
        r.setex(f"pending_payment:{phone}", ttl, json_lib.dumps(data))


def _get_and_delete_pending_payment(payment: dict) -> dict | None:
    """Look up pending payment using external_reference from the MP payment object."""
    r = _get_pending_payment_redis()
    if not r:
        return None
    phone = payment.get("external_reference", "")
    if not phone:
        return None
    key = f"pending_payment:{phone}"
    raw = r.get(key)
    if raw:
        r.delete(key)
        return json_lib.loads(raw)
    return None


def _get_calendar_client() -> CalendarClient | None:
    if not CONFIG.get("modules", {}).get("booking"):
        return None
    global _calendar_client
    if _calendar_client is None:
        booking_cfg = CONFIG["booking"]
        _calendar_client = CalendarClient(
            calendar_id=booking_cfg["calendar_id"],
            calendar_owner_email=booking_cfg["calendar_owner_email"],
            business_hours=booking_cfg["business_hours"],
            timezone=CONFIG["client"].get("timezone", "America/Argentina/Buenos_Aires"),
        )
    return _calendar_client


def _find_service(service_name: str) -> dict | None:
    services = CONFIG.get("booking", {}).get("services", [])
    name = service_name.lower()
    # Exact match first
    for s in services:
        if s["name"].lower() == name:
            return s
    # Substring match: prioritize longest match to avoid partial matches (e.g. "Dental Cleaning" matching before "Cleaning + Checkup (bundle)")
    matches = []
    for s in services:
        s_lower = s["name"].lower()
        if name in s_lower or s_lower in name:
            matches.append(s)
    if matches:
        # Return the service with the longest name (most specific match)
        return max(matches, key=lambda s: len(s["name"]))
    logger.warning("Service not found: '%s'. Available: %s", service_name,
                   [s["name"] for s in services])
    return None


def _find_location(location_name: str) -> dict | None:
    locations = CONFIG.get("booking", {}).get("locations", [])
    name = location_name.lower()
    for loc in locations:
        if loc["name"].lower() == name:
            return loc
    # Substring match
    for loc in locations:
        loc_lower = loc["name"].lower()
        if name in loc_lower or loc_lower in name:
            return loc
    logger.warning("Location not found: '%s'. Available: %s", location_name,
                   [loc["name"] for loc in locations])
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health():
    return {"status": "ok"}


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403)


@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not validate_webhook_signature(body, signature, APP_SECRET):
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = await request.json()

    try:
        entry = data["entry"][0]
        change = entry["changes"][0]["value"]

        # Ignore status updates (delivered, read, etc.)
        if "statuses" in change:
            return Response(status_code=200)

        message = change["messages"][0]
        phone = message["from"]
        msg_type = message.get("type")

        if msg_type == "text":
            user_text = message["text"]["body"]
        elif msg_type == "audio":
            media_id = message["audio"]["id"]
            try:
                audio_bytes, mime_type = await WA.download_media(media_id)
                user_text = transcribe_audio(audio_bytes, mime_type)
                logger.info("Audio transcribed for %s: %s", phone, user_text[:100])
            except Exception as e:
                logger.error("Audio transcription failed: %s", e)
                await WA.send_text(phone, "I couldn't process your audio. Could you send it as text instead?")
                return Response(status_code=200)
        else:
            await WA.send_text(phone, "I can only process text and audio messages for now.")
            return Response(status_code=200)

    except (KeyError, IndexError):
        return Response(status_code=200)

    # Prevent concurrent processing for the same phone number
    if not _acquire_message_lock(phone):
        logger.info("Message from %s skipped (already processing)", phone)
        # Still add to history so the next response has context
        HISTORY.add(phone, "user", user_text)
        return Response(status_code=200)

    try:
        await _process_message(phone, user_text)
    finally:
        _release_message_lock(phone)

    return Response(status_code=200)


async def _process_message(phone: str, user_text: str):
    """Process a single message with lock already held."""
    # Get conversation history
    history = HISTORY.get(phone)
    HISTORY.add(phone, "user", user_text)

    # Proactively detect modification intent from user text and save pending state
    # This ensures the state survives even if many messages pass before booking_confirmed
    if not _get_pending_modification(phone):
        words = set(user_text.lower().split())
        if words & _MODIFICATION_KEYWORDS:
            cal = _get_calendar_client()
            if cal:
                events = cal.find_upcoming_events_by_phone(phone)
                if events:
                    _save_pending_modification(phone, events[0])
                    logger.info("Proactive modification state saved for %s", phone)

    # Get AI response
    ai_response = get_ai_response(user_text, history, CONFIG, KNOWLEDGE)

    # Check for intent
    intent, visible_response = extract_booking_intent(ai_response)

    if intent and CONFIG.get("modules", {}).get("booking"):
        intent_type = intent.get("intent", "")
        logger.info("Intent detected: %s", intent)

        if intent_type == "booking_confirmed":
            await _handle_booking_intent(phone, intent, visible_response)
        elif intent_type == "cancellation_request":
            await _handle_cancellation_request(phone, visible_response)
        elif intent_type == "cancellation_confirmed":
            await _handle_cancellation_confirmed(phone, intent, visible_response)
        elif intent_type == "modification_request":
            await _handle_modification_request(phone, visible_response)
        elif intent_type == "modification_confirmed":
            await _handle_modification_confirmed(phone, intent, visible_response)
        else:
            HISTORY.add(phone, "assistant", visible_response or ai_response)
            await WA.send_text(phone, visible_response or ai_response)
    else:
        HISTORY.add(phone, "assistant", ai_response)
        await WA.send_text(phone, ai_response)


def _conversation_suggests_modification(history: list[dict]) -> bool:
    """Check recent conversation history for modification-related keywords."""
    # Look at the last 6 messages (3 exchanges) for modification signals
    recent = history[-6:] if len(history) > 6 else history
    for msg in recent:
        if msg["role"] == "user":
            words = set(msg["content"].lower().split())
            if words & _MODIFICATION_KEYWORDS:
                return True
    return False


async def _handle_booking_intent(phone: str, intent: dict, visible_response: str):
    """Process a confirmed booking intent."""
    cal = _get_calendar_client()
    if cal is None:
        HISTORY.add(phone, "assistant", visible_response)
        await WA.send_text(phone, visible_response)
        return

    logger.info("[INTENT] %s", intent)
    service = _find_service(intent.get("service", ""))
    location = _find_location(intent.get("location", ""))

    if not service or not location:
        logger.error("Service or location not found. service='%s' location='%s'", intent.get('service'), intent.get('location'))
        error_msg = "There was a problem processing your booking. Please contact us directly."
        HISTORY.add(phone, "assistant", error_msg)
        await WA.send_text(phone, error_msg)
        return

    try:
        booking_date = date.fromisoformat(intent["date"])
        booking_time = dt_time.fromisoformat(intent["time"])
    except (ValueError, KeyError) as e:
        logger.warning("Invalid date/time in intent: %s", e)
        error_msg = "There was a problem with your booking date. Could you try again?"
        HISTORY.add(phone, "assistant", error_msg)
        await WA.send_text(phone, error_msg)
        return

    # Validate date is not in the past
    if booking_date < date.today():
        logger.warning("Booking date in the past: %s", booking_date)
        error_msg = "That date has already passed. Would you like me to suggest the next available dates?"
        HISTORY.add(phone, "assistant", error_msg)
        await WA.send_text(phone, error_msg)
        return

    slot = Slot(date=booking_date, start_time=booking_time, location=location["name"])

    if not cal.is_slot_available(booking_date, booking_time, service["duration_minutes"]):
        sorry = (
            f"That time slot ({booking_date.strftime('%m/%d/%Y')} at {booking_time.strftime('%H:%M')}) "
            f"is already taken. Would you like another time that same day or a different day?"
        )
        HISTORY.add(phone, "assistant", sorry)
        await WA.send_text(phone, sorry)
        return

    payments_enabled = CONFIG.get("modules", {}).get("payments", False)

    # If this booking is part of a modification, delete the old event first
    pending_mod = _get_pending_modification(phone)

    # Fallback: if Haiku skipped modification_request, detect from conversation history
    if not pending_mod:
        history = HISTORY.get(phone)
        if _conversation_suggests_modification(history):
            events = cal.find_upcoming_events_by_phone(phone)
            if events:
                # Use the first (soonest) event as the one being modified
                pending_mod = events[0]
                logger.info("Modification detected from conversation context for phone %s", phone)

    if pending_mod:
        try:
            cal.delete_event(pending_mod["id"])
            logger.info("Old event deleted for modification: %s", pending_mod["id"])
        except Exception as e:
            logger.warning("Failed to delete old event during modification: %s", e)
        _delete_pending_modification(phone)

    if payments_enabled:
        await _handle_payment_flow(phone, intent, visible_response, service, location, slot, cal)
    else:
        cal.create_event(
            service_name=service["name"],
            user_name=intent.get("user_name", ""),
            user_phone=phone,
            slot=slot,
            duration_minutes=service["duration_minutes"],
            location_address=location["address"],
            price=service["price"],
            cancellation_policy=CONFIG["booking"].get("cancellation_policy", ""),
        )
        mod_note = " (previous appointment cancelled)" if pending_mod else ""
        confirmation = (
            f"{visible_response}\n\n"
            f"Appointment confirmed{mod_note}: {service['name']} on {booking_date.strftime('%m/%d/%Y')} "
            f"at {booking_time.strftime('%H:%M')} at {location['address']}.\n\n"
            f"Payment is due at the end of the appointment.\n\n"
            f"Cancellation policy: Appointments can be cancelled up to "
            f"24 hours in advance. After that period, a 40% fee will be charged."
        )
        HISTORY.add(phone, "assistant", confirmation)
        await WA.send_text(phone, confirmation)


async def _handle_payment_flow(phone, intent, visible_response, service, location, slot, cal):
    mp = _get_mp_client()
    if mp is None:
        return

    # Lock the slot for 30 minutes
    cal.lock_slot(slot.date, slot.start_time, ttl_seconds=1800)

    try:
        pref = mp.create_preference(
            title=service["name"],
            price=service["price"],
            external_reference=phone,
        )
    except Exception as e:
        logger.error("MP preference creation failed: %s", e)
        cal.release_slot(slot.date, slot.start_time)
        await WA.send_text(phone, "There was an error generating the payment link. Please try again.")
        return

    # Store pending payment keyed by phone (= external_reference used in MP preference)
    _save_pending_payment(phone, {
        "phone": phone,
        "service": service["name"],
        "date": slot.date.isoformat(),
        "time": slot.start_time.strftime("%H:%M"),
        "location": location["name"],
        "user_name": intent.get("user_name", ""),
        "price": service["price"],
        "duration_minutes": service["duration_minutes"],
        "location_address": location["address"],
    })

    msg = (
        f"{visible_response}\n\n"
        f"To confirm your appointment, complete the payment here:\n{pref['payment_url']}\n\n"
        f"Your appointment will be reserved once the payment is processed."
    )
    HISTORY.add(phone, "assistant", msg)
    await WA.send_text(phone, msg)


@app.post("/payments/webhook")
async def payment_webhook(request: Request):
    body = await request.body()
    x_signature = request.headers.get("X-Signature", "")

    # In sandbox mode, skip signature validation for easier testing
    sandbox = CONFIG.get("payments", {}).get("sandbox", True)
    if not sandbox and MP_WEBHOOK_SECRET:
        if not validate_mp_signature(body, x_signature, MP_WEBHOOK_SECRET):
            raise HTTPException(status_code=403, detail="Invalid MP signature")

    data = await request.json()

    if data.get("action") != "payment.updated":
        return Response(status_code=200)

    payment_id = str(data.get("data", {}).get("id", ""))
    if not payment_id:
        return Response(status_code=200)

    mp = _get_mp_client()
    if not mp:
        return Response(status_code=200)

    payment = mp.get_payment(payment_id)
    pending = _get_and_delete_pending_payment(payment)

    if not pending:
        return Response(status_code=200)

    phone = pending["phone"]

    if payment.get("status") == "approved":
        cal = _get_calendar_client()
        if cal:
            booking_date = date.fromisoformat(pending["date"])
            booking_time = dt_time.fromisoformat(pending["time"])
            slot = Slot(date=booking_date, start_time=booking_time, location=pending["location"])
            cal.create_event(
                service_name=pending["service"],
                user_name=pending["user_name"],
                user_phone=phone,
                slot=slot,
                duration_minutes=pending["duration_minutes"],
                location_address=pending["location_address"],
                price=pending["price"],
                cancellation_policy=CONFIG["booking"].get("cancellation_policy", ""),
            )
            cal.release_slot(booking_date, booking_time)
        confirmation = (
            f"Your payment has been confirmed. Appointment booked: {pending['service']} "
            f"on {pending['date']} at {pending['time']} at {pending['location_address']}. "
            f"See you there!"
        )
        await WA.send_text(phone, confirmation)
    else:
        cal = _get_calendar_client()
        if cal:
            cal.release_slot(
                date.fromisoformat(pending["date"]),
                dt_time.fromisoformat(pending["time"])
            )
        await WA.send_text(phone, "The payment was not processed. If you'd like to try again, message us.")

    return Response(status_code=200)


async def _handle_cancellation_request(phone: str, visible_response: str):
    """User wants to cancel — find their upcoming events."""
    cal = _get_calendar_client()
    if cal is None:
        await WA.send_text(phone, "The booking module is not available.")
        return

    events = cal.find_upcoming_events_by_phone(phone)

    if not events:
        msg = "I couldn't find any appointments booked with your number. Is there anything else I can help with?"
        HISTORY.add(phone, "assistant", msg)
        await WA.send_text(phone, msg)
        return

    _save_pending_cancellation(phone, events)

    if len(events) == 1:
        e = events[0]
        msg = (
            f"{visible_response}\n\n"
            f"I found this appointment:\n"
            f"*{e['summary']}*\n"
            f"Date: {e['date']}\n"
            f"Time: {e['time']}\n"
            f"Location: {e['location']}\n\n"
            f"Do you confirm you want to cancel it?"
        )
    else:
        lines = [f"{visible_response}\n\nI found these appointments:\n"]
        for i, e in enumerate(events, 1):
            lines.append(
                f"{i}. *{e['summary']}* — {e['date']} at {e['time']} at {e['location']}"
            )
        lines.append("\nWhich one would you like to cancel?")
        msg = "\n".join(lines)

    HISTORY.add(phone, "assistant", msg)
    await WA.send_text(phone, msg)


async def _handle_cancellation_confirmed(phone: str, intent: dict, visible_response: str):
    """User confirmed cancellation — delete the event."""
    cal = _get_calendar_client()
    if cal is None:
        await WA.send_text(phone, "The booking module is not available.")
        return

    events = _get_pending_cancellation(phone)
    if not events:
        msg = "I couldn't find an active cancellation request. Would you like to cancel an appointment? Let me know and I'll look up your bookings."
        HISTORY.add(phone, "assistant", msg)
        await WA.send_text(phone, msg)
        return

    event_index = intent.get("event_index", 1)
    try:
        event_index = int(event_index)
    except (TypeError, ValueError):
        event_index = 1

    if event_index < 1 or event_index > len(events):
        msg = f"Invalid number. Please choose a number from 1 to {len(events)}."
        HISTORY.add(phone, "assistant", msg)
        await WA.send_text(phone, msg)
        return

    event = events[event_index - 1]

    try:
        cal.delete_event(event["id"])
        _delete_pending_cancellation(phone)
        msg = (
            f"Appointment cancelled: *{event['summary']}* on {event['date']} at {event['time']}.\n\n"
            f"If you'd like to book another appointment, just message us."
        )
        logger.info("Event cancelled: %s for phone %s", event["id"], phone)
    except Exception as e:
        logger.error("Failed to cancel event: %s", e)
        msg = "There was a problem cancelling the appointment. Please contact us directly."

    HISTORY.add(phone, "assistant", msg)
    await WA.send_text(phone, msg)


async def _handle_modification_request(phone: str, visible_response: str):
    """User wants to modify — show their events and ask for new date.
    The old event is NOT deleted yet. It gets deleted when the new booking is confirmed."""
    cal = _get_calendar_client()
    if cal is None:
        await WA.send_text(phone, "The booking module is not available.")
        return

    events = cal.find_upcoming_events_by_phone(phone)

    if not events:
        msg = "I couldn't find any appointments booked with your number. Would you like to book a new one?"
        HISTORY.add(phone, "assistant", msg)
        await WA.send_text(phone, msg)
        return

    if len(events) == 1:
        e = events[0]
        # Store the event to delete later when the new booking is confirmed
        _save_pending_modification(phone, e)
        msg = (
            f"{visible_response}\n\n"
            f"I found your appointment:\n"
            f"*{e['summary']}*\n"
            f"Date: {e['date']}\n"
            f"Time: {e['time']}\n"
            f"Location: {e['location']}\n\n"
            f"What date and time would you like to change it to?"
        )
    else:
        _save_pending_cancellation(phone, events)
        lines = [f"{visible_response}\n\nI found these appointments:\n"]
        for i, e in enumerate(events, 1):
            lines.append(
                f"{i}. *{e['summary']}* — {e['date']} at {e['time']} at {e['location']}"
            )
        lines.append("\nWhich one would you like to modify?")
        msg = "\n".join(lines)

    HISTORY.add(phone, "assistant", msg)
    await WA.send_text(phone, msg)


async def _handle_modification_confirmed(phone: str, intent: dict, visible_response: str):
    """User chose which event to modify (multiple events case)."""
    cal = _get_calendar_client()
    if cal is None:
        await WA.send_text(phone, "The booking module is not available.")
        return

    events = _get_pending_cancellation(phone)
    if not events:
        msg = "I couldn't find an active modification request. Let me know which appointment you'd like to change."
        HISTORY.add(phone, "assistant", msg)
        await WA.send_text(phone, msg)
        return

    event_index = intent.get("event_index", 1)
    try:
        event_index = int(event_index)
    except (TypeError, ValueError):
        event_index = 1

    if event_index < 1 or event_index > len(events):
        msg = f"Invalid number. Please choose a number from 1 to {len(events)}."
        HISTORY.add(phone, "assistant", msg)
        await WA.send_text(phone, msg)
        return

    event = events[event_index - 1]
    _delete_pending_cancellation(phone)
    # Store event for deletion when new booking is confirmed
    _save_pending_modification(phone, event)

    msg = (
        f"Sure, let's modify *{event['summary']}* on {event['date']} at {event['time']}.\n\n"
        f"What date and time would you like to change it to?"
    )
    HISTORY.add(phone, "assistant", msg)
    await WA.send_text(phone, msg)


@app.post("/internal/send-reminders")
async def trigger_reminders(request: Request):
    secret = request.headers.get("X-Internal-Secret", "")
    if secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403)

    cal = _get_calendar_client()
    if cal is None:
        return {"sent": 0, "error": "booking module disabled"}

    sent = await send_reminders(CONFIG, cal._service, WA)
    return {"sent": sent}
