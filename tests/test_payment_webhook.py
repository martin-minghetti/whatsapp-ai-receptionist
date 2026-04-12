import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "123")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "mytoken")
os.environ.setdefault("WHATSAPP_APP_SECRET", "appsecret")
os.environ.setdefault("INTERNAL_SECRET", "internalsecret")
os.environ.setdefault("MP_ACCESS_TOKEN", "TEST-token")
os.environ.setdefault("MP_WEBHOOK_SECRET", "mpwebhooksecret")
os.environ.setdefault("GOOGLE_CALENDAR_ID", "test@calendar")
os.environ.setdefault("GOOGLE_CALENDAR_OWNER_EMAIL", "test@test.com")

from fastapi.testclient import TestClient
from core.main import app

client = TestClient(app)


def test_payment_webhook_approved(mocker):
    """When payment is approved, calendar event is created and user is notified."""
    pending = {
        "phone": "5491112345678",
        "service": "Plan Nutricional",
        "date": "2026-03-16",
        "time": "10:00",
        "location": "Consultorio Centro",
        "user_name": "Ana García",
        "price": 40000,
        "duration_minutes": 45,
        "location_address": "Av. San Martín 123, Bariloche",
    }
    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(pending)
    mocker.patch("core.main._get_pending_payment_redis", return_value=mock_redis)

    mock_mp = MagicMock()
    mock_mp.get_payment.return_value = {
        "status": "approved",
        "id": "pay_123",
        "external_reference": "5491112345678",
    }
    mocker.patch("core.main._get_mp_client", return_value=mock_mp)

    mock_calendar = MagicMock()
    mocker.patch("core.main._get_calendar_client", return_value=mock_calendar)

    mock_send = mocker.patch("core.main.WA.send_text", new_callable=AsyncMock)

    body = json.dumps({"action": "payment.updated", "data": {"id": "pay_123"}}).encode()
    resp = client.post("/payments/webhook", content=body,
        headers={"X-Signature": "ts=123,v1=skip", "Content-Type": "application/json"})

    assert resp.status_code == 200
    mock_calendar.create_event.assert_called_once()
    mock_send.assert_called_once()
    assert "confirmado" in mock_send.call_args[0][1].lower()


def test_payment_webhook_rejected(mocker):
    """When payment is rejected, user is notified and slot is released."""
    pending = {
        "phone": "5491112345678",
        "service": "Plan Nutricional",
        "date": "2026-03-16",
        "time": "10:00",
        "location": "Consultorio Centro",
        "user_name": "Ana García",
        "price": 40000,
        "duration_minutes": 45,
        "location_address": "Av. San Martín 123, Bariloche",
    }
    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(pending)
    mocker.patch("core.main._get_pending_payment_redis", return_value=mock_redis)

    mock_mp = MagicMock()
    mock_mp.get_payment.return_value = {
        "status": "rejected",
        "id": "pay_456",
        "external_reference": "5491112345678",
    }
    mocker.patch("core.main._get_mp_client", return_value=mock_mp)

    mock_calendar = MagicMock()
    mocker.patch("core.main._get_calendar_client", return_value=mock_calendar)
    mock_send = mocker.patch("core.main.WA.send_text", new_callable=AsyncMock)

    body = json.dumps({"action": "payment.updated", "data": {"id": "pay_456"}}).encode()
    resp = client.post("/payments/webhook", content=body,
        headers={"X-Signature": "ts=123,v1=skip", "Content-Type": "application/json"})

    assert resp.status_code == 200
    mock_calendar.create_event.assert_not_called()
    mock_calendar.release_slot.assert_called_once()
    mock_send.assert_called_once()


def test_payment_webhook_unknown_action(mocker):
    """Non-payment actions are ignored."""
    body = json.dumps({"action": "merchant_order.updated", "data": {"id": "123"}}).encode()
    resp = client.post("/payments/webhook", content=body,
        headers={"X-Signature": "ts=123,v1=skip", "Content-Type": "application/json"})
    assert resp.status_code == 200
