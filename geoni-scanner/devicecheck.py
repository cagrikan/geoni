"""
Apple DeviceCheck — cihaz basina KALICI ucretsiz-tarama sayaci.

Neden: ucretsiz tarama gercek API parasi yakiyor. Cihaz UUID / IP silinip
tekrar kurulunca (ya da wipe'ta) sifirlaniyor → sinirsiz ucretsiz istismar.
Apple DeviceCheck her FIZIKSEL cihaz icin Apple sunucusunda 2 bit kalici depo
verir; uygulama silinse/yeniden kurulsa/cihaz "erase" edilse bile korunur.

2 bit = 0..3 durum. free_scans_used sayacini burada tutariz:
    count = (bit1 << 1) | bit0     (bit0 = count&1, bit1 = (count>>1)&1)

Akis:
  1) iOS istemci `DCDevice.generateToken()` ile device_token uretir, backend'e yollar.
  2) query_device_count(token) → cihazin mevcut sayaci (yeni cihaz → 0).
  3) Tarama izinliyse ve tamamlandiysa set_device_count(token, count+1).

Gerekli env (App Store Connect → Keys → DeviceCheck .p8):
  APPLE_TEAM_ID           (ör. 2Y6PBTM588)
  DEVICECHECK_KEY_ID      (.p8 uretilince verilen 10 haneli Key ID)
  DEVICECHECK_PRIVATE_KEY (.p8 dosyasinin PEM icerigi; \\n'ler gercek satir sonu)
  DEVICECHECK_ENV         "production" | "development" (varsayilan production)

Env eksikse modul GUVENLI TARAFA duser: is_configured()=False → cagiran cihaz
katmanini atlar (account/IP katmani devrede kalir), tarama BLOKLANMAZ. Boylece
key gelene kadar canli akis bozulmaz.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

import httpx

logger = logging.getLogger(__name__)

APPLE_TEAM_ID = os.environ.get("APPLE_TEAM_ID", "")
DEVICECHECK_KEY_ID = os.environ.get("DEVICECHECK_KEY_ID", "")
# PEM: env'de "\n" kacisli olabilir → gercek satir sonuna cevir.
DEVICECHECK_PRIVATE_KEY = os.environ.get("DEVICECHECK_PRIVATE_KEY", "").replace("\\n", "\n")
_ENV = os.environ.get("DEVICECHECK_ENV", "production").strip().lower()

_HOST = (
    "https://api.development.devicecheck.apple.com"
    if _ENV.startswith("dev")
    else "https://api.devicecheck.apple.com"
)

MAX_FREE_SCANS = int(os.environ.get("FREE_SCAN_LIMIT", "2"))


def is_configured() -> bool:
    """Cihaz katmani calisir durumda mi (env tam mi)."""
    return bool(APPLE_TEAM_ID and DEVICECHECK_KEY_ID and DEVICECHECK_PRIVATE_KEY)


# ── ES256 JWT (Apple bearer) ────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt() -> str:
    """Apple DeviceCheck icin kisa omurlu ES256 JWT (iss=TEAM_ID, kid=KEY_ID)."""
    import json
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    header = {"alg": "ES256", "kid": DEVICECHECK_KEY_ID, "typ": "JWT"}
    payload = {"iss": APPLE_TEAM_ID, "iat": int(time.time())}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    ).encode()

    key = serialization.load_pem_private_key(DEVICECHECK_PRIVATE_KEY.encode(), password=None)
    der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    # DER → JOSE (r||s, her biri 32 bayt) — JWT ES256 ham imza ister.
    r, s = utils.decode_dss_signature(der_sig)
    jose_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input.decode() + "." + _b64url(jose_sig)


async def _post(path: str, body: dict) -> httpx.Response | None:
    try:
        jwt = _make_jwt()
    except Exception as e:  # cryptography yok / key bozuk → cihaz katmani devre disi
        logger.warning(f"DeviceCheck JWT uretilemedi: {e}")
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            return await client.post(
                f"{_HOST}{path}",
                headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
                json=body,
            )
    except Exception as e:
        logger.warning(f"DeviceCheck istegi basarisiz ({path}): {e}")
        return None


def _base_body(device_token: str) -> dict:
    return {
        "device_token": device_token,
        "transaction_id": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
    }


# ── Sayac (2 bit) ───────────────────────────────────────────────────────────

async def query_device_count(device_token: str) -> int | None:
    """Cihazin ucretsiz-tarama sayaci (0..3). Yeni cihaz / bit yoksa 0.
    None → sorgu yapilamadi (env yok ya da Apple hatasi) → cagiran cihaz
    katmanini ATLAMALI (guvenli taraf: diger katmanlar korur)."""
    if not is_configured() or not device_token:
        return None
    r = await _post("/v1/query_two_bits", _base_body(device_token))
    if r is None:
        return None
    if r.status_code == 200:
        try:
            d = r.json()
            bit0 = 1 if d.get("bit0") else 0
            bit1 = 1 if d.get("bit1") else 0
            return (bit1 << 1) | bit0
        except Exception:
            return 0  # govde parse edilemedi ama 200 → bit set degil kabul et
    # Apple bit hic set edilmemisse 200 + "Failed to find bit state" DONMESI
    # yerine bazen düz metin döner; bunu "yeni cihaz = 0" say.
    text = (r.text or "").lower()
    if "failed to find bit state" in text or "bit state not found" in text:
        return 0
    if r.status_code in (400,) and "bit" in text:
        return 0
    logger.warning(f"DeviceCheck query beklenmedik yanit: {r.status_code} {r.text[:120]}")
    return None


async def set_device_count(device_token: str, count: int) -> bool:
    """Cihaz sayacini `count` (0..3) yap. Basari → True."""
    if not is_configured() or not device_token:
        return False
    count = max(0, min(3, int(count)))
    body = _base_body(device_token)
    body["bit0"] = bool(count & 1)
    body["bit1"] = bool((count >> 1) & 1)
    r = await _post("/v1/update_two_bits", body)
    ok = bool(r is not None and r.status_code == 200)
    if not ok and r is not None:
        logger.warning(f"DeviceCheck update basarisiz: {r.status_code} {r.text[:120]}")
    return ok


async def device_over_limit(device_token: str) -> bool:
    """Cihaz ucretsiz tavani doldurmus mu? Sorgu yapilamadiysa (None) → False
    (guvenli taraf: cihaz katmani belirsizse account/IP katmani karar verir)."""
    count = await query_device_count(device_token)
    if count is None:
        return False
    return count >= MAX_FREE_SCANS
