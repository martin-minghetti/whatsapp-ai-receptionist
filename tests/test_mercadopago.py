# tests/test_mercadopago.py
import os
import pytest
from unittest.mock import patch, MagicMock
from modules.payments.mercadopago import MPClient, validate_mp_signature


def test_validate_mp_signature_invalid():
    result = validate_mp_signature(b"body", "v1=badsig,ts=123", "secret")
    assert result is False


def test_validate_mp_signature_empty():
    result = validate_mp_signature(b"body", "", "secret")
    assert result is False


def test_create_preference(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    with patch("modules.payments.mercadopago.mercadopago.SDK") as mock_sdk:
        mock_instance = MagicMock()
        mock_sdk.return_value = mock_instance
        mock_instance.preference().create.return_value = {
            "status": 201,
            "response": {
                "id": "pref_123",
                "sandbox_init_point": "https://sandbox.mercadopago.com/pay/pref_123",
                "init_point": "https://mercadopago.com/pay/pref_123",
            }
        }
        client = MPClient(sandbox=True)
        result = client.create_preference(
            title="Plan Nutricional",
            price=40000,
            external_reference="5491112345678",
        )
        assert result["preference_id"] == "pref_123"
        assert "sandbox.mercadopago.com" in result["payment_url"]


def test_create_preference_uses_init_point_when_not_sandbox(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    with patch("modules.payments.mercadopago.mercadopago.SDK") as mock_sdk:
        mock_instance = MagicMock()
        mock_sdk.return_value = mock_instance
        mock_instance.preference().create.return_value = {
            "status": 201,
            "response": {
                "id": "pref_456",
                "sandbox_init_point": "https://sandbox.mercadopago.com/pay/pref_456",
                "init_point": "https://mercadopago.com/pay/pref_456",
            }
        }
        client = MPClient(sandbox=False)
        result = client.create_preference(
            title="Plan Nutricional",
            price=40000,
            external_reference="5491112345678",
        )
        assert "sandbox" not in result["payment_url"]
        assert result["payment_url"] == "https://mercadopago.com/pay/pref_456"
