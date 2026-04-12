import hashlib
import hmac
import pytest
from core.whatsapp import validate_webhook_signature, WhatsAppClient

SECRET = "test_app_secret"
BODY = b'{"test": "data"}'

def _make_sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

def test_valid_signature():
    sig = _make_sig(BODY, SECRET)
    assert validate_webhook_signature(BODY, sig, SECRET) is True

def test_invalid_signature():
    assert validate_webhook_signature(BODY, "sha256=invalid", SECRET) is False

def test_missing_sha256_prefix():
    assert validate_webhook_signature(BODY, "invalidsig", SECRET) is False

@pytest.mark.asyncio
async def test_client_send_calls_api(httpx_mock):
    from core.whatsapp import WhatsAppClient
    httpx_mock.add_response(url="https://graph.facebook.com/v22.0/123/messages", json={"messages": [{"id": "msg_id"}]})
    client = WhatsAppClient(phone_number_id="123", access_token="token")
    await client.send_text("54911111111", "hola")
    # No exception = pass
