import json
import logging
import os
import re
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def load_knowledge(path: str = "knowledge/client.txt") -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Knowledge file not found: %s", path)
        return ""


def build_system_prompt(config: dict, knowledge: str) -> str:
    client_name = config["client"]["name"]
    modules = config.get("modules", {})

    lines = [
        f"You are the virtual assistant for {client_name}.",
        "Speak in the third person about the professional (do not impersonate them).",
        "Answer using the information from the knowledge base.",
        "Respond in the user's language.",
        "Be concise (maximum 3-4 paragraphs). Do not use emojis.",
        "FORMAT: Use WhatsApp formatting, NOT markdown. Bold with *single asterisks* (not **double**). Italics with _underscores_. Do not use # or other markdown formatting.",
        "Do not make up information that is not in the knowledge base.",
        "",
        "KNOWLEDGE BASE:",
        knowledge,
    ]

    if modules.get("booking"):
        booking = config.get("booking", {})
        services_text = "\n".join(
            f"- {s['name']}: ${s['price']:,} ({s['duration_minutes']} min)"
            for s in booking.get("services", [])
        )
        locations_text = "\n".join(
            f"- {loc['name']}: {loc['address']} ({', '.join(loc['days'])})"
            for loc in booking.get("locations", [])
        )
        hours = booking.get("business_hours", {})
        from datetime import date as date_cls, timedelta
        today = date_cls.today()
        day_names_en = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
                        4: "Friday", 5: "Saturday", 6: "Sunday"}
        today_str = f"{day_names_en[today.weekday()]} {today.isoformat()}"

        # Calculate next available dates based on booking days
        day_name_to_num = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                           "friday": 4, "saturday": 5, "sunday": 6}
        booking_days = set()
        for loc in booking.get("locations", []):
            for d in loc.get("days", []):
                booking_days.add(day_name_to_num.get(d.lower(), -1))
        next_dates = []
        check = today + timedelta(days=1)
        while len(next_dates) < 5:
            if check.weekday() in booking_days:
                next_dates.append(f"{day_names_en[check.weekday()]} {check.day}/{check.month} ({check.isoformat()})")
            check += timedelta(days=1)
        next_dates_str = ", ".join(next_dates)

        lines += [
            "",
            "BOOKINGS:",
            f"Today is {today_str}.",
            f"Next available dates for appointments: {next_dates_str}.",
            "Use ONLY these dates when proposing appointments. Do not make up other dates.",
            "When the user wants to book an appointment, you need:",
            "1. Type of service (from the services list)",
            "2. Date and time",
            "3. Full name",
            "",
            "IMPORTANT about the booking experience:",
            "- 'Tomorrow' means the day after today. 'Day after tomorrow' is two days later. ALWAYS resolve these expressions to the actual date.",
            "- If the user says something vague like 'tomorrow', 'Wednesday' or 'next week', YOU resolve the actual date and propose a time.",
            "  Example: if today is Tuesday 03/31, 'tomorrow' = Wednesday 04/01. Reply: 'Tomorrow Wednesday 04/01 at 10:00. Does that work for you?'",
            "- NEVER ask for multiple pieces of information at once. Ask ONE thing per message. If you already have the date, ask for the service. If you already have the service, ask for the name. Never numbered lists with multiple questions.",
            "- Try to complete the booking in as few messages as possible.",
            f"Available hours: {hours.get('start', '09:00')} to {hours.get('end', '18:00')}",
            f"Available services:\n{services_text}",
            f"Locations:\n{locations_text}",
            f"Cancellation policy: {booking.get('cancellation_policy', '')}",
            "",
            "Once you have all the data confirmed by the user, respond with a confirmation message",
            "AND at the end include this JSON on a single line (NO markdown, NO ```json, NO backticks):",
            '{"intent": "booking_confirmed", "service": "<EXACT service name from the list>", '
            '"date": "<YYYY-MM-DD>", "time": "<HH:MM>", "location": "<location name>", '
            '"user_name": "<full name>"}',
            "IMPORTANT: The 'service' field in the JSON MUST be the exact name from the services list. "
            "For example, if the user asks for the bundle, use 'Cleaning + Checkup (bundle)', NOT 'Dental Cleaning'.",
            "IMPORTANT: Do not mention payment, the system handles it automatically.",
            "IMPORTANT: The JSON goes in plain text at the end of the message, never inside code blocks.",
            "",
            "CANCELLATIONS:",
            "When the user wants to cancel an appointment, respond politely and at the end include:",
            '{"intent": "cancellation_request"}',
            "Do not ask for name or other details. The system searches for appointments automatically by phone number.",
            "If the system shows the appointments and the user confirms which one to cancel, respond with:",
            '{"intent": "cancellation_confirmed", "event_index": <appointment number>}',
            "If the user says 'yes' and there is only one appointment, use event_index: 1.",
            "",
            "MODIFICATIONS:",
            "When the user wants to change, modify, move or reschedule an appointment, ALWAYS respond with:",
            '{"intent": "modification_request"}',
            "IMPORTANT: ALWAYS emit modification_request first, even if the user already provides the new date in the same message. "
            "The system needs to look up the current appointment before proceeding. Do not try to handle the modification conversationally.",
            "Do not ask for name or extra details. The system searches by phone and shows the appointments.",
            "After the system shows the appointment, the user will tell you the new date/time.",
            "IMPORTANT: When the user confirms the new date/time for their modified appointment, emit a booking_confirmed JSON (NOT modification_confirmed). "
            "Use the same service as the original appointment. Example:",
            '{"intent": "booking_confirmed", "service": "Dental Cleaning", "date": "2026-04-03", "time": "15:00", "location": "Downtown Office", "user_name": "John Smith"}',
            "modification_confirmed is ONLY used when there are MULTIPLE appointments and the user chooses which one to modify:",
            '{"intent": "modification_confirmed", "event_index": <appointment number>}',
        ]

    return "\n".join(lines)


def extract_intent(response: str) -> tuple[dict | None, str]:
    """
    Look for any intent JSON block in Claude's response.
    Returns (intent_dict, visible_text) — strips the JSON from user-visible text.
    """
    pattern = re.compile(r'\{[^{}]*"intent"\s*:\s*"[^"]*"[^{}]*\}', re.DOTALL)
    match = pattern.search(response)
    if not match:
        return None, response
    try:
        intent = json.loads(match.group())
    except json.JSONDecodeError:
        return None, response
    visible = response[:match.start()].strip()
    # Remove any trailing markdown code fences that Claude might add
    visible = re.sub(r'```\s*json\s*$', '', visible, flags=re.MULTILINE).strip()
    visible = re.sub(r'```\s*$', '', visible, flags=re.MULTILINE).strip()
    return intent, visible


# Keep backward compatibility
extract_booking_intent = extract_intent


def get_ai_response(
    user_message: str,
    history: list[dict],
    config: dict,
    knowledge: str,
) -> str:
    system_prompt = build_system_prompt(config, knowledge)
    messages = history + [{"role": "user", "content": user_message}]
    resp = get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=messages,
    )
    return resp.content[0].text
