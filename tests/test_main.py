import os
import json
import hashlib
import hmac
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "123")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "mytoken")
os.environ.setdefault("WHATSAPP_APP_SECRET", "appsecret")
os.environ.setdefault("INTERNAL_SECRET", "internalsecret")
os.environ.setdefault("GOOGLE_CALENDAR_ID", "test@calendar")
os.environ.setdefault("GOOGLE_CALENDAR_OWNER_EMAIL", "test@test.com")

from core.main import app

client = TestClient(app)


def test_health_check():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_webhook_verification():
    resp = client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "mytoken",
        "hub.challenge": "12345",
    })
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_webhook_verification_wrong_token():
    resp = client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "wrong",
        "hub.challenge": "12345",
    })
    assert resp.status_code == 403


def test_webhook_invalid_signature():
    resp = client.post(
        "/webhook",
        content=b'{"test": "data"}',
        headers={"X-Hub-Signature-256": "sha256=invalid", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


def test_webhook_status_update_ignored():
    body = json.dumps({
        "entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]
    }).encode()
    sig = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


def test_booking_intent_creates_event_without_payments(mocker):
    """When payments are disabled, booking intent creates calendar event immediately."""
    mocker.patch("core.main.CONFIG", {
        "client": {"name": "Test", "timezone": "America/Argentina/Buenos_Aires"},
        "modules": {"booking": True, "payments": False, "reminders": False},
        "booking": {
            "calendar_id": "test",
            "calendar_owner_email": "test@test.com",
            "business_hours": {"start": "09:00", "end": "18:00"},
            "services": [{"name": "Dental Cleaning", "price": 150, "duration_minutes": 45}],
            "locations": [{"name": "Downtown Office", "address": "456 Oak Avenue, Springfield", "days": ["monday"]}],
            "cancellation_policy": "24 hours in advance",
        }
    })
    mock_calendar = mocker.MagicMock()
    mock_calendar.is_slot_available.return_value = True
    mocker.patch("core.main._get_calendar_client", return_value=mock_calendar)
    mock_send = mocker.patch("core.main.WA.send_text", new_callable=mocker.AsyncMock)
    mocker.patch("core.main.get_ai_response", return_value=(
        'Your appointment is confirmed. {"intent": "booking_confirmed", "service": "Dental Cleaning", '
        '"date": "2027-03-16", "time": "10:00", "location": "Downtown Office", "user_name": "Jane"}'
    ))

    import json
    import hashlib
    import hmac
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"from": "5491112345678", "type": "text", "text": {"body": "I want to book"}}
    ]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()

    from fastapi.testclient import TestClient
    from core.main import app
    test_client = TestClient(app)
    resp = test_client.post("/webhook", content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})

    assert resp.status_code == 200
    mock_calendar.create_event.assert_called_once()
    assert mock_send.called
