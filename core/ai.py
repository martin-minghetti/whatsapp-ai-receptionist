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
        f"Sos el asistente virtual de {client_name}.",
        "Hablás en tercera persona sobre el profesional (no te hacés pasar por él/ella).",
        "Respondé usando la información de la base de conocimiento.",
        "Respondé en el idioma del usuario.",
        "Sé conciso (máximo 3-4 párrafos). No uses emojis.",
        "FORMATO: Usá formato WhatsApp, NO markdown. Negritas con *un solo asterisco* (no **doble**). Itálica con _guión bajo_. No uses # ni otros formatos markdown.",
        "No inventes información que no esté en la base de conocimiento.",
        "",
        "BASE DE CONOCIMIENTO:",
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
        day_names_es = {0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves",
                        4: "viernes", 5: "sábado", 6: "domingo"}
        today_str = f"{day_names_es[today.weekday()]} {today.isoformat()}"

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
                next_dates.append(f"{day_names_es[check.weekday()]} {check.day}/{check.month} ({check.isoformat()})")
            check += timedelta(days=1)
        next_dates_str = ", ".join(next_dates)

        lines += [
            "",
            "RESERVAS:",
            f"Hoy es {today_str}.",
            f"Próximas fechas disponibles para turnos: {next_dates_str}.",
            "Usá SOLAMENTE estas fechas al proponer turnos. No inventes otras.",
            "Cuando el usuario quiera reservar un turno, necesitás:",
            "1. Tipo de consulta (de la lista de servicios)",
            "2. Fecha y horario",
            "3. Nombre completo",
            "",
            "IMPORTANTE sobre la experiencia de reserva:",
            "- 'Mañana' significa el día siguiente a hoy. 'Pasado mañana' es dos días después. Resolvé SIEMPRE estas expresiones a la fecha concreta.",
            "- Si el usuario dice algo vago como 'mañana', 'el miércoles' o 'la semana que viene', VOS resolvé la fecha concreta y proponé un horario.",
            "  Ejemplo: si hoy es martes 31/03, 'mañana' = miércoles 1/04. Respondé: 'Mañana miércoles 1/04 a las 10:00. ¿Te va bien?'",
            "- NUNCA pidas varios datos a la vez. Preguntá UNA sola cosa por mensaje. Si ya tenés la fecha, preguntá el servicio. Si ya tenés el servicio, preguntá el nombre. Nunca listas numeradas con múltiples preguntas.",
            "- Intentá resolver la reserva en la menor cantidad de mensajes posible.",
            f"Horario disponible: {hours.get('start', '09:00')} a {hours.get('end', '18:00')}",
            f"Servicios disponibles:\n{services_text}",
            f"Lugares:\n{locations_text}",
            f"Política de cancelación: {booking.get('cancellation_policy', '')}",
            "",
            "Una vez que tengas todos los datos confirmados por el usuario, respondé con un mensaje",
            "de confirmación Y al final incluí este JSON en una sola línea (SIN markdown, SIN ```json, SIN backticks):",
            '{"intent": "booking_confirmed", "service": "<nombre EXACTO del servicio de la lista>", '
            '"date": "<YYYY-MM-DD>", "time": "<HH:MM>", "location": "<nombre del lugar>", '
            '"user_name": "<nombre completo>"}',
            "IMPORTANTE: El campo 'service' en el JSON DEBE ser el nombre exacto de la lista de servicios. "
            "Por ejemplo, si el usuario pide el combo, usá 'Plan + Antropometría (combo)', NO 'Plan Nutricional'.",
            "IMPORTANTE: No menciones el pago, el sistema lo gestiona automáticamente.",
            "IMPORTANTE: El JSON va en texto plano al final del mensaje, nunca dentro de bloques de código.",
            "",
            "CANCELACIONES:",
            "Cuando el usuario quiera cancelar un turno, respondé amablemente y al final incluí:",
            '{"intent": "cancellation_request"}',
            "No pidas nombre ni datos. El sistema busca los turnos automáticamente por número de teléfono.",
            "Si el sistema te muestra los turnos y el usuario confirma cuál cancelar, respondé con:",
            '{"intent": "cancellation_confirmed", "event_index": <número del turno>}',
            "Si el usuario dice 'sí' y solo hay un turno, usá event_index: 1.",
            "",
            "MODIFICACIONES:",
            "Cuando el usuario quiera cambiar, modificar, mover o reprogramar un turno, SIEMPRE respondé con:",
            '{"intent": "modification_request"}',
            "IMPORTANTE: SIEMPRE emití modification_request primero, incluso si el usuario ya dice la nueva fecha en el mismo mensaje. "
            "El sistema necesita buscar el turno actual antes de proceder. No intentes manejar la modificación conversacionalmente.",
            "No pidas nombre ni datos extra. El sistema busca por teléfono y muestra los turnos.",
            "Después de que el sistema muestre el turno, el usuario te dirá la nueva fecha/horario.",
            "IMPORTANTE: Cuando el usuario confirme la nueva fecha/horario para su turno modificado, emití un JSON de booking_confirmed (NO modification_confirmed). "
            "Usá el mismo servicio que tenía el turno original. Ejemplo:",
            '{"intent": "booking_confirmed", "service": "Consulta Nutricional", "date": "2026-04-03", "time": "15:00", "location": "Consultorio Centro", "user_name": "Juan Pérez"}',
            "modification_confirmed SOLO se usa cuando hay VARIOS turnos y el usuario elige cuál modificar:",
            '{"intent": "modification_confirmed", "event_index": <número del turno>}',
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
