"""Ödeme webhook'ları — para basma uçları (denetim #9, KRİTİK).

RevenueCat (IAP) ve Polar (web) webhook'ları cüzdana kredi yazar. İmza/auth
doğrulaması ve olay ayrıştırma saf fonksiyonlardır; burada kilitlenir ki bir
regresyon "imzasız isteğe kredi ver" ya da "anonim kullanıcıya kredi" açığı
üretmesin.
"""
import base64
import hashlib
import hmac
import time

import iap
import polar


# ── RevenueCat auth ───────────────────────────────────────────────────────
def test_revenuecat_auth_accepts_secret(monkeypatch):
    monkeypatch.setattr(iap, "REVENUECAT_WEBHOOK_SECRET", "s3cret")
    assert iap.verify_webhook_auth("s3cret") is True
    assert iap.verify_webhook_auth("Bearer s3cret") is True


def test_revenuecat_auth_rejects_wrong(monkeypatch):
    monkeypatch.setattr(iap, "REVENUECAT_WEBHOOK_SECRET", "s3cret")
    assert iap.verify_webhook_auth("wrong") is False
    assert iap.verify_webhook_auth("Bearer wrong") is False
    assert iap.verify_webhook_auth("") is False


def test_revenuecat_auth_fails_closed_when_unset(monkeypatch):
    # Giz yapılandırılmamışsa HER istek reddedilmeli (fail-closed).
    monkeypatch.setattr(iap, "REVENUECAT_WEBHOOK_SECRET", "")
    assert iap.verify_webhook_auth("anything") is False


# ── RevenueCat olay ayrıştırma ────────────────────────────────────────────
def _rc(etype, **over):
    ev = {"type": etype, "app_user_id": "geoni-user-1", "product_id": "tok_100", "id": "evt_1"}
    ev.update(over)
    return {"event": ev}


def test_parse_event_grant():
    out = iap.parse_event(_rc("NON_RENEWING_PURCHASE"))
    assert out is not None
    assert out["kind"] == "grant"
    assert out["user_id"] == "geoni-user-1"
    assert out["external_id"] == "rc_evt_1"  # idempotency anahtarı


def test_parse_event_refund():
    out = iap.parse_event(_rc("REFUND"))
    assert out is not None and out["kind"] == "refund"


def test_parse_event_ignores_unknown_type():
    assert iap.parse_event(_rc("TEST")) is None
    assert iap.parse_event(_rc("RENEWAL")) is None


def test_parse_event_rejects_anonymous():
    # RevenueCat tanımadığı kullanıcıyı $RCAnonymousID ile işaretler — kredi verme.
    assert iap.parse_event(_rc("INITIAL_PURCHASE", app_user_id="$RCAnonymousID:abc")) is None


def test_parse_event_requires_ids():
    assert iap.parse_event(_rc("INITIAL_PURCHASE", app_user_id=None)) is None
    assert iap.parse_event(_rc("INITIAL_PURCHASE", product_id=None)) is None
    assert iap.parse_event(_rc("INITIAL_PURCHASE", id=None, transaction_id=None)) is None


# ── Polar (Standard Webhooks) imza doğrulama ──────────────────────────────
def _polar_sign(secret_raw: bytes, webhook_id: str, timestamp: str, body: bytes) -> str:
    signed = f"{webhook_id}.{timestamp}.".encode() + body
    sig = base64.b64encode(hmac.new(secret_raw, signed, hashlib.sha256).digest()).decode()
    return f"v1,{sig}"


def test_polar_signature_valid(monkeypatch):
    secret = "topsecretkey"
    monkeypatch.setattr(polar, "POLAR_WEBHOOK_SECRET", secret)
    body = b'{"type":"order.paid","data":{}}'
    wid, ts = "msg_123", str(int(time.time()))
    header = _polar_sign(secret.encode(), wid, ts, body)
    assert polar.verify_webhook_signature(body, wid, ts, header) is True


def test_polar_signature_wrong_key(monkeypatch):
    monkeypatch.setattr(polar, "POLAR_WEBHOOK_SECRET", "topsecretkey")
    body = b'{"type":"order.paid"}'
    wid, ts = "msg_123", str(int(time.time()))
    header = _polar_sign(b"attacker-key", wid, ts, body)
    assert polar.verify_webhook_signature(body, wid, ts, header) is False


def test_polar_signature_tampered_body(monkeypatch):
    secret = "topsecretkey"
    monkeypatch.setattr(polar, "POLAR_WEBHOOK_SECRET", secret)
    wid, ts = "msg_123", str(int(time.time()))
    header = _polar_sign(secret.encode(), wid, ts, b'{"amount":10}')
    # İmza 10 için üretildi ama gövde 1000'e değiştirildi.
    assert polar.verify_webhook_signature(b'{"amount":1000}', wid, ts, header) is False


def test_polar_signature_replay_rejected(monkeypatch):
    secret = "topsecretkey"
    monkeypatch.setattr(polar, "POLAR_WEBHOOK_SECRET", secret)
    body = b'{"type":"order.paid"}'
    old_ts = str(int(time.time()) - 10_000)  # replay toleransının çok ötesi
    header = _polar_sign(secret.encode(), "msg_1", old_ts, body)
    assert polar.verify_webhook_signature(body, "msg_1", old_ts, header) is False


def test_polar_signature_fails_closed_when_unset(monkeypatch):
    monkeypatch.setattr(polar, "POLAR_WEBHOOK_SECRET", "")
    assert polar.verify_webhook_signature(b"{}", "id", str(int(time.time())), "v1,x") is False
