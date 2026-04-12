# modules/booking/calendar.py
import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pytz
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


@dataclass
class Slot:
    date: date
    start_time: time
    location: str


def _get_credentials() -> Credentials:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    service_account_info = json.loads(base64.b64decode(raw))
    return Credentials.from_service_account_info(service_account_info, scopes=SCOPES)


def _get_redis():
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis
        r = redis.from_url(redis_url)
        r.ping()
        return r
    except Exception:
        return None


class CalendarClient:
    def __init__(self, calendar_id: str, calendar_owner_email: str,
                 business_hours: dict, timezone: str = "America/Argentina/Buenos_Aires"):
        self._calendar_id = calendar_id
        self._owner_email = calendar_owner_email
        self._business_hours = business_hours
        self._tz = pytz.timezone(timezone)
        creds = _get_credentials()
        self._service = build("calendar", "v3", credentials=creds)

    def _slot_key(self, d: date, t: time) -> str:
        return f"slot_lock:{d.isoformat()}:{t.strftime('%H:%M')}"

    def is_slot_available(self, d: date, start_time: time, duration_minutes: int) -> bool:
        """Check Google Calendar + Redis locks."""
        r = _get_redis()
        if r and r.exists(self._slot_key(d, start_time)):
            return False

        start_dt = self._tz.localize(datetime.combine(d, start_time))
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        body = {
            "timeMin": start_dt.isoformat(),
            "timeMax": end_dt.isoformat(),
            "items": [{"id": self._calendar_id}],
        }
        result = self._service.freebusy().query(body=body).execute()
        busy = result["calendars"][self._calendar_id]["busy"]
        return len(busy) == 0

    def lock_slot(self, d: date, start_time: time, ttl_seconds: int = 1800) -> None:
        """Soft-lock a slot in Redis for TTL seconds (default 30 min)."""
        r = _get_redis()
        if r:
            r.setex(self._slot_key(d, start_time), ttl_seconds, "1")

    def release_slot(self, d: date, start_time: time) -> None:
        r = _get_redis()
        if r:
            r.delete(self._slot_key(d, start_time))

    def create_event(
        self, service_name: str, user_name: str, user_phone: str,
        slot: Slot, duration_minutes: int, location_address: str,
        price: int, cancellation_policy: str,
    ) -> str:
        start_dt = self._tz.localize(datetime.combine(slot.date, slot.start_time))
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        description = (
            f"Servicio: {service_name}\n"
            f"Valor: ${price:,}\n"
            f"Teléfono: {user_phone}"
        )

        event = {
            "summary": f"{service_name} - {user_name}",
            "location": location_address,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": str(self._tz)},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": str(self._tz)},
            "reminders": {"useDefault": False, "overrides": []},
        }

        result = self._service.events().insert(
            calendarId=self._calendar_id,
            body=event,
        ).execute()
        return result["id"]

    def find_upcoming_events_by_phone(self, phone: str) -> list[dict]:
        """Find future events that contain this phone number in the description."""
        now = datetime.now(self._tz).isoformat()
        result = self._service.events().list(
            calendarId=self._calendar_id,
            timeMin=now,
            maxResults=10,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        matching = []
        for event in result.get("items", []):
            desc = event.get("description", "")
            if phone in desc:
                start = event["start"].get("dateTime", "")
                # Format date as human-readable Spanish
                date_str = start[:10] if start else ""
                if date_str:
                    from datetime import date as date_cls
                    d = date_cls.fromisoformat(date_str)
                    day_names = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
                                 4: "Viernes", 5: "Sábado", 6: "Domingo"}
                    month_names = {1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
                                   5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
                                   9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"}
                    date_str = f"{day_names[d.weekday()]} {d.day} de {month_names[d.month]}, {d.year}"
                matching.append({
                    "id": event["id"],
                    "summary": event.get("summary", ""),
                    "date": date_str,
                    "time": start[11:16] if start else "",
                    "location": event.get("location", ""),
                })
        return matching

    def delete_event(self, event_id: str) -> None:
        """Delete an event from the calendar."""
        self._service.events().delete(
            calendarId=self._calendar_id,
            eventId=event_id,
        ).execute()
