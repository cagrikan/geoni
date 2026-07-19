"""Cloudflare Turnstile anti-abuse dogrulamasi — main.py'den BAGIMSIZ modul
(fastapi/playwright CEKMEZ) ki testler minimal ortamda (yalniz httpx) kosabilsin.
main.py'deki enforce_turnstile bunu sarar ve blok durumunda HTTPException firlatir.

SOFT ROLLOUT: TURNSTILE_SECRET yoksa dogrulama kapali (True). Token varsa dogrula,
FAIL ise blok. Token yoksa soft-allow (TURNSTILE_ENFORCE=1 + secret varsa blok).
Ag/parse hatasi -> soft-allow (abuse korumasi tarama pipeline'ini ASLA dusurmez).
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_secret() -> str:
    return os.environ.get("TURNSTILE_SECRET", "")


def turnstile_enforce() -> bool:
    return os.environ.get("TURNSTILE_ENFORCE", "").strip().lower() in ("1", "true", "yes", "on")


def turnstile_fail_message(lang: str) -> str:
    if lang == "en":
        return "Verification failed. Please refresh the page and try again."
    return "Doğrulama başarısız. Lütfen sayfayı yenileyip tekrar deneyin."


async def verify_turnstile(token: str, ip: str = "") -> bool:
    """Donus True = gecerli ya da soft-allow. Secret yoksa True (dogrulama kapali).
    Token bos: False (cagiran soft-allow/enforce karar verir). siteverify 'success'
    beklenir. Ag/parse hatasi: True (soft-allow, loglanir)."""
    secret = turnstile_secret()
    if not secret:
        return True
    if not token:
        return False
    data = {"secret": secret, "response": token}
    if ip and ip != "unknown":
        data["remoteip"] = ip
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(TURNSTILE_VERIFY_URL, data=data)
            result = resp.json()
            success = bool(result.get("success"))
            if not success:
                logger.info(f"turnstile: dogrulama basarisiz codes={result.get('error-codes')}")
            return success
    except Exception as e:
        logger.warning(f"turnstile: siteverify ag/parse hatasi, soft-allow ({e})")
        return True


async def check_turnstile(token: str | None, ip: str, lang: str, endpoint: str) -> tuple[bool, str | None]:
    """Doner (blocked, message). blocked=True ise cagiran 403 firlatmali.
    - Token VARSA dogrula; FAIL -> (True, mesaj).
    - Token YOKSA soft-allow; TURNSTILE_ENFORCE=1 + secret varsa (True, mesaj).
    - Secret yoksa verify zaten True (dev/lokal gecer)."""
    tok = (token or "").strip()
    if tok:
        ok = await verify_turnstile(tok, ip)
        if not ok:
            logger.info(f"turnstile: token gecersiz, blok (endpoint={endpoint}, ip={ip})")
            return True, turnstile_fail_message(lang)
        return False, None
    if turnstile_enforce() and turnstile_secret():
        logger.info(f"turnstile: token yok + ENFORCE, blok (endpoint={endpoint}, ip={ip})")
        return True, turnstile_fail_message(lang)
    logger.info(f"turnstile: token yok, soft-allow (endpoint={endpoint}, ip={ip})")
    return False, None
