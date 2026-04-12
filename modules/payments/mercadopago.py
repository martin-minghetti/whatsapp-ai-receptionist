# modules/payments/mercadopago.py
import hashlib
import hmac
import logging
import os

import mercadopago

logger = logging.getLogger(__name__)


def validate_mp_signature(body: bytes, x_signature: str, webhook_secret: str) -> bool:
    """
    Validate Mercado Pago webhook signature.
    MP sends X-Signature header with format: ts=<timestamp>,v1=<hash>
    """
    if not x_signature:
        return False
    try:
        parts = dict(p.split("=", 1) for p in x_signature.split(","))
        ts = parts.get("ts", "")
        v1 = parts.get("v1", "")
        if not ts or not v1:
            return False
        expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(v1, expected)
    except Exception:
        return False


class MPClient:
    def __init__(self, sandbox: bool = True):
        self._sandbox = sandbox
        access_token = os.environ["MP_ACCESS_TOKEN"]
        self._sdk = mercadopago.SDK(access_token)

    def create_preference(self, title: str, price: int, external_reference: str) -> dict:
        """
        Create a payment preference (checkout link).
        external_reference stores the phone number so the webhook can look it up.
        """
        preference_data = {
            "items": [{
                "title": title,
                "quantity": 1,
                "unit_price": float(price),
                "currency_id": "ARS",
            }],
            "external_reference": external_reference,
            "back_urls": {
                "success": "https://wa.me",
                "failure": "https://wa.me",
                "pending": "https://wa.me",
            },
            "auto_return": "approved",
        }
        result = self._sdk.preference().create(preference_data)
        if result["status"] != 201:
            raise RuntimeError(f"MP preference creation failed: {result}")

        resp = result["response"]
        url_key = "sandbox_init_point" if self._sandbox else "init_point"
        return {
            "preference_id": resp["id"],
            "payment_url": resp[url_key],
        }

    def get_payment(self, payment_id: str) -> dict:
        """Get payment status from MP API."""
        result = self._sdk.payment().get(payment_id)
        return result["response"]
