"""
GEONI - Lemon Squeezy payment integration.

Lemon Squeezy is a Merchant of Record - it handles card processing, VAT/
sales tax compliance and invoicing itself, GEONI never touches raw card
data. Chosen over Stripe because Stripe doesn't support Turkey as a
seller country.

Docs: https://docs.lemonsqueezy.com/api

Flow:
1. User picks a credit package on the frontend -> POST /api/checkout/create
2. We create a Lemon Squeezy checkout via their API, embedding the GEONI
   user_id and credit amount in checkout_data.custom, and return the
   hosted checkout URL for the frontend to redirect to.
3. User pays on Lemon Squeezy's hosted page (we never see the card).
4. Lemon Squeezy POSTs an "order_created" webhook back to us with the
   same custom data (in meta.custom_data) - we credit the user's account.
"""

import os
import hmac
import hashlib
import logging

import httpx

logger = logging.getLogger(__name__)

LEMONSQUEEZY_API_KEY = os.environ.get("LEMONSQUEEZY_API_KEY", "")
LEMONSQUEEZY_WEBHOOK_SECRET = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")
LEMONSQUEEZY_STORE_ID = os.environ.get("LEMONSQUEEZY_STORE_ID", "")

API_BASE = "https://api.lemonsqueezy.com/v1"


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 of the raw request body, keyed with the webhook signing
    secret, compared timing-safe against the X-Signature header."""
    if not LEMONSQUEEZY_WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(LEMONSQUEEZY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def create_checkout(variant_id: str, user_id: str, credits: int, email: str = "") -> str | None:
    """Creates a hosted Lemon Squeezy checkout for the given variant,
    embedding user_id + credits as custom data so the webhook knows who
    to credit and how much. Returns the checkout URL, or None on failure."""
    if not LEMONSQUEEZY_API_KEY or not LEMONSQUEEZY_STORE_ID:
        return None
    body = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email": email or None,
                    "custom": {"user_id": user_id, "credits": credits},
                },
            },
            "relationships": {
                "store": {"data": {"type": "stores", "id": LEMONSQUEEZY_STORE_ID}},
                "variant": {"data": {"type": "variants", "id": variant_id}},
            },
        },
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{API_BASE}/checkouts",
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                    "Authorization": f"Bearer {LEMONSQUEEZY_API_KEY}",
                },
                json=body,
                timeout=20,
            )
            if r.status_code not in (200, 201):
                logger.warning(f"Lemon Squeezy checkout create failed: {r.status_code} {r.text[:300]}")
                return None
            return r.json()["data"]["attributes"]["url"]
    except Exception as e:
        logger.warning(f"Lemon Squeezy checkout create error: {e}")
        return None


def parse_order_webhook(payload: dict) -> dict | None:
    """Extracts what we need from an order_created webhook payload.
    Returns None if this isn't a paid order or our custom data is missing
    (e.g. a manual test order created directly in the LS dashboard)."""
    event = (payload.get("meta") or {}).get("event_name")
    if event != "order_created":
        return None
    custom = (payload.get("meta") or {}).get("custom_data") or {}
    user_id = custom.get("user_id")
    credits = custom.get("credits")
    if not user_id or not credits:
        return None
    attrs = (payload.get("data") or {}).get("attributes") or {}
    if attrs.get("status") != "paid":
        return None
    return {
        "user_id": user_id,
        "credits": int(credits),
        "amount_paid": (attrs.get("total") or 0) / 100,  # LS reports cents
        "currency_paid": attrs.get("currency"),
        "external_id": str((payload.get("data") or {}).get("id") or ""),
        "email": attrs.get("user_email"),
    }
