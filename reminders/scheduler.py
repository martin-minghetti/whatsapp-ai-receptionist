import logging
import re
from datetime import datetime, timedelta

import pytz

logger = logging.getLogger(__name__)


def extract_phone_from_description(description: str) -> str | None:
    match = re.search(r'Phone:\s*(\d+)', description or "")
    return match.group(1) if match else None


def format_reminder_message(template: str, event: dict) -> str:
    start_raw = event["start"].get("dateTime", "")
    try:
        dt = datetime.fromisoformat(start_raw)
        time_str = dt.strftime("%H:%M")
    except ValueError:
        time_str = start_raw

    summary = event.get("summary", "")
    service_name = summary.split(" - ")[0] if " - " in summary else summary
    location = event.get("location", "the agreed location")

    return template.format(time=time_str, location=location, service=service_name)


async def send_reminders(config: dict, calendar_service, wa_client) -> int:
    """
    Query Calendar for tomorrow's events and send WhatsApp reminders.
    Returns number of reminders sent.
    """
    tz = pytz.timezone(config["client"].get("timezone", "America/Argentina/Buenos_Aires"))
    now = datetime.now(tz)
    tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_end = tomorrow_start.replace(hour=23, minute=59, second=59)

    calendar_id = config["booking"]["calendar_id"]
    events_result = calendar_service.events().list(
        calendarId=calendar_id,
        timeMin=tomorrow_start.isoformat(),
        timeMax=tomorrow_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = events_result.get("items", [])
    template = config["reminders"]["message_template"]
    sent = 0

    for event in events:
        description = event.get("description", "")
        phone = extract_phone_from_description(description)
        if not phone:
            logger.warning("No phone in event: %s", event.get("summary"))
            continue
        msg = format_reminder_message(template, event)
        await wa_client.send_text(phone, msg)
        sent += 1
        logger.info("Reminder sent to %s for event %s", phone, event.get("summary"))

    return sent
