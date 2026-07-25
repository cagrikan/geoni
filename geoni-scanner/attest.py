"""Apple App Attest — mobil istemcinin GERCEK uygulama + GERCEK cihaz oldugunu kriptografik dogrular.

NEDEN VAR (2026-07-25): mobil muafiyeti (Turnstile atlama + IP rate-limit atlama)
`_is_mobile_client()` ile YALNIZCA User-Agent string'ine bakiyordu. Sahte UA gonderen biri
Turnstile'i, IP limitini ve (device_token gondermeyerek) DeviceCheck katmanini atlayabiliyordu;
geriye yalnizca saldirganin kendi girdisi olan e-posta/alan limiti kaliyordu. Her tarama
~$0.31 gercek para -> bakiye yakma acigi.

App Attest bunu kapatir: Apple'in guvenli enclave'inde uretilen anahtarla imzalanmis
assertion, istegin gercekten bizim uygulamamizdan ve gercek Apple donanimindan geldigini
kanitlar. UA taklidi ise yaramaz.

AKIS
  1) /api/attest/challenge      -> tek kullanimlik challenge
  2) cihaz anahtar uretir, Apple'dan attestation alir
  3) /api/attest/register       -> attestation dogrulanir, public key saklanir
  4) her tarama isteginde assertion basligi -> verify_assertion() ile dogrulanir

DOGRULAMA ADIMLARI (Apple: "Validating Apps That Connect to Your Server")
  attestation: CBOR coz -> sertifika zinciri Apple App Attest Root CA'ya kadar dogrula ->
    nonce = SHA256(authData || SHA256(challenge)) credCert uzantisinda (1.2.840.113635.100.8.2)
    -> keyId == SHA256(public key) -> rpIdHash == SHA256("TEAMID.bundleid") -> counter == 0
  assertion: CBOR coz -> nonce = SHA256(authenticatorData || SHA256(clientData)) ->
    imzayi saklanan public key ile dogrula -> counter ARTMIS olmali (replay engeli)
"""

import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# appId = "<TEAM_ID>.<BUNDLE_ID>" — rpIdHash bunun SHA256'sidir
APPLE_TEAM_ID = os.environ.get("APPLE_TEAM_ID", "2Y6PBTM588")
APP_BUNDLE_ID = os.environ.get("APP_BUNDLE_ID", "ai.geoni.app")
APP_ID = f"{APPLE_TEAM_ID}.{APP_BUNDLE_ID}"

CHALLENGE_TTL_MIN = 5
# Faz 1: dogrula + logla ama muafiyeti HALA UA belirlesin (davranis degismez).
# Faz 2: ATTEST_ENFORCE=1 -> muafiyet yalnizca gecerli assertion ile verilir.
ENFORCE = os.environ.get("ATTEST_ENFORCE", "0") == "1"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Apple App Attest Root CA — attestation sertifika zincirinin kokü.
APPLE_ROOT_CA_PEM = b"""-----BEGIN CERTIFICATE-----
MIICITCCAaegAwIBAgIQC/O+DvHN0uD7jG5yH2IXmDAKBggqhkjOPQQDAzBSMSYw
JAYDVQQDDB1BcHBsZSBBcHAgQXR0ZXN0YXRpb24gUm9vdCBDQTETMBEGA1UECgwK
QXBwbGUgSW5jLjETMBEGA1UECAwKQ2FsaWZvcm5pYTAeFw0yMDAzMTgxODMyNTNa
Fw00NTAzMTUwMDAwMDBaMFIxJjAkBgNVBAMMHUFwcGxlIEFwcCBBdHRlc3RhdGlv
biBSb290IENBMRMwEQYDVQQKDApBcHBsZSBJbmMuMRMwEQYDVQQIDApDYWxpZm9y
bmlhMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAERTHhmLW07ATaFQIEVwTtT4dyctdh
NbJhFs/Ii2FdCgAHGbpphY3+d8qjuDngIN3WVhQUBHAoMeQ/cLiP1sOUtgjqK9au
Yen1mMEvRq9Sk3Jm5X8U62H+xTD3FE9TgS41o0IwQDAPBgNVHRMBAf8EBTADAQH/
MB0GA1UdDgQWBBSskRBTM72+aEH/pwyp5frq5eWKoTAOBgNVHQ8BAf8EBAMCAQYw
CgYIKoZIzj0EAwMDaAAwZQIwQgFGnByvsiVbpTKwSga0kP0e8EeDS4+sQmTvb7vn
53O5+FRXgeLhpJ06ysC5PrOyAjEAp5U4xDgEgllF7En3VcE3iexZZtKeYnpqtijV
oyFraWVIyd/dganmrduC1bmTBGwD
-----END CERTIFICATE-----"""

