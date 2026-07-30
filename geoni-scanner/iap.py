"""
GEONI - RevenueCat In-App Purchase integration.

Apple (and Google) require digital goods to be sold through their own
In-App Purchase system inside the mobile app; we can't send iOS users to
the Polar hosted checkout for token packs. RevenueCat sits in front of
StoreKit/Play Billing: the app buys a consumable via RevenueCat's SDK, and
RevenueCat POSTs a server-to-server webhook here so we can credit the
user's GEONI wallet - exactly like the Polar webhook does for the web.

Docs: https://www.revenuecat.com/docs/integrations/webhooks

Flow:
1. Mobile app calls Purchases.logIn(<supabase user id>) then buys a
   consumable product (ai.geoni.tokens.100 / .500 / .1000).
2. Apple/Google process the payment; RevenueCat validates the receipt.
3. RevenueCat POSTs a NON_RENEWING_PURCHASE event to /api/webhooks/revenuecat
   with app_user_id = the GEONI user id and the product_id.
4. We map product_id -> credits (credit_packages.apple_product_id) and
   credit the wallet, idempotent on the RevenueCat event id.

Auth: RevenueCat lets you set a fixed Authorization header value in the
dashboard; we compare it (constant-time) against REVENUECAT_WEBHOOK_SECRET.
If the secret is unset the endpoint fails closed - it is never an open
crediting endpoint.
"""

import os
import hmac
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

REVENUECAT_WEBHOOK_SECRET = os.environ.get("REVENUECAT_WEBHOOK_SECRET", "")

# Event types that grant credits. Token packs are consumables, so the store
# reports NON_RENEWING_PURCHASE; we also accept INITIAL_PURCHASE in case a
# product is ever configured as a non-consumable one-off.
GRANT_EVENT_TYPES = {"NON_RENEWING_PURCHASE", "INITIAL_PURCHASE"}
# Event types that reverse a purchase (store-issued refund / chargeback).
REFUND_EVENT_TYPES = {"REFUND", "CANCELLATION"}


def verify_webhook_auth(auth_header: str) -> bool:
    """Constant-time check of the Authorization header RevenueCat sends
    against our configured shared secret. Fails closed when unset."""
    if not REVENUECAT_WEBHOOK_SECRET or not auth_header:
        return False
    # Accept both "Bearer <secret>" and a bare "<secret>" configuration.
    candidate = auth_header
    if auth_header.startswith("Bearer "):
        candidate = auth_header[len("Bearer "):]
    return hmac.compare_digest(candidate, REVENUECAT_WEBHOOK_SECRET) or hmac.compare_digest(
        auth_header, REVENUECAT_WEBHOOK_SECRET
    )


def parse_event(payload: dict) -> dict | None:
    """Normalise a RevenueCat webhook body into the fields we credit on, or
    None if it's an event type we ignore (renewals, test pings, etc.).

    Returns: {kind, user_id, product_id, external_id, price, currency,
    environment, store, purchased_at} where kind is "grant" or "refund".
    """
    event = (payload or {}).get("event") or {}
    etype = event.get("type")
    if etype not in GRANT_EVENT_TYPES and etype not in REFUND_EVENT_TYPES:
        return None

    user_id = event.get("app_user_id")
    product_id = event.get("product_id")
    if not user_id or not product_id:
        logger.warning("revenuecat event %s missing app_user_id/product_id", etype)
        return None

    # RevenueCat anonymises users it doesn't recognise as "$RCAnonymousID:...".
    # We always call logIn(<supabase uid>) before purchase, so a real credit
    # must carry a real GEONI user id - refuse to credit anonymous ids.
    if user_id.startswith("$RCAnonymousID:"):
        logger.warning("revenuecat event for anonymous user, ignoring")
        return None

    event_id = event.get("id") or event.get("transaction_id")
    if not event_id:
        return None

    kind = "grant" if etype in GRANT_EVENT_TYPES else "refund"
    return {
        "kind": kind,
        "user_id": user_id,
        "product_id": product_id,
        "external_id": f"rc_{event_id}",
        "price": event.get("price_in_purchased_currency") or event.get("price") or 0,
        "currency": event.get("currency") or "USD",
        "environment": event.get("environment") or "PRODUCTION",
        # Hangi magaza odemeyi aldi. RevenueCat: APP_STORE / MAC_APP_STORE /
        # PLAY_STORE / AMAZON / STRIPE / PROMOTIONAL. Bilinmiyorsa APP_STORE'a
        # DUSMEYIZ - "UNKNOWN" der ve kanal oyle yazilir; sessizce iOS saymak
        # tam olarak 2026-07-29'da yasanan hataydi (Play alimi "ios_sandbox"
        # olarak deftere gecti).
        "store": event.get("store") or "UNKNOWN",
        # K5 (2026-07-30): satin almanin MAGAZADAKI zamani. Webhook gecikebilir
        # (retry/ag), bu yuzden "hangi niyet bu satin almaya ait" sorusu teslim
        # anina degil SATIN ALMA anina gore cevaplanmali — yoksa gecikmis webhook
        # daha yeni bir niyeti tuketip bileti yanlis hedefe aciyor.
        # None = magaza vermedi -> cagiran eski (zamansiz) davranisa duser.
        "purchased_at": _ms_to_dt(event.get("purchased_at_ms")),
    }


def _ms_to_dt(ms) -> datetime | None:
    """RevenueCat epoch-ms -> timezone-aware UTC datetime. Bozuk/eksik deger
    sessizce None doner: zaman bilgisi olmadan da webhook islenebilmeli."""
    try:
        if ms is None:
            return None
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        logger.warning("revenuecat purchased_at_ms cozulemedi: %r", ms)
        return None


# RevenueCat magaza adi -> bizim kanal etiketimiz (credit_transactions.channel).
# Admin ciro raporu (db.get_admin_sales_stats) bu etikete gore kiriliyor.
_STORE_CHANNEL = {
    "APP_STORE": "ios",
    "MAC_APP_STORE": "ios",
    "PLAY_STORE": "android",
    "AMAZON": "amazon",
    "STRIPE": "stripe",
    "RC_BILLING": "rc_billing",
    "PADDLE": "paddle",
    "PROMOTIONAL": "promo",
}

# Magaza adi -> kullaniciya/admine gorunen aciklama metni.
_STORE_LABEL = {
    "APP_STORE": "App Store",
    "MAC_APP_STORE": "App Store",
    "PLAY_STORE": "Play Store",
    "AMAZON": "Amazon Appstore",
    "STRIPE": "Stripe",
    "RC_BILLING": "RevenueCat Billing",
    "PADDLE": "Paddle",
    "PROMOTIONAL": "Promosyon",
}


def channel_and_label(store: str, sandbox: bool) -> tuple[str, str]:
    """(kanal, magaza adi) dondurur. Sandbox alimlar AYRI bir kanala yazilir
    ('..._sandbox') cunku gercek para donmez ve ciro toplamina girmemeleri
    gerekir - bkz. db.get_admin_sales_stats."""
    store = (store or "UNKNOWN").upper()
    channel = _STORE_CHANNEL.get(store, store.lower())
    label = _STORE_LABEL.get(store, store.title())
    return (channel + "_sandbox" if sandbox else channel), label


def is_sandbox_channel(channel: str) -> bool:
    """Ciro raporlarinin disladigi kanal mi (gercek para donmemis)."""
    return (channel or "").endswith("_sandbox")
