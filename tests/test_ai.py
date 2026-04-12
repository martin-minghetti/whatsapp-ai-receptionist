import json
import pytest
from core.ai import extract_booking_intent, build_system_prompt


def test_extract_booking_intent_present():
    response = 'Perfecto, te confirmo el turno. {"intent": "booking_confirmed", "service": "Plan Nutricional", "date": "2026-03-17", "time": "10:00", "location": "Consultorio Centro", "user_name": "Ana"}'
    intent, visible = extract_booking_intent(response)
    assert intent is not None
    assert intent["intent"] == "booking_confirmed"
    assert intent["service"] == "Plan Nutricional"
    assert "intent" not in visible
    assert "Perfecto" in visible


def test_extract_booking_intent_absent():
    response = "Hola, ¿en qué te puedo ayudar?"
    intent, visible = extract_booking_intent(response)
    assert intent is None
    assert visible == response


def test_build_system_prompt_core_only():
    config = {
        "client": {"name": "Test Nutricionista"},
        "modules": {"booking": False, "payments": False},
    }
    knowledge = "Servicios: Plan nutricional $40.000"
    prompt = build_system_prompt(config, knowledge)
    assert "Test Nutricionista" in prompt
    assert "Servicios: Plan nutricional" in prompt
    assert "booking_confirmed" not in prompt  # booking disabled


def test_build_system_prompt_with_booking():
    config = {
        "client": {"name": "Test"},
        "modules": {"booking": True, "payments": False},
        "booking": {
            "services": [{"name": "Plan", "price": 40000, "duration_minutes": 45}],
            "locations": [{"name": "Consultorio Centro", "address": "Av. San Martín 123", "days": ["monday"]}],
            "business_hours": {"start": "09:00", "end": "18:00"},
            "cancellation_policy": "24hs antes",
        }
    }
    prompt = build_system_prompt(config, "knowledge")
    assert "booking_confirmed" in prompt
    assert "Plan" in prompt