# credCert icindeki nonce uzantisinin OID'i
NONCE_OID = "1.2.840.113635.100.8.2"


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


# --------------------------------------------------------------------------- challenge
async def new_challenge() -> str | None:
    """Tek kullanimlik challenge uret + kaydet."""
    if not is_configured():
        return None
    ch = secrets.token_urlsafe(32)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{SUPABASE_URL}/rest/v1/attest_challenges",
                             headers=_headers(), json={"challenge": ch}, timeout=10)
            if r.status_code >= 300:
                logger.warning(f"attest challenge kaydedilemedi: {r.status_code}")
                return None
    except Exception as e:
        logger.warning(f"attest challenge hatasi: {e}")
        return None
    return ch


async def _consume_challenge(ch: str) -> bool:
    """Challenge gecerli mi + tek kullanimlik olarak tuket."""
    if not is_configured():
        return False
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{SUPABASE_URL}/rest/v1/attest_challenges",
                            headers=_headers(),
                            params={"challenge": f"eq.{ch}", "select": "challenge,created_at,used_at"},
                            timeout=10)
            rows = r.json() if r.status_code == 200 else []
            if not rows:
                return False
            row = rows[0]
            if row.get("used_at"):
                return False  # tekrar kullanim
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - created > timedelta(minutes=CHALLENGE_TTL_MIN):
                return False  # suresi gecti
            await c.patch(f"{SUPABASE_URL}/rest/v1/attest_challenges",
                          headers=_headers(), params={"challenge": f"eq.{ch}"},
                          json={"used_at": datetime.now(timezone.utc).isoformat()}, timeout=10)
            return True
    except Exception as e:
        logger.warning(f"challenge dogrulama hatasi: {e}")
        return False


# --------------------------------------------------------------------------- attestation
def _parse_auth_data(auth_data: bytes) -> dict:
    """authenticatorData: rpIdHash(32) | flags(1) | counter(4) | attestedCredentialData..."""
    if len(auth_data) < 37:
        raise ValueError("authData cok kisa")
    rp_id_hash = auth_data[0:32]
    flags = auth_data[32]
    counter = int.from_bytes(auth_data[33:37], "big")
    out = {"rp_id_hash": rp_id_hash, "flags": flags, "counter": counter}
    if len(auth_data) >= 55:
        out["aaguid"] = auth_data[37:53]
        cred_id_len = int.from_bytes(auth_data[53:55], "big")
        out["credential_id"] = auth_data[55:55 + cred_id_len]
    return out


