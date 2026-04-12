import hashlib
import hmac
import logging
import httpx
from core.phone import normalize_phone

logger = logging.getLogger(__name__)

WA_API_VERSION = "v22.0"
WA_API_BASE = f"https://graph.facebook.com/{WA_API_VERSION}"


def validate_webhook_signature(body: bytes, signature: str, app_secret: str) -> bool:
    """Validate Meta webhook HMAC-SHA256 signature."""
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class WhatsAppClient:
    def __init__(self, phone_number_id: str, access_token: str):
        self._phone_number_id = phone_number_id
        self._token = access_token
        self._client = httpx.AsyncClient(timeout=30)

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def send_text(self, to: str, text: str) -> None:
        to = normalize_phone(to)
        url = f"{WA_API_BASE}/{self._phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        resp = await self._client.post(url, json=payload, headers=self._headers)
        if not resp.is_success:
            logger.error("WhatsApp send error %s: %s", resp.status_code, resp.text)

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        url = f"{WA_API_BASE}/{media_id}"
        resp = await self._client.get(url, headers=self._headers)
        resp.raise_for_status()
        data = resp.json()
        download_url = data["url"]
        mime_type = data.get("mime_type", "audio/ogg")
        media_resp = await self._client.get(download_url, headers=self._headers)
        media_resp.raise_for_status()
        return media_resp.content, mime_type
