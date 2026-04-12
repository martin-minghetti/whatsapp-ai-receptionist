# tests/test_calendar.py
import pytest
from datetime import date, time
from unittest.mock import MagicMock, patch
from modules.booking.calendar import CalendarClient, Slot


def make_client():
    # Patch both _get_credentials (avoids needing GOOGLE_SERVICE_ACCOUNT_JSON env var)
    # and build (avoids real HTTP calls to Google API).
    with patch("modules.booking.calendar._get_credentials") as mock_creds, \
         patch("modules.booking.calendar.build") as mock_build:
        mock_creds.return_value = MagicMock()
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        client = CalendarClient(
            calendar_id="test@calendar",
            calendar_owner_email="owner@gmail.com",
            business_hours={"start": "09:00", "end": "18:00"},
        )
        client._service = mock_service
        return client, mock_service


def test_slot_dataclass():
    s = Slot(date=date(2026, 3, 16), start_time=time(10, 0), location="Consultorio Centro")
    assert s.date == date(2026, 3, 16)


def test_is_slot_available_free(monkeypatch):
    client, mock_service = make_client()
    mock_service.freebusy().query().execute.return_value = {
        "calendars": {"test@calendar": {"busy": []}}
    }
    monkeypatch.setattr("modules.booking.calendar._get_redis", lambda: None)
    result = client.is_slot_available(date(2026, 3, 16), time(10, 0), 45)
    assert result is True


def test_is_slot_available_busy(monkeypatch):
    client, mock_service = make_client()
    mock_service.freebusy().query().execute.return_value = {
        "calendars": {"test@calendar": {"busy": [
            {"start": "2026-03-16T10:00:00-03:00", "end": "2026-03-16T10:45:00-03:00"}
        ]}}
    }
    monkeypatch.setattr("modules.booking.calendar._get_redis", lambda: None)
    result = client.is_slot_available(date(2026, 3, 16), time(10, 0), 45)
    assert result is False


def test_create_event(monkeypatch):
    client, mock_service = make_client()
    mock_service.events().insert().execute.return_value = {"id": "event123"}
    event_id = client.create_event(
        service_name="Plan Nutricional",
        user_name="Ana García",
        user_phone="5491112345678",
        slot=Slot(date=date(2026, 3, 16), start_time=time(10, 0), location="Consultorio Centro"),
        duration_minutes=45,
        location_address="Av. San Martín 123, Bariloche",
        price=40000,
        cancellation_policy="24hs antes",
    )
    assert event_id == "event123"
    call_args = mock_service.events().insert.call_args
    event_body = call_args[1]["body"]
    assert "Plan Nutricional" in event_body["summary"]
    assert "5491112345678" in event_body["description"]
