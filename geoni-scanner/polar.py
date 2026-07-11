"""
GEONI - Polar payment integration.

Polar (polar.sh) is a Merchant of Record like Lemon Squeezy - it handles
card processing, VAT/sales tax and invoicing, GEONI never touches raw card
data. Added as an alternative provider while Lemon Squeezy account approval
is pending; Turkey is on Polar's supported seller list.

Docs: https://polar.sh/docs/api-reference

Flow (mirrors lemonsqueezy.py):
1. User picks a credit package on the frontend -> POST /api/checkout/create
2. We create a Polar checkout via their API, embedding the GEONI user_id
   and credit amount in metadata, and return the hosted checkout URL.
3. User pays on Polar's hosted page.
4. Polar POSTs an "order.paid" webhook (Standard Webhooks signature) with
   the same metadata copied onto the order - we credit the user's account.
"""

import os
import hmac
import time
import base64
import hashlib
import logging

import httpx

logger = logging.getLogger(__name__)

POLAR_ACCESS_TOKEN = os.environ.get("POLAR_ACCESS_TOKEN", "")
POLAR_WEBHOOK_SECRET = os.environ.get("POLAR_WEBHOOK_SECRET", "")

API_BASE = "https://api.polar.sh/v1"
# Polar sits behind Cloudflare, which rejects default Python user agents.
HEADERS = {
    "Authorization": f"Bearer {POLAR_ACCESS_TOKEN}",
    "User-Agent": "geoni-backend/1.0",
}
SUCCESS_URL = "https://app.geoni.ai/?checkout=success"
WEBHOOK_TOLERANCE_SECONDS = 300


def verify_webhook_signature(raw_body: bytes, webhook_id: str, timestamp: str, signature_header: str) -> bool:
    """Standard Webhooks verification: HMAC-SHA256 over "{id}.{timestamp}.{body}"
    keyed with the endpoint secret, base64-encoded, compared timing-safe
    against each candidate in the (space-separated) webhook-signature header.
    Rejects deliveries older than the replay tolerance."""
    if not POLAR_WEBHOOK_SECRET or not webhook_id or not timestamp or not signature_header:
        return False
    try:
        if abs(time.time() - int(timestamp)) > WEBHOOK_TOLERANCE_SECONDS:
            return False
        # Standard Webhooks der ki: anahtar, whsec_ sonrasi base64'un
        # cozulmus hali. Polar pratikte gizi ham dize olarak da
        # anahtarlayabiliyor - iki yorumu da deniyoruz (ikisi de yalnizca
        # bizim ve Polar'in bildigi gizden turetildigi icin guvenli).
        secret = POLAR_WEBHOOK_SECRET
        keys = [secret.encode()]
        if secret.startswith("whsec_"):
            b64 = secret[len("whsec_"):]
            try:
                # Polar base64 kismi '=' dolgusuz uretiyor.
                keys.append(base64.b64decode(b64 + "=" * (-len(b64) % 4)))
            except Exception:
                pass
        signed_content = f"{webhook_id}.{timestamp}.".encode() + raw_body
        expected = [
            base64.b64encode(hmac.new(k, signed_content, hashlib.sha256).digest()).decode()
            for k in keys
        ]
        for candidate in signature_header.split(" "):
            # Each entry looks like "v1,<base64sig>"
            parts = candidate.split(",", 1)
            if len(parts) == 2 and any(hmac.compare_digest(e, parts[1]) for e in expected):
                return True
    except Exception:
        return False
    return False


async def create_checkout(product_id: str, user_id: str, credits: int, email: str = "") -> str | None:
    """Creates a hosted Polar checkout for the given product, embedding
    user_id + credits as metadata so the webhook knows who to credit and
    how much. Returns the checkout URL, or None on failure."""
    if not POLAR_ACCESS_TOKEN:
        return None
    body = {
        "products": [product_id],
        "metadata": {"user_id": user_id, "credits": str(credits)},
        "success_url": SUCCESS_URL,
    }
    if email:
        body["customer_email"] = email
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{API_BASE}/checkouts/", headers=HEADERS, json=body, timeout=20)
            if r.status_code not in (200, 201):
                logger.warning(f"Polar checkout create failed: {r.status_code} {r.text[:300]}")
                return None
            return r.json()["url"]
    except Exception as e:
        logger.warning(f"Polar checkout create error: {e}")
        return None


def parse_order_webhook(payload: dict) -> dict | None:
    """Extracts what we need from an order.paid webhook payload. Returns
    None if this isn't a paid order or our metadata is missing (e.g. an
    order created manually in the Polar dashboard)."""
    if payload.get("type") != "order.paid":
        return None
    order = payload.get("data") or {}
    if not order.get("paid") and order.get("status") != "paid":
        return None
    metadata = order.get("metadata") or {}
    user_id = metadata.get("user_id")
    credits = metadata.get("credits")
    if not user_id or not credits:
        return None
    customer = order.get("customer") or {}
    return {
        "user_id": user_id,
        "credits": int(credits),
        "amount_paid": (order.get("total_amount") or 0) / 100,  # Polar reports cents
        "currency_paid": order.get("currency"),
        "external_id": str(order.get("id") or ""),
        "email": customer.get("email"),
    }
