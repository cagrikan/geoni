"""Cloudflare Turnstile anti-abuse dogrulamasi — main.py'den BAGIMSIZ modul
(fastapi/playwright CEKMEZ) ki testler minimal ortamda (yalniz httpx) kosabilsin.
main.py'deki enforce_turnstile bunu sarar ve blok durumunda HTTPException firlatir.

SOFT ROLLOUT: TURNSTILE_SECRET yoksa dogrulama kapali (True). Token varsa dogrula,
FAIL ise blok. Token yoksa soft-allow (TURNSTILE_ENFORCE=1 + secret varsa blok).
Ag/parse hatasi -> soft-allow (abuse korumasi tarama pipeline'ini ASLA dusurmez).
"""
import logging
import os
import time
from collections import defaultdict

import httpx

logger = logging.getLogger(__name__)
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# AKILLI ENFORCE (Fable 2026-07-23): token-siz istek YUMUSAK gecerse (eski davranis)
# script abuse sinirsiz ucretsiz tarama yaptirabiliyordu. Sert enforce ise widget
# yuklenemeyen/mobil GERCEK kullaniciyi da engeller. Denge: IP-bazli GRACE — bir IP
# kisa surede ILK birkac token-siz istege izin (mesru kullanici + widget-hatasi
# toleransi), grace asilinca turnstile ZORUNLU (token'li gercek kullanici HIC etkilenmez;
# yalniz token-siz tekrar-istek zincirleri = abuse imzasi bloklanir).
_NOTOKEN_GRACE = int(os.environ.get("TURNSTILE_NOTOKEN_GRACE", "2"))
_NOTOKEN_WINDOW = int(os.environ.get("TURNSTILE_NOTOKEN_WINDOW", "3600"))  # 1 saat
_notoken_hits: dict[str, list[float]] = defaultdict(list)


def _notoken_over_grace(ip: str) -> bool:
    """Bu IP token-siz GRACE'ini astiysa True (artik turnstile zorunlu). Sliding-window,
    yalniz token-SIZ isteklerde artar; token'li istekler bu sayaca HIC dokunmaz."""
    if not ip:
        return False
    now = time.monotonic()
    hits = [t for t in _notoken_hits.get(ip, []) if t > now - _NOTOKEN_WINDOW]
    hits.append(now)
    _notoken_hits[ip] = hits
    if len(_notoken_hits) > 20000:      # kaba bellek korumasi (unbounded buyume onle)
        _notoken_hits.clear()
    return len(hits) > _NOTOKEN_GRACE


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
    # Token YOK. Secret varsa akilli-enforce: ENFORCE=1 -> hep blok; degilse IP-bazli GRACE
    # (ilk birkac token-siz istek gecer, sonra turnstile ZORUNLU). Secret yoksa (dev) gecer.
    if turnstile_secret():
        if turnstile_enforce() or _notoken_over_grace(ip):
            logger.info(f"turnstile: token yok + (enforce/grace-asildi), blok (endpoint={endpoint}, ip={ip})")
            return True, turnstile_fail_message(lang)
        logger.info(f"turnstile: token yok, grace-ici soft-allow (endpoint={endpoint}, ip={ip})")
        return False, None
    return False, None