def verify_attestation(key_id_b64: str, attestation_b64: str, challenge: str) -> dict:
    """Attestation'i dogrular. Basarili olursa {'public_key': DER bytes, 'environment': ...} doner.
    Basarisizsa ValueError firlatir."""
    import cbor2
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    att = cbor2.loads(base64.b64decode(attestation_b64))
    if att.get("fmt") != "apple-appattest":
        raise ValueError(f"beklenmeyen fmt: {att.get('fmt')}")

    x5c = att["attStmt"]["x5c"]
    auth_data = att["authData"]
    cred_cert = x509.load_der_x509_certificate(x5c[0])
    ca_cert = x509.load_der_x509_certificate(x5c[1])
    root = x509.load_pem_x509_certificate(APPLE_ROOT_CA_PEM)

    # 1) Zincir: credCert <- caCert <- Apple Root
    for child, parent in ((cred_cert, ca_cert), (ca_cert, root)):
        parent.public_key().verify(
            child.signature, child.tbs_certificate_bytes,
            ec.ECDSA(child.signature_hash_algorithm),
        )

    # 2) nonce = SHA256(authData || SHA256(challenge)) credCert uzantisinda olmali
    client_data_hash = hashlib.sha256(challenge.encode()).digest()
    expected_nonce = hashlib.sha256(auth_data + client_data_hash).digest()
    ext = cred_cert.extensions.get_extension_for_oid(x509.ObjectIdentifier(NONCE_OID))
    if expected_nonce not in ext.value.value:
        raise ValueError("nonce eslesmedi (challenge/authData tutmuyor)")

    # 3) keyId == SHA256(public key, uncompressed point)
    pub = cred_cert.public_key()
    pub_point = pub.public_bytes(serialization.Encoding.X962,
                                 serialization.PublicFormat.UncompressedPoint)
    if hashlib.sha256(pub_point).digest() != base64.b64decode(key_id_b64):
        raise ValueError("keyId public key ile eslesmiyor")

    # 4) rpIdHash == SHA256(appId), counter == 0
    parsed = _parse_auth_data(auth_data)
    if parsed["rp_id_hash"] != hashlib.sha256(APP_ID.encode()).digest():
        raise ValueError("rpIdHash eslesmedi (bundle/team id farkli)")
    if parsed["counter"] != 0:
        raise ValueError(f"attestation counter 0 olmali, {parsed['counter']}")

    aaguid = parsed.get("aaguid", b"")
    env = "development" if aaguid.startswith(b"appattestdevelop") else "production"

    der = pub.public_bytes(serialization.Encoding.DER,
                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return {"public_key": der, "environment": env}


# --------------------------------------------------------------------------- assertion
def verify_assertion(assertion_b64: str, client_data: str, public_key_der: bytes,
                     stored_counter: int) -> int:
    """Assertion imzasini dogrular, yeni counter'i doner. Hata -> ValueError."""
    import cbor2
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    a = cbor2.loads(base64.b64decode(assertion_b64))
    signature = a["signature"]
    auth_data = a["authenticatorData"]

    client_data_hash = hashlib.sha256(client_data.encode()).digest()
    nonce = hashlib.sha256(auth_data + client_data_hash).digest()

    pub = serialization.load_der_public_key(public_key_der)
    pub.verify(signature, nonce, ec.ECDSA(hashes.SHA256()))

    parsed = _parse_auth_data(auth_data)
    if parsed["rp_id_hash"] != hashlib.sha256(APP_ID.encode()).digest():
        raise ValueError("assertion rpIdHash eslesmedi")
    if parsed["counter"] <= stored_counter:
        raise ValueError(f"counter artmadi (replay?): {parsed['counter']} <= {stored_counter}")
    return parsed["counter"]


# --------------------------------------------------------------------------- depolama
async def save_key(key_id: str, public_key_der: bytes, environment: str,
                   user_id: str | None = None) -> bool:
    if not is_configured():
        return False
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{SUPABASE_URL}/rest/v1/attest_keys",
                             headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
                             json={"key_id": key_id,
                                   "public_key": "\\x" + public_key_der.hex(),
                                   "bundle_id": APP_BUNDLE_ID,
                                   "environment": environment,
                                   "user_id": user_id},
                             timeout=10)
            return r.status_code < 300
    except Exception as e:
        logger.warning(f"attest key kaydedilemedi: {e}")
        return False


async def get_key(key_id: str) -> dict | None:
    if not is_configured():
        return None
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{SUPABASE_URL}/rest/v1/attest_keys", headers=_headers(),
                            params={"key_id": f"eq.{key_id}",
                                    "select": "key_id,public_key,sign_count,revoked"},
                            timeout=10)
            rows = r.json() if r.status_code == 200 else []
            if not rows or rows[0].get("revoked"):
                return None
            row = rows[0]
            pk = row["public_key"]
            row["public_key"] = bytes.fromhex(pk[2:] if pk.startswith("\\x") else pk)
            return row
    except Exception as e:
        logger.warning(f"attest key okunamadi: {e}")
        return None


async def bump_counter(key_id: str, counter: int) -> None:
    if not is_configured():
        return
    try:
        async with httpx.AsyncClient() as c:
            await c.patch(f"{SUPABASE_URL}/rest/v1/attest_keys", headers=_headers(),
                          params={"key_id": f"eq.{key_id}"},
                          json={"sign_count": counter,
                                "last_used_at": datetime.now(timezone.utc).isoformat()},
                          timeout=10)
    except Exception as e:
        logger.debug(f"counter guncellenemedi: {e}")


# --------------------------------------------------------------------------- istek dogrulama
async def check_request(http_request) -> tuple[bool, str]:
    """Istegin gecerli bir App Attest assertion'i tasiyip tasimadigini soyler.
    (dogrulandi_mi, sebep) doner. Faz 1'de yalnizca LOGLAMA icin kullanilir."""
    key_id = http_request.headers.get("x-attest-key-id", "")
    assertion = http_request.headers.get("x-attest-assertion", "")
    client_data = http_request.headers.get("x-attest-client-data", "")
    if not (key_id and assertion and client_data):
        return False, "baslik yok"

    rec = await get_key(key_id)
    if not rec:
        return False, "anahtar kayitli degil"
    try:
        new_counter = verify_assertion(assertion, client_data, rec["public_key"],
                                       int(rec.get("sign_count") or 0))
    except Exception as e:
        return False, f"assertion gecersiz: {str(e)[:80]}"
    await bump_counter(key_id, new_counter)
    return True, "ok"
