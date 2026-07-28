"""
Apple DeviceCheck — cihaz basina KALICI ucretsiz-tarama sayaci.

Neden: ucretsiz tarama gercek API parasi yakiyor. Cihaz UUID / IP silinip
tekrar kurulunca (ya da wipe'ta) sifirlaniyor → sinirsiz ucretsiz istismar.
Apple DeviceCheck her FIZIKSEL cihaz icin Apple sunucusunda 2 bit kalici depo
verir; uygulama silinse/yeniden kurulsa/cihaz "erase" edilse bile korunur.

🔴 KRITIK: bu 2 bit UYGULAMA basina DEGIL, GELISTIRICI EKIBI (2Y6PBTM588) basina.
Ayni ekipteki daktilo (app.timeletter.mobile) AYNI iki biti kullaniyor. Eskiden ikisi de
0..3 sayaci yaziyordu → 2 ucretsiz GEONI taramasi yapan kullanici daktilo'da sayaci
dolu okuyup ilk mektuptan itibaren paraya takiliyordu (tersi de). Kurucu karari
(2026-07-27): **her uygulamaya 1 bit, uygulama basina 1 ucretsiz hak.**

    bit0 → daktilo        bit1 → GEONI (BU dosya)

Sayac yok; GEONI yalnizca kendi bitini boole olarak okur/yazar.

⚠️ Apple'da tek bit yazan uc YOK: `update_two_bits` IKI biti birden yazar. Bu yuzden
yazmadan once daktilo'nun bitini okuyup AYNEN geri yazmak zorunludur (oku-degistir-yaz).
Bu adim atlanirsa daktilo kullanicilarinin hakki sifirlanir.

Akis:
  1) iOS istemci `DCDevice.generateToken()` ile device_token uretir, backend'e yollar.
  2) query_device_state(token) → {"used": GEONI biti, "other": daktilo biti}.
  3) Tarama izinliyse ve tamamlandiysa mark_device_used(token, state).

Gerekli env (App Store Connect → Keys → DeviceCheck .p8):
  APPLE_TEAM_ID           (ör. 2Y6PBTM588)
  DEVICECHECK_KEY_ID      (.p8 uretilince verilen 10 haneli Key ID)
  DEVICECHECK_PRIVATE_KEY (.p8 dosyasinin PEM icerigi; \\n'ler gercek satir sonu)
  DEVICECHECK_ENV         "production" | "development" (varsayilan production)

Env eksikse modul GUVENLI TARAFA duser: is_configured()=False → cagiran cihaz
katmanini atlar (account/IP katmani devrede kalir), tarama BLOKLANMAZ. Boylece
key gelene kadar canli akis bozulmaz.

🛑 ENV'I ACMADAN ONCE OKU (2026-07-28): daktilo'da bir GOC kurali acik —
"bit1=1 & bit0=0 → eski daktilo izi, bit0'a tasi". GEONI canli olmadigi surece bu
desen gercekten daktilo'nundur. Ama biz env'i acar acmaz ayni desen "GEONI hakkini
kullandi" demeye baslar; kural acik kalirsa daktilo her muhurlemede GEONI'nin bitini
SILER. Bu yuzden once daktilo tarafinda `DEVICECHECK_LEGACY_BIT1_MIGRATION=0`
(Vercel `timeletter-relay`) yapilmali, memory/devicecheck-bit-sozlesmesi.md'ye
"sonumlendi" yazilmali; ANCAK ONDAN SONRA buraya DEVICECHECK_* env'i girilir.
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

# Ekip bit'i paylasildigi icin CIHAZ katmani yapisal olarak yalnizca 1 hak ifade
# edebilir (tek bit = 0/1). HESAP katmani (profiles.free_scans_used) ayni sayiyi
# kullanir; varsayilan 1'e cekildi (kurucu karari 2026-07-27).
MAX_FREE_SCANS = int(os.environ.get("FREE_SCAN_LIMIT", "1"))

# Sozlesme (memory/devicecheck-bit-sozlesmesi.md) — DEGISTIRME.
_GEONI_BIT = "bit1"
_OTHER_BIT = "bit0"  # daktilo


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


# ── Bit durumu (GEONI = bit1, daktilo = bit0) ───────────────────────────────

async def query_device_state(device_token: str) -> dict | None:
    """{"used": bool, "other": bool} → GEONI biti ve daktilo biti.
    Yeni cihaz / hic bit yazilmamis → ikisi de False.
    None → sorgu yapilamadi (env yok ya da Apple hatasi) → cagiran cihaz
    katmanini ATLAMALI (guvenli taraf: diger katmanlar korur).

    `other` yalnizca bilgi degil: yazarken geri konmasi ZORUNLU (bkz. modul basligi)."""
    if not is_configured() or not device_token:
        return None
    r = await _post("/v1/query_two_bits", _base_body(device_token))
    if r is None:
        return None
    bos = {"used": False, "other": False}
    if r.status_code == 200:
        try:
            d = r.json()
        except Exception:
            return bos  # 200 ama govde yok → Apple'in "hic bit yok" bicimi
        if not isinstance(d, dict):
            return bos
        return {"used": bool(d.get(_GEONI_BIT)), "other": bool(d.get(_OTHER_BIT))}
    # Bit hic set edilmemisse Apple bazen 200 disinda duz metin doner → yeni cihaz.
    text = (r.text or "").lower()
    if "failed to find bit state" in text or "bit state not found" in text:
        return bos
    if r.status_code == 400 and "bit" in text:
        return bos
    logger.warning(f"DeviceCheck query beklenmedik yanit: {r.status_code} {r.text[:120]}")
    return None


async def mark_device_used(device_token: str, state: dict | None) -> bool:
    """GEONI bitini 1 yapar; daktilo bitini `state`ten AYNEN korur. Basari → True.

    `state` None ise yazmayiz: diger uygulamanin bitini bilmeden yazmak onun hakkini
    sifirlar. Hak kaybettirmektense bir ucretsiz taramayi kayitsiz birakmak yeglenir
    (hesap katmani + IP rate-limit yine devrede)."""
    if not is_configured() or not device_token:
        return False
    if state is None:
        logger.warning("DeviceCheck: bit durumu okunamadigi icin yazilmadi (daktilo biti korunur)")
        return False
    body = _base_body(device_token)
    body[_GEONI_BIT] = True
    body[_OTHER_BIT] = bool(state.get("other"))
    r = await _post("/v1/update_two_bits", body)
    ok = bool(r is not None and r.status_code == 200)
    if not ok and r is not None:
        logger.warning(f"DeviceCheck update basarisiz: {r.status_code} {r.text[:120]}")
    return ok
