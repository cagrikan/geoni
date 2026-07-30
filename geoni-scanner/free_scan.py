"""
Ucretsiz-tarama tavani — MAX GUVENLIK: cihaz VE hesap katmani birlikte.

"Giris yapsa da yapmasa da, para vermeden en fazla FREE_SCAN_LIMIT tarama."
Her ucretsiz tarama gercek API $ yakar; bu yuzden cap sizmamali:
  - CIHAZ katmani (devicecheck.py, Apple 2-bit): anonimi de kapsar, reinstall/
    wipe-proof. Cok-hesap acmayi ENGELLER (ayni telefon = ayni sayac).
  - HESAP katmani (profiles.free_scans_used): girisliyse ek kisit; cok-cihaz
    kullanmayi ENGELLER (ayni hesap = ayni sayac).
  - PREMIUM (kredi almis / admin): tavan yok.

Karar: premium → izin. Aksi halde cihaz<LIMIT VE hesap<LIMIT → izin; degilse blok.
Belirsizlik (DeviceCheck env yok / Apple hatasi) GUVENLI TARAFA duser: o katman
atlanir, diger katman + IP rate-limit korur; canli akis bloklanmaz.

Kullanim:
    allowed, info = await free_scan_gate(user_id, device_token)
    if not allowed: -> 402 {"error": "free_limit_reached", ...}
    ... tarama BASARIYLA bitince:
    await record_free_scan(user_id, device_token, info)
"""
from __future__ import annotations

import logging

import os

from db import (
    check_is_premium,
    count_scans_this_month,
    get_credit_balance,
    get_free_scans_used,
    increment_free_scans,
    is_barter_creator,
)
from devicecheck import (
    MAX_FREE_SCANS as FREE_SCAN_LIMIT,
    mark_device_used,
    query_device_state,
)

logger = logging.getLogger(__name__)

# Barter creator'a isbirligi.html'de vaat edilen aylik tarama hakki.
CREATOR_MONTHLY_SCANS = int(os.environ.get("CREATOR_MONTHLY_SCANS", "30"))


async def free_scan_gate(user_id: str | None, device_token: str | None,
                         scan_cost: int = 0) -> tuple[bool, dict]:
    """(allowed, info) döner. info, record_free_scan icin device_state tasir.

    `scan_cost`: bu taramanin DUSECEGI token (web 5, kisi/marka 10, sosyal 0).
    Bakiye bunu karsiliyorsa tarama UCRETSIZ DEGILDIR -> tavan uygulanmaz.
    """
    # Premium/kredi almis kullanici: tavan yok.
    if user_id and await check_is_premium(user_id):
        return True, {"reason": "premium", "device_state": None}

    # ODENEN tarama ucretsiz tarama degildir.
    #
    # NEDEN (2026-07-30): kapi yalnizca `check_is_premium`e bakiyordu, o da
    # SADECE `total_credits_purchased > 0`. Hediye/referral tokenu olan (ama
    # para odememis) kullanicinin ilk taramasi hem 5 token yakiyor HEM omurluk
    # ucretsiz hakkini harciyordu; ikinci taramada cebinde token varken
    # `free_limit_reached` yiyordu. Referral odulu tam da bu yuzden dogdugu anda
    # olu kaliyordu: davet -> ikisine de 10 token -> davetli o tokeni HIC
    # harcayamiyor.
    #
    # Kurucunun "herkese sadece 1 ucretsiz tarama" kurali UCRETSIZ taramayla
    # ilgilidir; token dusen tarama ucretsiz degildir. Sosyal tarama (cost=0,
    # bkz. save_brand_check deduct=not social) ve anonim tarama (user_id yok)
    # gercekten bedava oldugu icin tavan orada AYNEN sürer.
    #
    # Bakiye submit'te bakilip dusum sonda yapiliyor; arada esZAMANLI baska bir
    # tarama bakiyeyi tuketirse dusum basarisiz olur ve save_audit maliyeti
    # sifirlar (_maliyeti_sifirla) -- bu yaris ONCEDEN DE vardi, burasi onu
    # buyutmuyor: aksine submit'te bakiye SARTI ekleyerek daraltiyor.
    if user_id and scan_cost > 0 and await get_credit_balance(user_id) >= scan_cost:
        return True, {"reason": "paid", "device_state": None}

    # Barter creator: cihaz/hesap tavani yerine AYLIK kota. Cihaz katmanindan
    # once bakilir — yoksa tek bitlik cihaz hakki creator'i ilk taramada keserdi.
    if user_id and await is_barter_creator(user_id):
        kullanilan = await count_scans_this_month(user_id)
        if kullanilan is None:
            # Sayilamadi → GUVENLI TARAF: kota dolmus say. Aksi halde sayim
            # hatasi "sinirsiz ucretsiz tarama"ya donusurdu (her biri gercek $).
            logger.warning("creator aylik sayim yapilamadi → kota dolmus sayildi")
            return False, {"reason": "free_limit_reached", "limit": CREATOR_MONTHLY_SCANS,
                           "creator": True, "device_over": False, "account_over": False,
                           "device_state": None, "account_used": 0}
        if kullanilan < CREATOR_MONTHLY_SCANS:
            return True, {"reason": "creator", "device_state": None,
                          "creator_used": kullanilan, "limit": CREATOR_MONTHLY_SCANS}
        return False, {"reason": "free_limit_reached", "limit": CREATOR_MONTHLY_SCANS,
                       "creator": True, "device_over": False, "account_over": False,
                       "device_state": None, "account_used": kullanilan}

    # Cihaz katmani (None → sorgulanamadi → bu katman atlanir).
    # Ekip bit'i daktilo ile paylasildigi icin cihaz katmani tek bit = tek hak;
    # FREE_SCAN_LIMIT yalnizca hesap katmanini yonetir (bkz. devicecheck.py basligi).
    device_state = await query_device_state(device_token) if device_token else None
    device_over = device_state is not None and device_state["used"]

    # Hesap katmani (anonimde user_id yok → 0 → atlanir).
    account_used = await get_free_scans_used(user_id) if user_id else 0
    account_over = account_used >= FREE_SCAN_LIMIT

    if device_over or account_over:
        return False, {
            "reason": "free_limit_reached",
            "limit": FREE_SCAN_LIMIT,
            "device_over": device_over,
            "account_over": account_over,
            "device_state": device_state,
            "account_used": account_used,
        }
    return True, {"reason": "ok", "device_state": device_state, "account_used": account_used}


async def record_free_scan(user_id: str | None, device_token: str | None, info: dict) -> None:
    """Basarili ucretsiz taramadan SONRA iki sayaci da +1. Premium'da no-op.

    Creator'da da no-op: aylik kota audits'ten TUREV sayiliyor, tutulacak sayac
    yok. Omurluk ucretsiz sayacini artirmak da yanlis olurdu — creator kotasi
    bittiginde ayrica "omurluk hakkini da kullandin" demis olurduk.

    `paid`de de no-op: token dusen tarama ucretsiz hakki YAKMAZ (bkz. gate)."""
    if info.get("reason") in ("premium", "creator", "paid"):
        return
    if user_id:
        await increment_free_scans(user_id)
    device_state = info.get("device_state")
    if device_token and device_state is not None:
        # daktilo'nun biti `device_state["other"]`ten aynen geri yazilir.
        await mark_device_used(device_token, device_state)
