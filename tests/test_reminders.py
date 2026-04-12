import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from reminders.scheduler import (
    extract_phone_from_description,
    format_reminder_message,
    send_reminders,
)


def make_mock_event(summary, description, start_datetime, location="456 Oak Avenue, Springfield"):
    return {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_datetime},
    }


def test_extract_phone_from_description():
    desc = "Service: Dental Cleaning\nPrice: $150\nPhone: 5491112345678\nPolicy: 24 hours"
    assert extract_phone_from_description(desc) == "5491112345678"


def test_extract_phone_missing():
    assert extract_phone_from_description("No phone here") is None


def test_format_reminder_message():
    template = "Reminder: {service} tomorrow at {time} at {location}."
    event = make_mock_event(
        "Dental Cleaning - Jane",
        "Phone: 5491112345678",
        "2026-03-17T10:00:00-03:00",
    )
    msg = format_reminder_message(template, event)
    assert "10:00" in msg
    assert "Dental Cleaning" in msg


@pytest.mark.asyncio
async def test_send_reminders_sends_message_for_each_event():
    config = {
        "client": {"timezone": "America/Argentina/Buenos_Aires"},
        "booking": {"calendar_id": "test@calendar"},
        "reminders": {"message_template": "Reminder: {service} tomorrow at {time} at {location}."},
    }

    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {
        "items": [
            make_mock_event(
                "Dental Cleaning - Jane",
                "Phone: 5491112345678",
                "2026-03-18T10:00:00-03:00",
            )
        ]
    }

    mock_wa = MagicMock()
    mock_wa.send_text = AsyncMock()

    count = await send_reminders(config, mock_service, mock_wa)
    assert count == 1
    mock_wa.send_text.assert_called_once()
    call_phone = mock_wa.send_text.call_args[0][0]
    assert call_phone == "5491112345678"


@pytest.mark.asyncio
async def test_send_reminders_skips_event_without_phone():
    config = {
        "client": {"timezone": "America/Argentina/Buenos_Aires"},
        "booking": {"calendar_id": "test@calendar"},
        "reminders": {"message_template": "Reminder: {service} at {time} at {location}."},
    }

    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {
        "items": [
            make_mock_event("Dental Cleaning - Jane", "No phone here", "2026-03-18T10:00:00-03:00")
        ]
    }

    mock_wa = MagicMock()
    mock_wa.send_text = AsyncMock()

    count = await send_reminders(config, mock_service, mock_wa)
    assert count == 0
    mock_wa.send_text.assert_not_called()
