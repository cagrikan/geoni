"""
GEONI - Supabase database integration
Saves audit results and brand check results to Supabase.
Uses service role key to bypass RLS.
"""

import asyncio
import base64
import json
import os
import re
import secrets
import time
import uuid
import html
import urllib.parse
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs, quote
import httpx

from scan_costs import WEB_SCAN_COST, BRAND_SCAN_COST, SOCIAL_SCAN_COST

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


# Vitrindeki sayaca eklenen gosterim tabani: lansman oncesi test/beta
# donemindeki dis taramalari da temsil eder; gercek kayitlar tabana
# eklenerek buyumeye devam eder (kullanici karari, 2026-07-09).
SCAN_COUNT_DISPLAY_BASE = int(os.environ.get("SCAN_COUNT_DISPLAY_BASE", "1200"))

# Referral odulu (kontor). KURAL: odul = TAM BIR TARAMA. Onceki deger 1'di,
# aciklamasi "+1 tarama" diyordu ama vaadin 1/10'u odeniyordu (kurucu karari
# 2026-07-25 -> 10). 2026-08-03'te tarama bedeli 20'ye cikinca odul de 20 oldu:
# aksi halde davetli 10 tokenla HICBIR SEY yapamaz ve odul dogdugu anda olur —
# bu tam olarak [[geoni-ucretsiz-kapi-token-catismasi]]'nda yasanan hataydi.
# Bedel degisirse bu sayi da degismeli; ikisi ayni kurala baglidir.
REFERRAL_REWARD_CREDITS = int(os.environ.get("REFERRAL_REWARD_CREDITS", "20"))

# Token'in "referans" USD degeri: 1000'lik paketin kuru ($79.99/1000).
# YALNIZ RAPORLAMA — muhasebe defterinde bir isin liste degerini gostermek icin.
# Uzman odemesi bundan TUREMEZ; odeme hizmet basina sabit ucrettir
# (ticket_types.expert_payout_usd). Paketler arasi birim fiyat $0.0999-$0.0600
# arasinda degistigi icin "liste fiyati"nin tek dogru degeri yok; bu yuzden
# odemeyi ona bagli birakmadik.
TOKEN_REFERENCE_USD = float(os.environ.get("TOKEN_REFERENCE_USD", "0.08"))


async def get_total_scan_count() -> int:
    """Public aggregate count for the landing page social-proof counter (Madde 3.1)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return SCAN_COUNT_DISPLAY_BASE
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=id",
                headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"},
                timeout=10,
            )
            content_range = r.headers.get("content-range", "")
            total = content_range.split("/")[-1] if "/" in content_range else ""
            real = int(total) if total.isdigit() else 0
            return SCAN_COUNT_DISPLAY_BASE + real
    except Exception as e:
        logger.warning(f"get_total_scan_count failed: {e}")
        return SCAN_COUNT_DISPLAY_BASE


async def create_pending_audit(job_id: str, audit_type: str, domain: str, user_id: str = None) -> bool:
    """SQS modu: is kuyruga yazilmadan ONCE 'queued' satiri olusur, boylece
    status endpoint'i API process'inin belleginden bagimsiz her zaman cevap
    verebilir. Basarisizsa False doner — cagiran is'i kuyruga YAZMASIN."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/audits",
                headers=_headers(),
                json={"id": job_id, "user_id": user_id, "type": audit_type,
                      "domain": domain, "status": "queued", "credits_spent": 0},
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"create_pending_audit failed: {e}")
        return False


async def update_audit_status(job_id: str, status: str, result: dict = None, score=None) -> None:
    """Kosan isin durumunu audits satirina isler (SQS modunda). Sessizce
    loglar — durum guncellemesi taramayi asla dusurmemeli."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    patch: dict = {"status": status}
    if result is not None:
        patch["result_json"] = result
    if score is not None:
        patch["score"] = score
    if status in ("complete", "failed"):
        patch["completed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/audits?id=eq.{job_id}",
                headers=_headers(), json=patch, timeout=10,
            )
    except Exception as e:
        logger.warning(f"update_audit_status({job_id},{status}) failed: {e}")


async def get_audit_row(job_id: str) -> dict | None:
    """Status endpoint'inin DB fallback'i: satiri getirir, yoksa None."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?id=eq.{job_id}&select=id,status,result_json,created_at",
                headers=_headers(), timeout=10,
            )
            rows = r.json() if r.status_code == 200 else []
            return rows[0] if rows else None
    except Exception as e:
        logger.warning(f"get_audit_row({job_id}) failed: {e}")
        return None


async def _maliyeti_sifirla(client, job_id: str, user_id: str, amount: int, ne: str):
    """Dusum BASARISIZ olduysa satirdaki maliyeti gercege cek (0).

    Satir dusumden ONCE yaziliyor ve bu sira bilincli: once dusup sonra yazsak,
    yazma hatasinda kullanici hem kredisini hem raporunu kaybederdi. Bunun
    bedeli, dusum patlarsa satirin "odendi" demesi - defterde karsiligi olmayan
    hayali maliyet (bkz. 2026-07-12'deki 3 satir). O yuzden dusum sonucuna gore
    satiri DUZELTIYORUZ.

    ERROR seviyesinde loglanir: Sentry'de gorunmesi gerekir, cunku bu noktaya
    gelinmesi ya bakiye yaris kosulu ya da DB hatasi demektir."""
    logger.error(
        "kontor dusumu BASARISIZ (%s): job=%s user=%s amount=%s - satirdaki "
        "maliyet 0'a cekiliyor", ne, job_id, user_id, amount,
    )
    try:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/audits",
            headers={**_headers(), "Prefer": "return=minimal"},
            params={"id": f"eq.{job_id}"},
            json={"credits_spent": 0},
            timeout=10,
        )
        if r.status_code not in (200, 204):
            logger.error(
                "maliyet sifirlama da basarisiz: job=%s %s %s",
                job_id, r.status_code, r.text[:200],
            )
    except Exception as e:
        logger.error("maliyet sifirlama hatasi: job=%s %s", job_id, e)


async def save_audit(job_id: str, request_data: dict, result: dict, user_id: str = None, deduct: bool = True) -> bool:
    """Save domain audit result to Supabase audits table.
    deduct=False: otomatik izleme taramalari kontor dusmez (izleme ucretsiz)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("Supabase not configured, skipping audit save")
        return False

    payload = {
        "id": job_id,
        "user_id": user_id,
        "type": "web",
        "domain": request_data.get("domain"),
        "score": result.get("score"),
        "result_json": result,
        # Gercek dusumle ayni olmali (asagida ayni sabitle dusuluyor) -
        # eskiden 10 yaziliyordu, kullanici gercekte harcadigindan fazlasini goruyordu.
        # user_id kosulu da SART: anonim/ucretsiz taramada dusum yapilmiyor
        # (asagida `if user_id and deduct`), ama satira 5 yaziliyordu -> admin
        # raporu odenmemis krediyi harcanmis sayiyordu.
        "credits_spent": WEB_SCAN_COST if (deduct and user_id) else 0,
        "status": "complete",
        "completed_at": result.get("created_at"),
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/audits",
                # SQS modunda ayni id'li 'queued' satiri onceden var — upsert.
                headers={**_headers(), "Prefer": "return=minimal,resolution=merge-duplicates"},
                json=payload,
                timeout=10,
            )
            if r.status_code in (200, 201):
                logger.info(f"Audit {job_id} saved to Supabase")
                # Deduct credits if user is logged in
                if user_id and deduct:
                    # Donus DEGERI kontrol edilir: False donerse satir "5 kredi
                    # harcandi" demeye devam edemez.
                    if not await deduct_credits(user_id, WEB_SCAN_COST, "web_audit", job_id):
                        await _maliyeti_sifirla(client, job_id, user_id, WEB_SCAN_COST, "web_audit")
                # (C) Ayni hedefin ESKI tam raporunu hemen sadelestir (skor kalir).
                if user_id:
                    asyncio.create_task(run_audit_retention(user_id))
                    # Viral cekirdek Faz 2: davetli tarama tamamlayinca +1/+1 (idempotent, tek sefer).
                    asyncio.create_task(grant_referral_reward(user_id))
                return True
            logger.warning(f"Supabase audit save failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Supabase audit save error: {e}")
    return False


async def save_brand_check(job_id: str, request_data: dict, result: dict, user_id: str = None,
                           deduct: bool = True, started_at: str = None) -> bool:
    """Save brand check result to Supabase audits table.
    deduct=False: otomatik izleme taramalari kontor dusmez (izleme ucretsiz).

    started_at: isin GERCEK baslangici (ISO). Bu satir isin SONUNDA olustugu
    icin created_at'in DB varsayilanina (now()) birakilmasi bitis anini yaziyor
    ve `completed_at - created_at` NEGATIF cikiyordu -- yani kisi/marka/sosyal
    taramalarda sure hic olculemiyordu (2026-07-29: social 54/54, person 20/20,
    brand 2/2 negatif). Verilmezse eski davranis korunur (geriye donuk uyumlu).
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("Supabase not configured, skipping brand check save")
        return False

    entity_type = request_data.get("type", "person")
    # save_audit ile ayni kural: dusum `if user_id and deduct` ile yapiliyor,
    # yazilan maliyet de ayni kosula bagli olmali (anonim taramada 0).
    # 2026-08-03: sosyal tarama artik ucretsiz DEGIL (SOCIAL_SCAN_COST=10, yari
    # fiyat). Bedel tipe gore secilir; tek yerde durur ki `credits_spent` ile
    # fiilen dusulen tutar ayrisamasin (scan_costs.py basligindaki hata bicimi).
    _bedel = SOCIAL_SCAN_COST if entity_type == "social" else BRAND_SCAN_COST
    credits = _bedel if (deduct and user_id) else 0

    payload = {
        "id": job_id,
        "user_id": user_id,
        "type": entity_type,
        "name": request_data.get("name"),
        "role": request_data.get("role"),
        "company": request_data.get("company"),
        "location": request_data.get("location"),
        "topic": request_data.get("topic"),
        "linkedin_url": request_data.get("linkedin_url"),
        "score": result.get("score"),
        "result_json": result,
        "credits_spent": credits,
        "status": "complete",
        "completed_at": result.get("created_at"),
    }
    if started_at:
        payload["created_at"] = started_at

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/audits",
                headers=_headers(),
                json=payload,
                timeout=10,
            )
            if r.status_code in (200, 201):
                logger.info(f"Brand check {job_id} saved to Supabase")
                if user_id and deduct:
                    if not await deduct_credits(user_id, credits, f"{entity_type}_check", job_id):
                        await _maliyeti_sifirla(client, job_id, user_id, credits, f"{entity_type}_check")
                # (C) Ayni kisi/marka'nin ESKI tam raporunu hemen sadelestir.
                if user_id:
                    asyncio.create_task(run_audit_retention(user_id))
                    # Viral cekirdek Faz 2: davetli tarama tamamlayinca +1/+1 (idempotent, tek sefer).
                    asyncio.create_task(grant_referral_reward(user_id))
                return True
            logger.warning(f"Supabase brand check save failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Supabase brand check save error: {e}")
    return False


def _norm_topic(t) -> str:
    return (t or "").strip().casefold()


def _cached_row_matches(res: dict, lang: str, topic: str, scoring_version: str | None) -> bool:
    """Cache satiri (result_json) istekle eslesir mi — TEK KAYNAK anahtar kontrolu.
    Saf/senkron ki golden test edilebilsin (tests/test_cache_key.py). Fable 2026-07-19:
    - F-K2: skorlama surumu esitse (v5 eski v4 cache'i gecersiz kilar).
    - F-K1: lang ACIK+TAM eslesmeli; lang'siz legacy satir hicbir dile eslesmez.
    - F-K3: nis/topic eslesmeli; ayni handle farkli nisle farkli skor uretir."""
    if scoring_version is not None and res.get("scoring_version") != scoring_version:
        return False
    if res.get("lang") != lang:
        return False
    if _norm_topic(res.get("topic")) != _norm_topic(topic):
        return False
    return True


async def get_recent_cached_brand(name: str, entity_type: str, lang: str,
                                  topic: str = "", scoring_version: str | None = None,
                                  max_age_hours: int = 24) -> dict | None:
    """A2-1 (QA 2026-07-19): 24h idempotent tarama cache'i. Cache ANAHTARI:
    (ad, tip, dil, nis/topic, skorlama_surumu) — son `max_age_hours` icinde
    TAMAMLANMIS eslesme varsa result_json'unu doner, yoksa None. Determinizm +
    LLM maliyeti tasarrufu. Cagiran: private/custom_queries/force'ta CAGIRMAZ.
    Anahtarin her parcasi ZORUNLU (2026-07-19 Fable bulgulari):
    - lang TAM+ACIK eslesmeli; lang'siz legacy satir HICBIR dile eslesmez (K1).
    - scoring_version eslesmeli; surum degisince (or. v5) eski cache gecersiz (K2).
    - topic/nis eslesmeli; ayni handle farkli nisle farkli skor uretir (K3).
    Bu alanlar build_brand_payload ile result_json'a gomuludur (audits'te lang
    kolonu yok; topic kolonu var ama tutarlilik icin payload'dan okunur)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    nm = (name or "").strip()
    if not nm:
        return None
    try:
        from datetime import timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits",
                headers=_headers(),
                params={"name": f"eq.{nm}", "type": f"eq.{entity_type}",
                        "status": "eq.complete", "select": "result_json,completed_at",
                        "order": "completed_at.desc", "limit": "5"},
                timeout=8,
            )
            if r.status_code != 200:
                return None
            for row in r.json():
                res = row.get("result_json") or {}
                if not _cached_row_matches(res, lang, topic, scoring_version):
                    continue
                ca = row.get("completed_at")
                if not ca:
                    continue
                try:
                    ts = datetime.fromisoformat(ca.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if ts >= cutoff:
                    return res
    except Exception as e:
        logger.info(f"get_recent_cached_brand skip: {e}")
    return None


async def get_pinned_sov_queries(name: str, entity_type: str, lang: str, topic: str) -> list | None:
    """F-Y1 determinizm (2026-07-19, Fable re-test): SOV sorgu setini (ad, tip, dil,
    nis/topic) anahtarli SON complete audit'ten yeniden kullanir. Boylece ayni hedef
    HER koşuda AYNI sorgulari alir -> mention pattern koşu-arasi savrulmaz, SOV skoru
    deterministik olur. Sorgu = olcum ALETI (metodoloji), olcum degil:
    - skorlama surumunden BAGIMSIZ (v5 audit'inin sorgulari v6 taramada kullanilabilir),
    - YAS siniri YOK (nis/kategori yavas degisir; force=true bile AYNI aletle olcer).
    Sorgu seti yenileme AYRI/bilincli eylem (topic degisimi -> anahtar degisir -> otomatik).
    Donus: [{query, adjacent, topic}, ...] ya da None (eslesen audit/sorgu yoksa)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    nm = (name or "").strip()
    if not nm:
        return None
    nt = _norm_topic(topic)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits",
                headers=_headers(),
                params={"name": f"eq.{nm}", "type": f"eq.{entity_type}",
                        "status": "eq.complete", "select": "result_json",
                        "order": "completed_at.desc", "limit": "10"},
                timeout=8,
            )
            if r.status_code != 200:
                return None
            for row in r.json():
                res = row.get("result_json") or {}
                if res.get("lang") != lang or _norm_topic(res.get("topic")) != nt:
                    continue
                qs = ((res.get("sov") or {}).get("queries")) or []
                pinned = [{"query": q.get("query"), "adjacent": bool(q.get("adjacent")),
                           "topic": q.get("adjacent_topic") or topic}
                          for q in qs if q.get("query")]
                if pinned:
                    return pinned
    except Exception as e:
        logger.info(f"get_pinned_sov_queries skip: {e}")
    return None


async def deduct_credits(user_id: str, amount: int, description: str, reference_id: str = None) -> bool:
    """Kontor duser ve defter satirini yazar. TEK transaction, atomik.

    🔴 Neden tek RPC: eski surum uc ayri HTTP cagrisi yapiyordu (defterde var mi
    SELECT -> bakiyeyi dus RPC -> defter satirini yaz POST). Bu bir
    check-then-act desenidir ve DB'deki kismi UNIQUE yalnizca IKINCI DEFTER
    SATIRINI engelliyor, ikinci BAKIYE DUSUMUNU degil. Iki es zamanli cagri
    (SQS yeniden teslimi, heartbeat gecikmesi) ilk SELECT'te ikisi de bos
    goruyor, bakiye IKI KEZ dusuyor, defterde tek satir kaliyor ve iki cagri da
    True donuyordu. 2026-07-29 denetiminde calistirilarak uretildi:
    100 -> 90 (5 yerine 10), defterde 1 satir.

    `spend_credits_atomic` defter satirini ONCE yaziyor; ikinci kez
    ucretlendirme uygulama kodunda degil DB kisitinda duruyor, yani yaris
    penceresi yok. Bakiye yetmezse defter satiri geri aliniyor.

    Donus: True = bu is icin ucretlendirme GECERLI (yeni dusuldu ya da zaten
    dusulmustu). False = dusulmedi; cagiran maliyeti 0 yazmali.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/spend_credits_atomic",
                headers=_headers(),
                json={
                    "p_user_id": user_id,
                    "p_amount": amount,
                    "p_description": description,
                    "p_reference_id": reference_id,
                },
                timeout=10,
            )
            if r.status_code != 200:
                logger.error(
                    "spend_credits_atomic HATA: user=%s amount=%s ref=%s %s %s",
                    user_id, amount, reference_id, r.status_code, r.text[:200],
                )
                return False

            sonuc = r.json() or {}
            if sonuc.get("applied"):
                logger.info(f"Deducted {amount} credits from user {user_id}")
                return True

            neden = sonuc.get("reason")
            if neden == "duplicate":
                # Ayni is zaten ucretlendirilmis (SQS yeniden teslimi). Bakiyeye
                # dokunulmadi ve DOKUNULMAMALI - ama is ucretli sayilir.
                logger.info(
                    "deduct_credits idempotent no-op: reference_id=%s zaten dusuldu",
                    reference_id,
                )
                return True

            logger.warning(
                "Kontor dusulemedi (%s): user=%s amount=%s", neden, user_id, amount,
            )
            return False
    except Exception as e:
        logger.warning(f"Credit deduction error: {e}")
    return False


_token_cache: dict[str, tuple[str | None, float]] = {}
_TOKEN_CACHE_TTL = 30.0  # seconds


async def get_user_id_from_token(token: str) -> str | None:
    """Validate Supabase JWT token and return user ID. The admin panel opens
    with a burst of ~10-15 parallel requests (one per widget), each calling
    this with the SAME token - without caching, that's 10-15 concurrent hits
    to Supabase's /auth/v1/user, which under load turned single-digit-ms
    checks into multi-second ones (contention, not raw latency). A short
    cache collapses the burst into one real validation."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not token:
        return None
    cached = _token_cache.get(token)
    if cached and time.monotonic() - cached[1] < _TOKEN_CACHE_TTL:
        return cached[0]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {token}",
                },
                timeout=10,
            )
            if r.status_code == 200:
                user_id = r.json().get("id")
                _token_cache[token] = (user_id, time.monotonic())
                return user_id
    except Exception as e:
        logger.warning(f"Token validation error: {e}")
    return None


async def check_is_premium(user_id: str) -> bool:
    """Check if user is admin or has purchased credits (premium)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_admin,total_credits_purchased",
                headers=_headers(),
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    return data[0].get('is_admin', False) or data[0].get('total_credits_purchased', 0) > 0
    except Exception as e:
        logger.warning(f"Premium check failed: {e}")
    return False


# ── Ucretsiz-tarama sayaci (HESAP katmani) ──────────────────────────────────
# Cihaz katmani devicecheck.py'de (Apple, wipe-proof). Hesap katmani burada:
# profiles.free_scans_used. Ikisi de <FREE_SCAN_LIMIT olmali (max guvenlik).
# Migration (bir kez, Supabase SQL):
#   alter table profiles add column if not exists free_scans_used int not null default 0;
#   create or replace function increment_free_scans(uid uuid) returns int as $$
#     update profiles set free_scans_used = coalesce(free_scans_used,0)+1
#     where id = uid returning free_scans_used;
#   $$ language sql;

async def get_free_scans_used(user_id: str) -> int:
    """Hesabin simdiye kadar yaptigi ucretsiz tarama sayisi (yoksa 0)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=free_scans_used",
                headers=_headers(),
                timeout=8,
            )
            if r.status_code == 200 and r.json():
                return int(r.json()[0].get("free_scans_used") or 0)
    except Exception as e:
        logger.warning(f"get_free_scans_used failed: {e}")
    return 0


async def increment_free_scans(user_id: str) -> int | None:
    """Hesabin ucretsiz-tarama sayacini ATOMIK +1 (RPC). Yeni degeri döner.
    RPC yoksa/hata → None (cagiran soft-basarisiz sayar; cihaz katmani korur)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/increment_free_scans",
                headers=_headers(),
                json={"uid": user_id},
                timeout=8,
            )
            if r.status_code in (200, 201):
                body = r.json()
                return int(body) if isinstance(body, int) else int(body[0]) if body else None
    except Exception as e:
        logger.warning(f"increment_free_scans failed: {e}")
    return None


# ── Creator (barter) aylik tarama kotasi ────────────────────────────────────
# isbirligi.html'de barter creator'a "ayda 30 tarama" vaat ediliyor (2026-07-28
# oncesi "sinirsiz" yaziyordu, kurucu karariyla indirildi). Kotayi AYRI SAYAC
# KOLONUNDA tutmuyoruz: aylik sayacin her ay sifirlanmasi gerekir, bu da cron +
# saat dilimi + kacan reset = kalici hata kaynagi. Bunun yerine audits'ten TUREV
# sayim yapiyoruz — sayac diye bir sey olmayinca drift de olmuyor.
# Indeks: audits(user_id, created_at desc) — migration creator_monthly_scan_index.

_creator_cache: dict[str, tuple[bool, float]] = {}


async def is_barter_creator(user_id: str) -> bool:
    """Kabul edilmis BARTER creator mi? (expert ortagina tarama kotasi vaat
    edilmiyor — ona is yonlendiriliyor, o yuzden yalnizca model='barter'.)"""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    cached = _creator_cache.get(user_id)
    if cached and time.monotonic() - cached[1] < _TOKEN_CACHE_TTL:
        return cached[0]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/creator_applications"
                f"?user_id=eq.{user_id}&status=eq.accepted&model=eq.barter&select=id&limit=1",
                headers=_headers(), timeout=8,
            )
            if r.status_code == 200:
                result = bool(r.json())
                _creator_cache[user_id] = (result, time.monotonic())
                return result
    except Exception as e:
        logger.warning(f"is_barter_creator failed: {e}")
    return False


async def count_scans_this_month(user_id: str) -> int | None:
    """Kullanicinin BU TAKVIM AYINDA baslattigi tarama sayisi.
    None → sayilamadi; cagiran GUVENLI TARAFA dusup kotayi dolmus saymali
    (aksi halde sayim hatasi sinirsiz ucretsiz tarama demek olurdu)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return None
    simdi = datetime.now(timezone.utc)
    ay_basi = simdi.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # DIKKAT: isoformat() "+00:00" uretir; sorgu dizesindeki "+" PostgREST'te
    # BOSLUGA cozulur -> 400 "invalid input syntax for type timestamp".
    # Canlida olculdu (2026-07-28). Z bicimi bu tuzagi tasimiyor.
    ay_str = ay_basi.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits"
                f"?user_id=eq.{user_id}&created_at=gte.{ay_str}&select=id&limit=1",
                headers={**_headers(), "Prefer": "count=exact"}, timeout=8,
            )
            if r.status_code in (200, 206):
                # PostgREST toplam sayiyi Content-Range'in payda kisminda doner: "0-0/42"
                rng = r.headers.get("content-range", "")
                if "/" in rng:
                    toplam = rng.rsplit("/", 1)[1]
                    if toplam.isdigit():
                        return int(toplam)
    except Exception as e:
        logger.warning(f"count_scans_this_month failed: {e}")
    return None


_is_admin_cache: dict[str, tuple[bool, float]] = {}


async def is_strict_admin(user_id: str) -> bool:
    """Strict is_admin check (unlike check_is_premium, does NOT pass for paying
    non-admin users). Used to gate the admin panel - cached briefly for the
    same reason as get_user_id_from_token (admin panel load fires this many
    times concurrently for the same user)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    cached = _is_admin_cache.get(user_id)
    if cached and time.monotonic() - cached[1] < _TOKEN_CACHE_TTL:
        return cached[0]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_admin",
                headers=_headers(),
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    result = bool(data[0].get('is_admin', False))
                    _is_admin_cache[user_id] = (result, time.monotonic())
                    return result
    except Exception as e:
        logger.warning(f"Admin check failed: {e}")
    return False


async def is_expert(user_id: str) -> bool:
    """Gates the expert ticket panel - separate from is_admin, since ticket
    experts shouldn't automatically get full admin panel access."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_expert",
                headers=_headers(),
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    return bool(data[0].get('is_expert', False))
    except Exception as e:
        logger.warning(f"Expert check failed: {e}")
    return False


async def log_provider_call(provider: str) -> None:
    """Fire-and-forget usage counter for external AI provider calls (admin panel 'motor kullanimi' tab).
    Requires the provider_usage table (see admin panel migration) - silently no-ops if missing."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/provider_usage",
                headers=_headers(),
                json={"provider": provider},
                timeout=5,
            )
    except Exception as e:
        logger.debug(f"Provider usage log skipped: {e}")


_auth_emails_cache = {"value": None, "fetched_at": None}
_AUTH_EMAILS_CACHE_TTL = timedelta(minutes=5)


async def _fetch_all_auth_emails(max_pages: int = 5, per_page: int = 200) -> dict:
    """id -> email map via Supabase GoTrue admin API (profiles table has no email
    column). This is up to 5 sequential paginated HTTP calls to Supabase - both
    admin_list_users and admin_list_audits called this on every single request
    (every keystroke in search, every sort click, every page turn), which is
    what made both admin panel tabs feel slow. Emails change rarely, so a short
    cache turns that into one real fetch every few minutes instead of every click."""
    now = datetime.now(timezone.utc)
    if _auth_emails_cache["value"] is not None and _auth_emails_cache["fetched_at"] and now - _auth_emails_cache["fetched_at"] < _AUTH_EMAILS_CACHE_TTL:
        return _auth_emails_cache["value"]
    emails = {}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return emails
    try:
        async with httpx.AsyncClient() as client:
            for page in range(1, max_pages + 1):
                r = await client.get(
                    f"{SUPABASE_URL}/auth/v1/admin/users?page={page}&per_page={per_page}",
                    headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                    timeout=10,
                )
                if r.status_code != 200:
                    break
                batch = r.json().get("users", [])
                for u in batch:
                    emails[u["id"]] = u.get("email", "")
                if len(batch) < per_page:
                    break
    except Exception as e:
        logger.warning(f"auth admin users fetch failed: {e}")
        return _auth_emails_cache["value"] or emails
    _auth_emails_cache["value"] = emails
    _auth_emails_cache["fetched_at"] = now
    return emails


async def _fetch_auth_user(user_id: str) -> dict | None:
    """Single-user GoTrue admin lookup - used for last_sign_in_at on the
    user detail card (not worth bulk-caching like the email map, since it's
    only fetched when an admin actually opens one user's detail view)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"_fetch_auth_user error: {e}")
    return None


async def _count(query: str) -> int:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/{query}",
                headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"},
                timeout=10,
            )
            cr = r.headers.get("content-range", "")
            total = cr.split("/")[-1] if "/" in cr else ""
            return int(total) if total.isdigit() else 0
    except Exception:
        return 0


async def _returning_users(activity_since: str, signup_before: str) -> int:
    """Users active (scanned) since `activity_since` whose profile predates
    `signup_before` (as opposed to a brand-new signup scanning for the first
    time). `signup_before` must be the SAME fixed cutoff (today_start) across
    both the "today" and "this week" calls - otherwise the two counts use
    different definitions of "new" and today's number can exceed the week's,
    which reads as a bug (today's activity is a subset of the week's, so its
    returning-count must be too)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=user_id&created_at=gte.{activity_since}&limit=5000",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200:
                return 0
            active_ids = {row["user_id"] for row in r.json() if row.get("user_id")}
            if not active_ids:
                return 0
            r2 = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=id,created_at&id=in.({','.join(active_ids)})",
                headers=_headers(), timeout=10,
            )
            if r2.status_code != 200:
                return 0
            return sum(1 for row in r2.json() if (row.get("created_at") or "") < signup_before)
    except Exception as e:
        logger.warning(f"returning_users error: {e}")
        return 0


async def get_admin_summary() -> dict:
    """Cheapest possible admin panel numbers, run concurrently so this
    endpoint answers fast while the heavier widgets (charts, external API
    calls) load independently on their own endpoints."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    (total_users, total_audits, new_users_today, returning_users_today,
     new_users_week, returning_users_week) = await asyncio.gather(
        _count("profiles?select=id"),
        _count("audits?select=id"),
        _count(f"profiles?select=id&created_at=gte.{today_start}"),
        _returning_users(today_start, today_start),
        _count(f"profiles?select=id&created_at=gte.{week_start}"),
        _returning_users(week_start, today_start),
    )
    return {
        "total_users": total_users,
        "total_audits": total_audits,
        "new_users_today": new_users_today,
        "returning_users_today": returning_users_today,
        "new_users_week": new_users_week,
        "returning_users_week": returning_users_week,
    }


async def get_admin_scans_daily(days: int = 14) -> dict:
    """Daily scan counts by type, for the overview chart. Also derives
    today/week totals from the same rows instead of firing separate counts."""
    empty = {"days": [], "today": 0, "week": 0}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return empty

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?select=created_at,type&created_at=gte.{since}&order=created_at.asc&limit=5000",
                headers=_headers(), timeout=15,
            )
            rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"admin_scans_daily error: {e}")
        return empty

    buckets = {}
    for row in rows:
        date_key = (row.get("created_at") or "")[:10]
        if not date_key:
            continue
        t = row.get("type") or "web"
        if t not in ("web", "person", "brand"):
            t = "web"
        buckets.setdefault(date_key, {"web": 0, "person": 0, "brand": 0})[t] += 1

    now = datetime.now(timezone.utc)
    ordered_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    series = [{"date": d, **buckets.get(d, {"web": 0, "person": 0, "brand": 0})} for d in ordered_days]

    today_key = now.strftime("%Y-%m-%d")
    week_since_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    today_total = sum(buckets.get(today_key, {}).values())
    week_total = sum(sum(v.values()) for k, v in buckets.items() if k >= week_since_key)

    return {"days": series, "today": today_total, "week": week_total}


async def get_admin_credits_stats(days: int = 14) -> dict:
    """Purchased/spent/gifted totals plus a daily granted-vs-spent trend and a
    breakdown of spend by reason (web/person/brand scan, admin adjustment).
    Admin users are internal/test accounts, not real customers - their
    activity is excluded from purchased/spent/gifted and the trend/reason
    breakdown, and reported separately (admin_spent) instead."""
    result = {"purchased": 0, "spent": 0, "gifted": 0, "admin_spent": 0, "daily": [], "by_reason": {}}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return result

    admin_ids = set()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=id,total_credits_purchased,total_credits_spent,total_credits_gifted,is_admin",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                for row in r.json():
                    if row.get("is_admin"):
                        admin_ids.add(row["id"])
                        result["admin_spent"] += row.get("total_credits_spent") or 0
                    else:
                        result["purchased"] += row.get("total_credits_purchased") or 0
                        result["spent"] += row.get("total_credits_spent") or 0
                        result["gifted"] += row.get("total_credits_gifted") or 0
    except Exception as e:
        logger.warning(f"admin_credits totals error: {e}")

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions?select=amount,type,description,created_at,user_id&created_at=gte.{since}&order=created_at.asc&limit=5000",
                headers=_headers(), timeout=15,
            )
            rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"admin_credits daily error: {e}")
        rows = []

    daily_buckets = {}
    reason_totals = {}
    for row in rows:
        if row.get("user_id") in admin_ids:
            continue
        date_key = (row.get("created_at") or "")[:10]
        amount = row.get("amount") or 0
        if date_key:
            b = daily_buckets.setdefault(date_key, {"granted": 0, "spent": 0})
            if amount >= 0:
                b["granted"] += amount
            else:
                b["spent"] += -amount
        if amount < 0:
            reason = row.get("description") or row.get("type") or "diger"
            reason_totals[reason] = reason_totals.get(reason, 0) + (-amount)

    now = datetime.now(timezone.utc)
    ordered_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    result["daily"] = [{"date": d, **daily_buckets.get(d, {"granted": 0, "spent": 0})} for d in ordered_days]
    result["by_reason"] = reason_totals
    return result


async def get_credit_packages(active_only: bool = True) -> list:
    """Purchasable credit packages (Polar products) for the Buy
    Credits page."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            url = f"{SUPABASE_URL}/rest/v1/credit_packages?select=*&order=credits.asc"
            if active_only:
                url += "&is_active=eq.true"
            r = await client.get(url, headers=_headers(), timeout=10)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"get_credit_packages error: {e}")
    return []


async def get_package_by_apple_product_id(product_id: str) -> dict | None:
    """Look up a credit package by its App Store / RevenueCat product id, so
    the IAP webhook knows how many credits a purchase grants."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not product_id:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_packages?select=id,name,credits&apple_product_id=eq.{product_id}",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"get_package_by_apple_product_id error: {e}")
    return None


async def record_purchase(user_id: str, credits: int, amount_paid: float, currency_paid: str, external_id: str, channel: str = "web", description: str = "Satın alma") -> bool:
    """Credits a user's balance for a REAL payment (Polar
    webhook). Idempotent on external_id - a retried/duplicate webhook
    delivery for the same order is a no-op, not a double-credit."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not credits:
        return False
    try:
        # Karar B: atomik + idempotent. Onceki oku-degistir-yaz (GET+PATCH)
        # eszamanli spend ile lost-update, cift webhook ile yaris uretiyordu.
        # apply_credit_change ledger'i ONCE ekler (external_id UNIQUE -> cift
        # teslim kaynagında durur), sonra bakiyeyi ayni transaction'da artirir.
        async with httpx.AsyncClient() as client:
            rpc = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/apply_credit_change",
                headers=_headers(),
                json={
                    "p_user_id": user_id, "p_amount": credits, "p_type": "purchase",
                    "p_description": description, "p_channel": channel,
                    "p_external_id": external_id, "p_purchased_delta": credits,
                    "p_amount_paid": amount_paid, "p_currency": currency_paid,
                    "p_idempotent": True,
                },
                timeout=10,
            )
            if rpc.status_code != 200:
                logger.warning(f"record_purchase rpc failed {rpc.status_code}: {rpc.text[:200]}")
                return False
            res = rpc.json()
            # applied=true (ilk teslim) VEYA reason=duplicate (mukerrer webhook) ->
            # ikisi de cagiran icin BASARI (idempotent no-op).
            if isinstance(res, dict) and res.get("applied"):
                # Komisyon YALNIZ ilk teslimde. reason=="duplicate" yolunda
                # cagirmiyoruz: o mukerrer webhook, yeni bir alim degil.
                # (Yine de partial unique index ikinci savunma hatti.)
                await _record_referral_commission(client, user_id, external_id,
                                                  amount_paid, currency_paid)
                return True
            if isinstance(res, dict) and res.get("reason") == "duplicate":
                return True
            logger.warning(f"record_purchase not applied: {res}")
            return False
    except Exception as e:
        logger.warning(f"record_purchase error: {e}")
    return False


def _parse_ts(v) -> datetime | None:
    """Supabase zaman damgasini datetime'a cevirir. Iki bicim de gelebiliyor:
    '2026-07-25T19:03:04.640749+00:00' ve '2026-07-25 19:03:04.640749+00'
    (ikincisinde saat dilimi iki haneli — fromisoformat eskiden bunu reddederdi).
    Cozulemezse None; cagiran guvenli bir varsayilana duser."""
    if not v:
        return None
    t = str(v).replace(" ", "T")
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    elif re.search(r"[+-]\d{2}$", t):
        t += ":00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# Influencer komisyonu: KADEMELI ve SURELI (kurucu karari 2026-07-25).
# (ay_ust_siniri, oran) — davetlinin KAYIT tarihinden itibaren.
# 1. yil %10, 2. yil %5, 3. yil %5, sonra biter. Sonsuz gelir paylasimi
# istenmedi: bir kez kazanilan musteriye omur boyu yukumluluk baglanmasin.
REFERRAL_COMMISSION_TIERS = ((12, 0.10), (24, 0.05), (36, 0.05))


def _referral_commission_rate(kayit_tarihi: datetime, alim_tarihi: datetime) -> float:
    """Davetlinin kayit tarihinden gecen aya gore komisyon orani."""
    gun = (alim_tarihi - kayit_tarihi).days
    ay = gun / 30.44  # ortalama ay
    for ust, oran in REFERRAL_COMMISSION_TIERS:
        if ay < ust:
            return oran
    return 0.0


async def _record_referral_commission(client, buyer_id: str, external_id: str,
                                      amount_paid, currency: str) -> bool:
    """Davet komisyonu — davetli PARA HARCADIGINDA davet edene nakit.

    KIME: yalnizca KABUL EDILMIS creator/elci ortagina (creator_applications
    status='accepted'). HERKESE DEGIL. Her kullanici nakit komisyon
    biriktirseydi (a) her davet kalici bir yukumluluk olurdu, (b) kendi kendini
    davet eden halkalar dogrudan nakit basardi. Sıradan kullanicinin karsiligi
    token odulu (grant_referral_reward, iki tarafa 10) — o yerinde duruyor.

    ORAN: kademeli ve sureli, REFERRAL_COMMISSION_TIERS. Saat davetlinin KAYIT
    tarihinden isler (profiles.created_at) — davetin gerceklestigi an odur.

    MATRAH: fiilen odenen tutar (credit_transactions.amount_paid), liste degil.
    NOT: bu BRUT tutardir; iOS'ta Apple %15-30 alir, yani %10 brut fiili
    tahsilatin ~%14'une denk gelir. Kurucu bunu bilerek brut sectiği icin
    boyle; degistirmek gerekirse tek yer burasi.
    """
    try:
        if not amount_paid or float(amount_paid) <= 0:
            return False
        if (currency or "USD").upper() != "USD":
            logger.info(f"_record_referral_commission: USD disi ({currency}), atlandi")
            return False

        pr = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{buyer_id}&select=referred_by,created_at",
            headers=_headers(), timeout=10,
        )
        rows = pr.json() if pr.status_code == 200 else []
        if not rows or not rows[0].get("referred_by"):
            return False
        referrer_id = rows[0]["referred_by"]

        # Kabul edilmis ortak mi? Degilse nakit YOK (token odulu zaten verildi).
        ca = await client.get(
            f"{SUPABASE_URL}/rest/v1/creator_applications"
            f"?user_id=eq.{referrer_id}&status=eq.accepted&select=id&limit=1",
            headers=_headers(), timeout=10,
        )
        if ca.status_code != 200 or not ca.json():
            return False

        simdi = datetime.now(timezone.utc)
        kayit = _parse_ts(rows[0].get("created_at"))
        if kayit is None:
            # FAIL-CLOSED: eskiden `or simdi` vardi -> tarih cozulemezse "bugun
            # kayit oldu" varsayilip EN YUKSEK oran (%10) odeniyordu; 5 yil
            # onceki, suresi dolmus bir davetli bile taze gibi komisyon alirdi
            # (2026-07-26 denetimi). Belirsizlikte odeme YAPILMAZ.
            logger.warning(f"_record_referral_commission: created_at cozulemedi (buyer={buyer_id}), komisyon atlandi")
            return False
        oran = _referral_commission_rate(kayit, simdi)
        if oran <= 0:
            return False  # 3 yil doldu

        # Islem kimligini external_id'den cek (RPC id dondurmuyor); komisyonun
        # tekrar yazilmasini partial unique index bunun uzerinden engelliyor.
        tx = await client.get(
            f"{SUPABASE_URL}/rest/v1/credit_transactions"
            f"?external_id=eq.{urllib.parse.quote(str(external_id))}&select=id&limit=1",
            headers=_headers(), timeout=10,
        )
        tx_rows = tx.json() if tx.status_code == 200 else []
        if not tx_rows:
            return False

        brut = round(float(amount_paid), 2)
        tutar = round(brut * oran, 2)
        if tutar <= 0:
            return False
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/expert_payouts",
            headers={**_headers(), "Prefer": "return=minimal"},
            json={
                "expert_id": referrer_id, "kind": "referral",
                "transaction_id": tx_rows[0]["id"], "customer_id": buyer_id,
                "basis_amount": brut, "rate": oran, "amount": tutar,
                "currency": "USD", "status": "pending",
                "period_month": simdi.date().replace(day=1).isoformat(),
                "note": f"Davet komisyonu (%{int(oran * 100)}) — davetlinin alimi",
            },
            timeout=10,
        )
        if r.status_code == 409:  # ayni islem icin komisyon zaten yazilmis
            return False
        if r.status_code not in (200, 201, 204):
            logger.warning(f"_record_referral_commission {r.status_code}: {r.text[:150]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"_record_referral_commission error: {e}")
        return False


async def void_delivery_payout(ticket_id: int, sebep: str = "iade/itiraz") -> bool:
    """Bir biletin TESLIM odemesini iptal eder (status='void').

    NEDEN VAR: 2026-07-26 denetimine kadar `kind='delivery'` bir odemeyi void
    eden HICBIR yol yoktu. Musteri itiraz edip admin tokeni iade ettiginde
    uzmana yazilan borc (or. $30) 'pending' kaliyor ve normal akista odeniyordu
    — sirket hem geliri iade ediyor hem uzmana nakit oduyordu. Admin bunu
    API'den duzeltemiyor, yalniz elle DB'ye dokunarak cozebiliyordu.

    Idempotent: zaten void ise 0 satir doner, False. Odenmis (paid) satiri da
    void eder — para cikmissa bile defter dogruyu gostermeli; muhasebe
    duzeltmesi admin'in isi.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/expert_payouts"
                f"?ticket_id=eq.{int(ticket_id)}&kind=eq.delivery&status=neq.void",
                headers={**_headers(), "Prefer": "return=representation"},
                json={"status": "void", "note": f"İptal — {sebep}"}, timeout=10,
            )
            if r.status_code != 200:
                logger.warning(f"void_delivery_payout {r.status_code}: {r.text[:150]}")
                return False
            rows = r.json()
            if rows:
                logger.warning(f"teslim odemesi iptal edildi: ticket={ticket_id} sebep={sebep}")
            return bool(rows)
    except Exception as e:
        logger.warning(f"void_delivery_payout error: {e}")
        return False


async def _void_referral_commission(client, buyer_id: str,
                                    original_external_id: str | None = None,
                                    amount_paid=None) -> bool:
    """Iade edilen alimin komisyonunu iptal eder (status='void').

    Iki eslesme yolu var cunku iki kanal ayni bilgiyi vermiyor:
      - Polar: iadenin external_id'si 'refund_<orijinal>' — orijinali BIREBIR
        biliyoruz, islem kimliginden kesin eslesme.
      - Apple/RevenueCat: iade olayi kendi olay kimligiyle geliyor, satin alma
        satirina baglayacak ortak bir anahtar YOK (alimi rc_<event_id> ile
        yaziyoruz). Bu yuzden musteri + ayni tutar + bekleyen komisyon
        uzerinden EN YENIsi iptal edilir. Kesin degil ama iade edilmis satisin
        komisyonunu odenmis birakmaktan iyidir; iptal defterde gorunur kalir
        (void satir listede durur, toplamlara girmez).
    """
    try:
        q = None
        if original_external_id:
            tx = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions"
                f"?external_id=eq.{urllib.parse.quote(str(original_external_id))}&select=id&limit=1",
                headers=_headers(), timeout=10,
            )
            rows = tx.json() if tx.status_code == 200 else []
            if rows:
                q = (f"{SUPABASE_URL}/rest/v1/expert_payouts"
                     f"?transaction_id=eq.{rows[0]['id']}&kind=eq.referral&status=eq.pending")
        if q is None and amount_paid:
            # ZAMAN PENCERESI + EN ESKI. Denetim (2026-07-26) sunu gosterdi:
            # yalnizca (musteri + tutar) ile EN YENI satiri void etmek YANLIS
            # komisyonu iptal edebiliyor — davetli ayni paketi iki kez alip
            # Apple ESKI alimi iade ederse, heuristik YENI (gecerli) komisyonu
            # siliyor, iade edilenin komisyonu ise odeniyordu.
            # Iki degisiklik: (1) 180 gunden eski satirlara hic dokunma —
            # alakasiz eski bir komisyonu silme riskini keser. (2) EN ESKI
            # eslesmeyi sec: iadeler tipik olarak once yapilan alimi hedefler,
            # boylece cok-alimli durumda dogru satira daha yakin isabet eder.
            # Kesin cozum RevenueCat alim satirina transaction_id yazmak — bu
            # bir sema/sozlesme isi, ayri maddede duruyor.
            # isoformat() "+00:00" uretiyordu; sorgu dizesinde "+" bosluga
            # cozulup 180 gunluk pencere filtresi bozuluyordu (2026-07-30).
            pencere = _pgrest_ts(datetime.now(timezone.utc) - timedelta(days=180))
            find = await client.get(
                f"{SUPABASE_URL}/rest/v1/expert_payouts"
                f"?customer_id=eq.{buyer_id}&kind=eq.referral&status=eq.pending"
                f"&basis_amount=eq.{round(float(amount_paid), 2)}"
                f"&created_at=gte.{pencere}"
                f"&select=id&order=created_at.asc&limit=1",
                headers=_headers(), timeout=10,
            )
            rows = find.json() if find.status_code == 200 else []
            if not rows:
                logger.info(f"_void_referral_commission: eslesen bekleyen komisyon yok (buyer={buyer_id})")
                return False
            q = f"{SUPABASE_URL}/rest/v1/expert_payouts?id=eq.{rows[0]['id']}"
        if q is None:
            return False
        r = await client.patch(
            q, headers={**_headers(), "Prefer": "return=representation"},
            json={"status": "void", "note": "İade edildi — komisyon iptal"}, timeout=10,
        )
        return r.status_code == 200 and bool(r.json())
    except Exception as e:
        logger.warning(f"_void_referral_commission error: {e}")
        return False


async def get_credit_transaction(tx_id) -> dict | None:
    """Tek bir token hareketi (admin iade akisi icin)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions?id=eq.{tx_id}&select=id,user_id,amount,type,description,external_id",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"get_credit_transaction error: {e}")
    return None


async def transaction_exists(external_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions?select=id&external_id=eq.{external_id}",
                headers=_headers(), timeout=10,
            )
            return r.status_code == 200 and bool(r.json())
    except Exception as e:
        logger.warning(f"transaction_exists error: {e}")
    return False


async def get_share_result(job_id: str) -> dict | None:
    """Viral paylasim karti (geoni.ai/s/<id>) icin minimal, kisisel-verisiz
    alan seti. id tahmin edilemez uuid oldugu icin public erisim kabul; email,
    user_id gibi alanlar ASLA donmez. Yalnizca tamamlanmis taramalar."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?id=eq.{job_id}&status=eq.complete"
                f"&select=id,type,domain,name,score,result_json,created_at&limit=1",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return None
            row = r.json()[0]
            rj = row.get("result_json") or {}
            atype = row.get("type") or "web"
            recognized = None
            if atype != "web":
                recognized = rj.get("recognized")
            return {
                "job_id": row["id"],
                "type": atype,
                "label": row.get("domain") if atype == "web" else row.get("name"),
                "score": row.get("score"),
                "recognized": recognized,
                "created_at": row.get("created_at"),
            }
    except Exception as e:
        logger.warning(f"get_share_result error: {e}")
        return None


# Ligde gizlenen alan adlari artik leaderboard_hidden TABLOSUNDA yasar
# (opt-out talepleri, kalibrasyon taramalari, ayni sahibin ikinci alan
# adlari). Kaldirma talebi gelince kod degil tablo guncellenir:
#   INSERT INTO leaderboard_hidden (domain, reason) VALUES ('x.com','optout');
# Skorlar audits'te aynen durur; tablo yalnizca vitrini yonetir.


async def _get_leaderboard_hidden(client: "httpx.AsyncClient") -> set[str]:
    """leaderboard_hidden tablosundaki domainler. Tablo okunamazsa bos set
    doner (lig gizleme listesi olmadan da yayinlanabilir; tersini yapip
    tum ligi bosaltmak daha kotu bir arizadir)."""
    try:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/leaderboard_hidden?select=domain",
            headers=_headers(), timeout=10,
        )
        if r.status_code == 200:
            return {(row.get("domain") or "").lower().strip() for row in r.json()}
    except Exception as e:
        logger.warning(f"leaderboard_hidden read error: {e}")
    return set()

# Ligde gosterilen skorlama surumu (scoring.py SCORING_VERSION ile birlikte
# guncellenir; import dongusune girmemek icin burada sabit).
SCORING_VERSION_SHOWN = "v4"


async def get_ai_friendly_list(limit: int = 10) -> list:
    """AI Friendly Ligi: en yuksek skorlu SITELER (type=web) — domain basina
    en iyi tarama. 70 barajini gecenler seal=True ile muhur tasir; baraji
    gecemeyenler de listelenir (devlerin de kaldigi gorulsun). Yalnizca
    DOGRULANMIS taramalar listelenir: crawl en az 3 sayfa gezebilmis olmali —
    bot korumasina takilip 0 sayfayla biten taramalarin skoru "erisemedik"
    cezasi tasir, boyle bir skoru herkese acik afise etmek haksizlik olur
    (Stripe ornegi: gercekte llms.txt'si var ama crawl'imiz kesildi).
    Kisi/marka taramalari KVKK hassasiyeti nedeniyle listelenmez;
    e-posta/kullanici bilgisi asla donmez."""
    MIN_CRAWLED_PAGES = 3
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            hidden = await _get_leaderboard_hidden(client)
            # scoring_version filtresi: lig yalnizca guncel metodolojinin
            # skorlarini gosterir — eski surum skorlariyla (farkli authority
            # bilesimi) ayni tabloda karistirmak elmayla armudu siralamak olur.
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?type=eq.web&status=eq.complete"
                f"&result_json->>scoring_version=eq.{SCORING_VERSION_SHOWN}"
                f"&select=id,domain,score,created_at,pages:result_json->>total_pages,internal:result_json->>internal"
                f"&order=score.desc,created_at.desc&limit=300",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200:
                return []
            best: dict[str, dict] = {}
            for row in r.json():
                d = (row.get("domain") or "").lower().strip()
                # "." sarti: domain alanina yazilmis serbest metinleri
                # (kisi adi vb.) site liginden dislar.
                if not d or "." not in d or d in hidden:
                    continue
                # 🚩 IC DOGRULAMA taramalari ligden ELENIR: bunlarda SOV atlanir
                # (maliyet), dolayisiyla skor musteri taramalariyla KIYASLANAMAZ.
                # Elenmezse eksik skorlu bir kayit herkese acik siralamaya sizar.
                if row.get("internal"):
                    continue
                try:
                    if int(row.get("pages") or 0) < MIN_CRAWLED_PAGES:
                        continue
                except (TypeError, ValueError):
                    continue
                if d not in best or row["score"] > best[d]["score"]:
                    best[d] = {"domain": d, "score": row["score"], "seal": row["score"] >= 70,
                               "job_id": row["id"], "date": row.get("created_at", "")[:10]}
            ranked = sorted(best.values(), key=lambda x: -x["score"])[:limit]
            return ranked
    except Exception as e:
        logger.warning(f"get_ai_friendly_list error: {e}")
        return []


async def update_user_social(user_id: str, linkedin_url: str, instagram_handle: str) -> bool:
    """Kullanicinin LinkedIn/Instagram profillerini profiles'a yazar. Yazma
    servis anahtariyla yapilir (musteri profiles'i dogrudan RLS ile
    guncelleyebiliyor ama korumali kolonlari da acacagi icin tum profil
    yazimlarini backend'e tasimak daha guvenli). Bos string -> null (temizler)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    payload = {
        "linkedin_url": linkedin_url.strip() or None,
        "instagram_handle": instagram_handle.strip().lstrip("@") or None,
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json=payload, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"update_user_social error: {e}")
        return False


async def delete_user_account(user_id: str) -> bool:
    """Kullanicinin hesabini ve kisisel verisini kalici siler (Apple 5.1.1(v)
    uygulama-ici hesap silme sarti). Once kullaniciya ait tablo satirlari, sonra
    Supabase Auth kullanicisi silinir. En kritik adim auth kullanicisinin
    silinmesidir; digerleri en iyi caba (biri basarisiz olsa da devam eder)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            # Biletlere bagli alt kayitlar + STORAGE DOSYALARI once temizlenir.
            ticket_ids = []
            try:
                tk = await client.get(
                    f"{SUPABASE_URL}/rest/v1/tickets?user_id=eq.{user_id}&select=id",
                    headers=_headers(), timeout=10,
                )
                ticket_ids = [t.get("id") for t in (tk.json() if tk.status_code == 200 else [])
                              if t.get("id") is not None]
            except Exception:
                pass
            # KVKK silinme hakki: kullanicinin bilet dosyalarini (ekler +
            # uretilen teslimatlar) storage'dan da sil; yoksa hesap gittikten
            # sonra dosyalar kalici public URL'lerle bucket'ta kalir.
            if ticket_ids:
                try:
                    await _delete_attachment_files_for_tickets(client, ticket_ids)
                except Exception:
                    pass
            for tid in ticket_ids:
                try:
                    await client.delete(f"{SUPABASE_URL}/rest/v1/ticket_tasks?ticket_id=eq.{tid}", headers=_headers(), timeout=10)
                    await client.delete(f"{SUPABASE_URL}/rest/v1/ticket_messages?ticket_id=eq.{tid}", headers=_headers(), timeout=10)
                    await client.delete(f"{SUPABASE_URL}/rest/v1/ticket_ratings?ticket_id=eq.{tid}", headers=_headers(), timeout=10)
                except Exception:
                    pass
            # B1 (derin test 2026-07-22): silinen kullanici bir UZMANSA, BASKA
            # musterilerin ona ATANMIS aktif biletlerini SILME (onlar baska
            # musterinin) — admin kuyruguna devret: uzmani kaldir + 'open'a al ki
            # admin yeniden atasin (hayalet-uzman bileti kalmasin). Uzmanlik
            # alanlarini temizle. expert_payouts/expert_contracts finansal/hukuki
            # saklama gerekcesiyle BIRAKILIR (expert_id artik PII'siz yetim id).
            try:
                await client.patch(
                    f"{SUPABASE_URL}/rest/v1/tickets"
                    f"?assigned_expert_id=eq.{user_id}"
                    f"&status=not.in.(verified,closed,cancelled,refunded)",
                    headers=_headers(),
                    json={"assigned_expert_id": None, "status": "open"},
                    timeout=10,
                )
                await client.delete(
                    f"{SUPABASE_URL}/rest/v1/expert_ticket_types?expert_id=eq.{user_id}",
                    headers=_headers(), timeout=10)
            except Exception:
                pass
            # profiles.referred_by -> profiles(id) FK'si NO ACTION. Bu kullanici
            # birilerini DAVET ETTIYSE onlarin referred_by'i ona isaret eder ve
            # profil silme FK'ye takilir -> hesap silinemez (Apple 5.1.1(v) IHLALI:
            # "uygulama icinden hesap silme" calismaz). Canli e2e testte yakalandi.
            # Davet edenin hesabi gidince attribution zaten anlamsizlasir; isaretci
            # temizlenir (odul kayitlari credit_transactions'ta duruyor, kaybolmaz).
            try:
                await client.patch(
                    f"{SUPABASE_URL}/rest/v1/profiles?referred_by=eq.{user_id}",
                    headers=_headers(), json={"referred_by": None}, timeout=10,
                )
            except Exception:
                pass
            for tbl, col in [
                ("tickets", "user_id"), ("audits", "user_id"), ("credit_transactions", "user_id"),
                ("watchlist", "user_id"), ("push_tokens", "user_id"), ("iap_intents", "user_id"),
                ("notifications", "user_id"), ("tracked_assets", "user_id"),
                ("profiles", "id"),
            ]:
                try:
                    await client.delete(f"{SUPABASE_URL}/rest/v1/{tbl}?{col}=eq.{user_id}", headers=_headers(), timeout=10)
                except Exception:
                    pass
            # Supabase Auth kullanicisini sil (admin API) - asil hesap silme adimi.
            r = await client.delete(
                f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                headers=_headers(), timeout=15,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_user_account error: {e}")
    return False


async def record_refund(user_id: str, credits: int, external_id: str,
                        description: str = "İade: Polar satın alma",
                        original_external_id: str | None = None,
                        amount_paid=None) -> bool:
    """Iade sonrasi tokenlari geri duser. Bakiye eksiye dusebilir (kullanici
    tokenlari coktan harcadiysa) - iade karari admin'in, engellemiyoruz.
    Idempotency'yi cagiran taraf transaction_exists ile saglar."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not credits:
        return False
    try:
        # Karar B: bakiyeyi atomik dus (oku-degistir-yaz -> lost-update kapandi).
        # Idempotency cagirandadir (transaction_exists); external_id ledger'a
        # yazilir (partial UNIQUE ayni refund'i tekrar yazmaya calisirsa
        # transaction icinde reddedilir -> atomik geri sarilir). Bakiye eksiye
        # inebilir (iade karari admin'in), o yuzden p_clip_zero=false.
        async with httpx.AsyncClient() as client:
            rpc = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/apply_credit_change",
                headers=_headers(),
                json={
                    "p_user_id": user_id, "p_amount": -credits, "p_type": "refund",
                    "p_description": description, "p_channel": "refund",
                    "p_external_id": external_id, "p_purchased_delta": -credits,
                    "p_idempotent": False,
                },
                timeout=10,
            )
            if rpc.status_code != 200:
                logger.warning(f"record_refund rpc failed {rpc.status_code}: {rpc.text[:200]}")
                return False
            res = rpc.json()
            ok = isinstance(res, dict) and bool(res.get("applied"))
            if ok:
                # Iade edilen satisin davet komisyonu odenmis kalmasin.
                await _void_referral_commission(client, user_id, original_external_id, amount_paid)
            return ok
    except Exception as e:
        logger.warning(f"record_refund error: {e}")
    return False


async def get_admin_sales_stats(days: int = 14) -> dict:
    """Real revenue (from actual purchases), broken down by
    channel (web/ios/android), by signup traffic source (utm_source, i.e.
    how many people SIGNED UP from each source), and by traffic source's
    actual REVENUE (i.e. of the people who bought, which source brought
    them - the number that actually answers "is this channel worth it"),
    plus a list of recent purchases for the Satış tab."""
    result = {
        "revenue_by_channel": {}, "revenue_total": 0, "currency": "TRY",
        "by_source": {}, "revenue_by_source": {}, "recent": [], "daily": [],
        "sandbox_excluded": 0,
    }
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return result

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions"
                f"?select=user_id,amount,channel,amount_paid,currency_paid,created_at"
                f"&type=eq.purchase&created_at=gte.{since}&order=created_at.desc&limit=1000",
                headers=_headers(), timeout=15,
            )
            purchases = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"get_admin_sales_stats purchases error: {e}")
        purchases = []

    daily_buckets = {}
    # Sandbox/test alimlarinda gercek para donmez (App Store sandbox, Play
    # lisans testi). Ciroya katilirlarsa rapor sismis olur - kurucunun kendi
    # test alimi 739.99 TRY olarak ciroya giriyordu. Disaridi tut ama SAYISINI
    # dondur: sessizce yutmak, "satis gorunmuyor" seklinde yeni bir kafa
    # karisikligi yaratir.
    # Kanal etiketini YAZAN yer iap.channel_and_label; okuyan burasi. Kosulu
    # kopyalamak yerine ayni moduldeki yardimciyi kullaniyoruz - iki yere
    # kopyalanan kosul sessizce ayrisir (bkz. credits_spent hatasi, 07-29).
    from iap import is_sandbox_channel as _sandbox_kanal
    gercek = [p for p in purchases if not _sandbox_kanal(p.get("channel"))]
    result["sandbox_excluded"] = len(purchases) - len(gercek)
    purchases = gercek

    for p in purchases:
        channel = p.get("channel") or "web"
        amount_paid = float(p.get("amount_paid") or 0)
        result["revenue_by_channel"][channel] = result["revenue_by_channel"].get(channel, 0) + amount_paid
        result["revenue_total"] += amount_paid
        if p.get("currency_paid"):
            result["currency"] = p["currency_paid"]
        date_key = (p.get("created_at") or "")[:10]
        if date_key:
            daily_buckets[date_key] = daily_buckets.get(date_key, 0) + amount_paid
    result["recent"] = purchases[:20]
    ordered_days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    result["daily"] = [{"date": d, "revenue": round(daily_buckets.get(d, 0), 2)} for d in ordered_days]

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=utm_source&created_at=gte.{since}",
                headers=_headers(), timeout=15,
            )
            profiles = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"get_admin_sales_stats sources error: {e}")
        profiles = []

    by_source = {}
    for p in profiles:
        source = p.get("utm_source") or "direct"
        by_source[source] = by_source.get(source, 0) + 1
    result["by_source"] = by_source

    # Revenue by traffic source: for each buyer in this window, look up their
    # (own signup-time) utm_source regardless of when they signed up - a
    # purchase this week can come from someone who signed up via Instagram
    # last month, and that's exactly the number worth seeing.
    buyer_ids = sorted({p["user_id"] for p in purchases if p.get("user_id")})
    if buyer_ids:
        try:
            ids_filter = ",".join(buyer_ids)
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/profiles?select=id,utm_source&id=in.({ids_filter})",
                    headers=_headers(), timeout=15,
                )
                buyer_profiles = r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.warning(f"get_admin_sales_stats buyer sources error: {e}")
            buyer_profiles = []
        source_by_user = {bp["id"]: (bp.get("utm_source") or "direct") for bp in buyer_profiles}
        revenue_by_source = {}
        for p in purchases:
            source = source_by_user.get(p.get("user_id"), "direct")
            revenue_by_source[source] = revenue_by_source.get(source, 0) + float(p.get("amount_paid") or 0)
        result["revenue_by_source"] = {k: round(v, 2) for k, v in revenue_by_source.items()}

    return result


async def get_pricing_tiers() -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_pricing_tiers?select=*&order=platform.asc,min_credits.asc",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"get_pricing_tiers error: {e}")
    return []


async def add_pricing_tier(platform: str, min_credits: int, max_credits: int | None, price_per_credit: float, currency: str = "TRY") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/credit_pricing_tiers",
                headers=_headers(),
                json={
                    "platform": platform, "min_credits": min_credits, "max_credits": max_credits,
                    "price_per_credit": price_per_credit, "currency": currency,
                },
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"add_pricing_tier error: {e}")
    return False


async def delete_pricing_tier(tier_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{SUPABASE_URL}/rest/v1/credit_pricing_tiers?id=eq.{tier_id}",
                headers=_headers(), timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_pricing_tier error: {e}")
    return False


async def list_campaigns() -> list:
    """Admin-managed short links (geoni.ai/r/<slug>) that redirect to a
    target URL with baked-in UTM params - used for things like an Instagram
    bio link, so the destination doesn't need a long query string."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/campaigns?select=*&order=created_at.desc",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"list_campaigns error: {e}")
    return []


async def create_campaign(slug: str, name: str, target_url: str, utm_source: str, utm_medium: str, utm_campaign: str = "") -> dict:
    """Returns {"success": bool, "error": str|None} - slug must be globally
    unique (enforced by a DB constraint), so a duplicate slug fails cleanly
    with a message the admin panel can show instead of a generic error."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/campaigns",
                headers=_headers(),
                json={
                    "slug": slug, "name": name, "target_url": target_url,
                    "utm_source": utm_source, "utm_medium": utm_medium,
                    "utm_campaign": utm_campaign or None,
                },
                timeout=10,
            )
            if r.status_code in (200, 201):
                return {"success": True, "error": None}
            if r.status_code == 409:
                return {"success": False, "error": "duplicate_slug"}
            return {"success": False, "error": f"http_{r.status_code}"}
    except Exception as e:
        logger.warning(f"create_campaign error: {e}")
        return {"success": False, "error": "exception"}


async def delete_campaign(campaign_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{SUPABASE_URL}/rest/v1/campaigns?id=eq.{campaign_id}",
                headers=_headers(), timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_campaign error: {e}")
    return False


async def get_admin_provider_usage() -> dict:
    """Call-count fallback for the 4 external AI motors (see anthropic_admin.py
    for the one motor - Anthropic - that also has real USD cost data)."""
    empty = {"today": {}, "week": {}}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return empty

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider_usage = {"today": {}, "week": {}}
    # Sayim DB'de (provider_usage_counts RPC): eskiden ham satirlar cekilip
    # limit=5000 ile Python'da sayiliyordu -> haftalik kullanim 5000'i gecince
    # kesilecekti (latent). RPC'de limit yok, olcekli, verimli.
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/provider_usage_counts",
                headers=_headers(),
                json={"p_today_start": today_start, "p_week_start": week_start},
                timeout=10,
            )
            if r.status_code == 200:
                for row in r.json():
                    p = row.get("provider") or "unknown"
                    provider_usage["week"][p] = int(row.get("week_count") or 0)
                    tc = int(row.get("today_count") or 0)
                    if tc:
                        provider_usage["today"][p] = tc
            else:
                logger.info(f"provider_usage_counts RPC failed ({r.status_code})")
    except Exception as e:
        logger.warning(f"Provider usage aggregate failed: {e}")
    return provider_usage


async def get_manual_balances() -> dict:
    """Manually-entered real balances for providers with no balance API
    (OpenAI, Google, Perplexity, Tavily) - keyed by provider."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/manual_balances?select=provider,balance,currency,updated_at",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return {row["provider"]: row for row in r.json()}
            logger.info(f"manual_balances query failed ({r.status_code}) - table may not exist yet")
    except Exception as e:
        logger.warning(f"get_manual_balances error: {e}")
    return {}


async def set_manual_balance(provider: str, balance: float, currency: str = "USD") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/manual_balances",
                headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
                json={
                    "provider": provider,
                    "balance": balance,
                    "currency": currency,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                timeout=10,
            )
            return r.status_code in (200, 201, 204)
    except Exception as e:
        logger.warning(f"set_manual_balance error: {e}")
    return False


async def log_perplexity_usage(cost_usd: float, prompt_tokens: int, completion_tokens: int) -> None:
    """Fire-and-forget cost log for Perplexity calls. Perplexity has no cost/usage
    API at all (unlike OpenAI/Anthropic) - GEONI computes cost itself from the
    token counts already returned in every response, using published per-token
    + per-request pricing (see perplexity_admin.py). Requires the
    perplexity_usage_log table - silently no-ops if missing."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/perplexity_usage_log",
                headers=_headers(),
                json={
                    "cost_usd": cost_usd,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
                timeout=5,
            )
    except Exception as e:
        logger.debug(f"Perplexity usage log skipped: {e}")


async def get_perplexity_cost_daily(start: datetime, end: datetime) -> dict:
    """date (YYYY-MM-DD) -> USD cost that day, from GEONI's own
    perplexity_usage_log (self-computed, Perplexity'nin cost API'si yok).
    KOK NEDEN DUZELTMESI: eskiden ham satirlar PostgREST'ten cekilip Python'da
    toplaniyordu; PostgREST satir limiti (~1000) 1964+ satiri kesip harcamayi
    ~yariya dusuruyordu. Artik toplama DB'de (perplexity_cost_daily RPC) yapilir
    -> limit yok, olcekli, verimli (gunluk ~<=120 satir doner)."""
    daily = {}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return daily
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/perplexity_cost_daily",
                headers=_headers(),
                json={
                    "p_start": start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    "p_end": end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                },
                timeout=15,
            )
            if r.status_code == 200:
                for row in r.json():
                    date_key = str(row.get("day") or "")[:10]
                    if date_key:
                        daily[date_key] = float(row.get("cost") or 0)
            else:
                logger.info(f"perplexity_cost_daily RPC failed ({r.status_code})")
    except Exception as e:
        logger.warning(f"get_perplexity_cost_daily error: {e}")
    return daily


async def count_provider_calls_today(provider: str) -> int:
    """Bugun (UTC 00:00'dan beri) provider_usage'da <provider> cagri sayisi. grok-web
    GUNLUK MALIYET TAVANI icin (tek istek, Prefer count=exact -> Content-Range)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/provider_usage",
                params={"provider": f"eq.{provider}", "created_at": f"gte.{today}", "select": "id"},
                headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"},
                timeout=8,
            )
            cr = r.headers.get("content-range", "")  # "0-0/<total>" veya "*/<total>"
            total = cr.split("/")[-1] if "/" in cr else ""
            return int(total) if total.isdigit() else 0
    except Exception as e:
        logger.warning(f"count_provider_calls_today({provider}) error: {e}")
    return 0


async def get_grok_usage_daily(start: datetime, end: datetime) -> dict:
    """provider_usage'daki grok + grok_web cagri sayilarini gune gore bucketler:
    {YYYY-MM-DD: {"grok": n, "grok_web": m}}. Maliyet grok_admin'de cagri-basi
    fiyatla hesaplanir. Grok yeni bir SEMA gerektirmesin diye mevcut provider_usage
    satirlarindan turetilir. PostgREST 1000-satir limitine karsi sayfalanir (perplexity
    kok-neden dersi: limit harcamayi yariya dusurmustu)."""
    out: dict = {}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return out
    page = 1000
    offset = 0
    try:
        async with httpx.AsyncClient() as client:
            while True:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/provider_usage",
                    params={
                        "provider": "in.(grok,grok_web)",
                        "created_at": [f"gte.{start.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                                       f"lt.{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"],
                        "select": "provider,created_at",
                        "order": "created_at.asc",
                        "limit": str(page),
                        "offset": str(offset),
                    },
                    headers=_headers(), timeout=15,
                )
                if r.status_code != 200:
                    logger.info(f"grok provider_usage query failed ({r.status_code})")
                    break
                rows = r.json()
                for row in rows:
                    dk = str(row.get("created_at") or "")[:10]
                    prov = row.get("provider")
                    if not dk or prov not in ("grok", "grok_web"):
                        continue
                    day = out.setdefault(dk, {"grok": 0, "grok_web": 0})
                    day[prov] += 1
                if len(rows) < page:
                    break
                offset += page
    except Exception as e:
        logger.warning(f"get_grok_usage_daily error: {e}")
    return out


async def get_manual_topups_total(provider: str) -> float:
    """Sum of all logged top-ups for a provider (e.g. openai) - paired with
    the provider's real Costs API spend to estimate remaining balance,
    since top-ups happen repeatedly over time rather than as one fixed value."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0.0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/manual_topups?select=amount&provider=eq.{provider}",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return sum(float(row.get("amount") or 0) for row in r.json())
            logger.info(f"manual_topups query failed ({r.status_code}) - table may not exist yet")
    except Exception as e:
        logger.warning(f"get_manual_topups_total error: {e}")
    return 0.0


async def list_manual_topups(provider: str, limit: int = 20) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/manual_topups?select=id,amount,note,created_at&provider=eq.{provider}&order=created_at.desc&limit={limit}",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"list_manual_topups error: {e}")
    return []


async def add_manual_topup(provider: str, amount: float, note: str = "") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/manual_topups",
                headers=_headers(),
                json={"provider": provider, "amount": amount, "note": note},
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"add_manual_topup error: {e}")
    return False


async def get_manual_cost(provider: str) -> dict | None:
    """Latest manually-logged cost snapshot for a provider that has no real
    cost API (e.g. Supabase - its Management API only exposes request
    counts, not dollar billing). Unlike manual_topups (which accumulates),
    this is a single current/projected snapshot the admin re-enters
    periodically from the provider's own billing dashboard."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/manual_costs"
                f"?select=*&provider=eq.{provider}&order=created_at.desc&limit=1",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
            if r.status_code != 200:
                logger.info(f"manual_costs query failed ({r.status_code}) - table may not exist yet")
    except Exception as e:
        logger.warning(f"get_manual_cost error: {e}")
    return None


async def set_manual_cost(provider: str, current_cost: float, projected_cost: float = None,
                           cycle_start: str = None, cycle_end: str = None, note: str = "") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/manual_costs",
                headers=_headers(),
                json={
                    "provider": provider, "current_cost": current_cost, "projected_cost": projected_cost,
                    "cycle_start": cycle_start, "cycle_end": cycle_end, "note": note,
                },
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"set_manual_cost error: {e}")
    return False


_profiles_cache = {"value": None, "fetched_at": None}
_LIST_CACHE_TTL = timedelta(seconds=20)


_USER_SORT_FIELDS = {"email", "credit_balance", "total_credits_purchased", "total_credits_spent", "total_credits_gifted", "created_at"}


def _user_sort_key(p: dict, field: str):
    if field == "email":
        return (p.get("email") or "").lower()
    if field == "created_at":
        return p.get("created_at") or ""
    return p.get(field) or 0


async def admin_list_users(search: str = "", sort_by: str = "created_at", sort_dir: str = "desc", limit: int = 50, offset: int = 0) -> dict:
    """Merges profiles with auth emails (profiles has no email column). Search/
    sort/pagination done in-process - fine at MVP scale. The full profile list
    is cached briefly so typing in the search box or flipping pages doesn't
    re-fetch all 1000 rows from Supabase on every keystroke."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"users": [], "total": 0}

    now = datetime.now(timezone.utc)
    if _profiles_cache["value"] is not None and _profiles_cache["fetched_at"] and now - _profiles_cache["fetched_at"] < _LIST_CACHE_TTL:
        profiles = [dict(p) for p in _profiles_cache["value"]]
    else:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/profiles?select=id,full_name,credit_balance,total_credits_purchased,total_credits_spent,total_credits_gifted,is_admin,is_expert,is_suspended,created_at&order=created_at.desc&limit=1000",
                    headers=_headers(), timeout=15,
                )
                profiles = r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.warning(f"admin_list_users profiles fetch failed: {e}")
            profiles = []
        _profiles_cache["value"] = profiles
        _profiles_cache["fetched_at"] = now

    emails = await _fetch_all_auth_emails()
    for p in profiles:
        p["email"] = emails.get(p["id"], "")

    if search:
        s = search.lower()
        profiles = [p for p in profiles if s in (p.get("email") or "").lower() or s in (p.get("full_name") or "").lower()]

    sort_field = sort_by if sort_by in _USER_SORT_FIELDS else "created_at"
    profiles.sort(key=lambda p: _user_sort_key(p, sort_field), reverse=(sort_dir != "asc"))

    total = len(profiles)
    return {"users": profiles[offset:offset + limit], "total": total}


async def admin_adjust_credits(user_id: str, delta: int, reason: str = "") -> bool:
    """Manual credit grant (positive delta) or deduction (negative delta) by an admin.
    Grants are tracked in total_credits_gifted, NOT total_credits_purchased - a gift
    isn't revenue, and once a real payment flow exists, purchased-based earnings
    calculations must not count these."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not delta:
        return False
    try:
        # Karar B: atomik (satir kilidi) + clip. Onceki max(0,...) oku-degistir-yaz
        # hem lost-update hem ledger tutarsizligi (klip'te tam delta yaziliyordu)
        # uretiyordu. RPC bakiyeyi 0 altina indirmez ve ledger'a GERCEK uygulanan
        # miktari yazar. Bagis yalniz delta>0'da total_credits_gifted'a islenir.
        async with httpx.AsyncClient() as client:
            rpc = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/apply_credit_change",
                headers=_headers(),
                json={
                    "p_user_id": user_id, "p_amount": delta,
                    "p_type": "admin_grant" if delta > 0 else "admin_deduct",
                    "p_description": reason or "Admin manuel duzeltme",
                    "p_channel": "admin",
                    "p_gifted_delta": delta if delta > 0 else 0,
                    "p_clip_zero": True,
                    "p_idempotent": False,
                },
                timeout=10,
            )
            if rpc.status_code != 200:
                logger.warning(f"admin_adjust_credits rpc failed {rpc.status_code}: {rpc.text[:200]}")
                return False
            res = rpc.json()
            return isinstance(res, dict) and bool(res.get("applied"))
    except Exception as e:
        logger.warning(f"admin_adjust_credits error: {e}")
    return False


# ---- Peer referral (viral cekirdek) --------------------------------------
# Sema HAZIR (migration YOK): profiles.referral_code (text), profiles.referred_by
# (uuid), total_credits_gifted (int). Kod DETERMINISTIK uuid'den turetilir ->
# carpisma/race yok, idempotent. Bu blok yalniz ATTRIBUTION -> PARA VERMEZ; +1/+1
# odul ayri asama (ilk-tarama tetigi, apply_credit_change idempotent RPC).

def _ref_code_for(user_id: str) -> str:
    """uuid'den deterministik 8-karakter kod (base32, kucuk harf). Deterministik
    oldugundan ayni kullanici hep ayni kodu alir (race/carpisma yok); 40 bit uuid."""
    try:
        raw = uuid.UUID(str(user_id)).bytes
    except Exception:
        raw = str(user_id).encode()
    return base64.b32encode(raw).decode("ascii")[:8].lower()


async def get_or_create_referral_code(user_id: str) -> str | None:
    """Referral kodunu dondurur; yoksa deterministik uretip profiles'a yazar."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=referral_code",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                existing = (r.json()[0] or {}).get("referral_code")
                if existing:
                    return existing
            code = _ref_code_for(user_id)
            pr = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"referral_code": code}, timeout=10,
            )
            if pr.status_code in (200, 204):
                return code
            logger.warning(f"get_or_create_referral_code patch {pr.status_code}: {pr.text[:150]}")
    except Exception as e:
        logger.warning(f"get_or_create_referral_code error: {e}")
    return None


async def count_referred(user_id: str) -> int:
    """Bu kullanicinin getirdigi (referred_by=user_id) kayit sayisi."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?referred_by=eq.{user_id}&select=id",
                headers={**_headers(), "Prefer": "count=exact"}, timeout=10,
            )
            cr = r.headers.get("content-range", "")
            if "/" in cr and cr.split("/")[-1].isdigit():
                return int(cr.split("/")[-1])
    except Exception as e:
        logger.warning(f"count_referred error: {e}")
    return 0


async def referral_earned(user_id: str) -> int:
    """Referral'dan kazanilan toplam kontor. Davet kartinda "su ana kadar X kontor
    kazandin" diye gosterilir — sayi gorunur olmadan dongu kendini beslemiyor."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/credit_transactions"
                f"?user_id=eq.{user_id}&type=eq.referral_reward&select=amount",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return sum(int(x.get("amount") or 0) for x in r.json())
    except Exception as e:
        logger.warning(f"referral_earned error: {e}")
    return 0


async def set_referred_by(user_id: str, ref_code: str) -> dict:
    """SERVER-AUTHORITATIVE referral attribution (client asla referred_by yazamaz).
    Guardlar: kod->referrer cozulur; self-referral engeli; YALNIZ referred_by BOS
    olan kullaniciya yazilir (tek-atim, sonradan degistirilemez). PARA VERMEZ.
    Doner {ok, reason[, referrer]}."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id or not ref_code:
        return {"ok": False, "reason": "eksik parametre"}
    code = str(ref_code).strip().lower()[:16]
    if not re.fullmatch(r"[a-z0-9]{4,16}", code):
        return {"ok": False, "reason": "gecersiz kod"}
    try:
        async with httpx.AsyncClient() as client:
            rr = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?referral_code=eq.{code}&select=id",
                headers=_headers(), timeout=10,
            )
            rows = rr.json() if rr.status_code == 200 else []
            if not rows:
                return {"ok": False, "reason": "kod bulunamadi"}
            referrer_id = rows[0]["id"]
            if referrer_id == user_id:
                return {"ok": False, "reason": "self-referral"}
            # Kosullu patch: yalniz referred_by IS NULL iken yaz (yaris/tekrar korumasi).
            pr = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&referred_by=is.null",
                headers={**_headers(), "Prefer": "return=representation"},
                json={"referred_by": referrer_id}, timeout=10,
            )
            if pr.status_code == 200:
                updated = pr.json() if pr.text else []
                if updated:
                    return {"ok": True, "reason": "attributed", "referrer": referrer_id}
                return {"ok": False, "reason": "zaten atanmis"}
            if pr.status_code == 204:
                return {"ok": True, "reason": "attributed", "referrer": referrer_id}
            logger.warning(f"set_referred_by patch {pr.status_code}: {pr.text[:150]}")
    except Exception as e:
        logger.warning(f"set_referred_by error: {e}")
    return {"ok": False, "reason": "hata"}


async def grant_referral_reward(user_id: str) -> None:
    """Faz 2 ODUL: davet edilen kullanici bir tarama TAMAMLAYINCA iki tarafa da
    REFERRAL_REWARD_CREDITS (10 kontor = tam bir kisi/marka taramasi) verilir.
    Idempotent — apply_credit_change p_external_id UNIQUE, davetli basina TEK sefer;
    her tarama tamamlanmasinda cagrilabilir ama yalniz bir kez oder. Referral yoksa
    no-op. Taramayi ASLA dusurmez (sessiz).
    Fraud: odul KAYITTA degil ILK TARAMADA odenir — sahte hesap acmak tek basina
    para kazandirmaz, gercek bir tarama tamamlamak gerekir; ayrica referred_by
    tek-atim + self-ref set asamasinda engelli, burada da (savunma) referrer==user
    ise no-op."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return
    try:
        async with httpx.AsyncClient() as client:
            pr = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=referred_by",
                headers=_headers(), timeout=10,
            )
            rows = pr.json() if pr.status_code == 200 else []
            referrer_id = rows[0].get("referred_by") if rows else None
            if not referrer_id or referrer_id == user_id:
                return  # referral yok ya da (savunma) self -> odul yok
            n = REFERRAL_REWARD_CREDITS
            grants = (
                # Aciklama hesap hareketlerinde GORUNUR: arayuzdeki birim adi "token".
                (user_id,     f"ref_reward_invitee:{user_id}", f"Davet bonusu: hoş geldin (+{n} token)"),
                (referrer_id, f"ref_reward_inviter:{user_id}", f"Davet bonusu: getirdiğin kişi tarama yaptı (+{n} token)"),
            )
            odendi = []
            for uid, ext, desc in grants:
                try:
                    rpc = await client.post(
                        f"{SUPABASE_URL}/rest/v1/rpc/apply_credit_change",
                        headers=_headers(),
                        json={
                            "p_user_id": uid, "p_amount": n, "p_type": "referral_reward",
                            "p_description": desc, "p_channel": "referral",
                            "p_external_id": ext, "p_gifted_delta": n, "p_idempotent": True,
                        }, timeout=10,
                    )
                    if rpc.status_code != 200:
                        logger.warning(f"grant_referral_reward rpc {rpc.status_code} uid={uid}: {rpc.text[:150]}")
                    else:
                        odendi.append(uid)
                except Exception as e:
                    logger.warning(f"grant_referral_reward grant error uid={uid}: {e}")
    except Exception as e:
        logger.warning(f"grant_referral_reward error: {e}")
        return
    # Bildirim odemeden SONRA ve HTTP istemcisinin DISINDA: push hatasi odulu
    # etkilemesin. Yalniz davet EDENe gonderilir — davetli odulu zaten uygulama
    # icinde aninda gorunur, davet eden ise uygulamada degil (asil viral tetik bu).
    if referrer_id in odendi:
        try:
            from pushnotify import send_referral_reward_push
            await send_referral_reward_push(referrer_id, REFERRAL_REWARD_CREDITS)
        except Exception as e:
            logger.warning(f"grant_referral_reward push error: {e}")


# ── Promosyon kodlari ───────────────────────────────────────────────────────
# TEK KULLANIMLIK benzersiz kodlar; hediye token verir (p_gifted_delta), yani
# `total_credits_purchased` ARTMAZ -> kullanici "premium" olmaz ama tokenini
# HARCAYABILIR (2026-07-30'da duzeltilen ucretsiz-tarama kapisi sayesinde;
# oncesinde bu ozellik calismazdi, bkz. free_scan.free_scan_gate).
#
# Kodlar SIRDIR: promo_codes tablosunda RLS acik ve HIC policy yok, yalniz
# service_role erisir. Kodu ASLA loga/hata mesajina yazma.

# Karistirilabilir karakterler (0/O, 1/I/L) BILEREK yok: kod telefonda elle
# yazilacak ve DM'de okunacak.
_PROMO_ALFABE = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def promo_kodu_normalize(kod: str) -> str:
    """Kullanici girisini kanonik hale getirir: bosluk/tire silinir, buyutulur.
    'ab cd-1234' ve 'ABCD1234' AYNI koddur — kullanici tire koyarsa cezalandirma."""
    return "".join(ch for ch in (kod or "").upper() if ch.isalnum())


def promo_kodu_uret(uzunluk: int = 10) -> str:
    return "".join(secrets.choice(_PROMO_ALFABE) for _ in range(uzunluk))


async def promo_kodu_kullan(user_id: str, kod: str) -> dict:
    """Kodu kullanir ve hediye tokenlari yatirir. {"ok": bool, "hata": str, "credits": int}

    Sira ONEMLI: once kodu ATOMIK olarak sahiplen (kosullu UPDATE), sonra
    krediyi yatir. Tersi olsaydi es zamanli iki istek ayni kodu iki kez
    odeyebilirdi. Kredi yatirma idempotent (p_external_id=promo:<kod>), yani
    sahiplenme sonrasi yeniden deneme guvenli.
    """
    kod = promo_kodu_normalize(kod)
    if not (4 <= len(kod) <= 32):
        return {"ok": False, "hata": "gecersiz_promo_kodu"}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return {"ok": False, "hata": "promo_kullanilamadi"}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/promo_codes?code=eq.{kod}"
                "&select=code,credits,batch,expires_at,redeemed_by",
                headers=_headers(), timeout=10,
            )
            satirlar = r.json() if r.status_code == 200 else []
            if not satirlar:
                return {"ok": False, "hata": "gecersiz_promo_kodu"}
            satir = satirlar[0]
            if satir.get("redeemed_by"):
                return {"ok": False, "hata": "promo_kodu_kullanilmis"}
            son = satir.get("expires_at")
            if son and _pgrest_zaman_gecti(son):
                return {"ok": False, "hata": "promo_kodu_suresi_gecmis"}

            # ATOMIK sahiplenme: `redeemed_by=is.null` kosulu yarisi burada keser.
            sahiplen = await client.patch(
                f"{SUPABASE_URL}/rest/v1/promo_codes?code=eq.{kod}&redeemed_by=is.null",
                headers={**_headers(), "Prefer": "return=representation"},
                json={"redeemed_by": user_id,
                      "redeemed_at": datetime.now(timezone.utc).isoformat()},
                timeout=10,
            )
            if sahiplen.status_code == 409 or "23505" in (sahiplen.text or ""):
                # (batch, redeemed_by) benzersiz indeksi: ayni partiden ikinci kod.
                return {"ok": False, "hata": "promo_partisi_zaten_kullanildi"}
            if sahiplen.status_code not in (200, 201):
                logger.warning(f"promo sahiplenme {sahiplen.status_code}: {sahiplen.text[:150]}")
                return {"ok": False, "hata": "promo_kullanilamadi"}
            alinan = sahiplen.json() if sahiplen.text else []
            if not alinan:
                return {"ok": False, "hata": "promo_kodu_kullanilmis"}  # yarisi kaybettik

            n = int(alinan[0]["credits"])
            rpc = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/apply_credit_change",
                headers=_headers(),
                json={"p_user_id": user_id, "p_amount": n, "p_type": "promo",
                      "p_description": f"Promosyon kodu (+{n} token)",
                      "p_channel": "promo", "p_external_id": f"promo:{kod}",
                      "p_gifted_delta": n, "p_idempotent": True},
                timeout=10,
            )
            if rpc.status_code != 200:
                # TELAFI: kod yandi ama token yatmadi -> sahiplenmeyi geri al ki
                # kullanici kodu tekrar deneyebilsin (aksi halde sessizce kaybederdi).
                logger.error(f"promo kredi yatirilamadi ({rpc.status_code}), sahiplenme geri aliniyor")
                await client.patch(
                    f"{SUPABASE_URL}/rest/v1/promo_codes?code=eq.{kod}",
                    headers=_headers(),
                    json={"redeemed_by": None, "redeemed_at": None}, timeout=10,
                )
                return {"ok": False, "hata": "promo_kullanilamadi"}
            return {"ok": True, "credits": n}
    except Exception as e:
        logger.warning(f"promo_kodu_kullan error: {e}")
        return {"ok": False, "hata": "promo_kullanilamadi"}


def _pgrest_zaman_gecti(iso: str) -> bool:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt <= datetime.now(timezone.utc)
    except Exception:
        return False  # ayristirilamadi -> suresi gecmis SAYMA (kullaniciyi cezalandirma)


async def promo_toplu_uret(batch: str, credits: int, adet: int,
                           expires_at: str | None = None) -> list:
    """Admin: bir partide `adet` benzersiz kod uretir. Uretilen kodlari doner —
    cagiran bunlari BIR KEZ gorur (tabloda dururlar ama arayuze listelenirken
    de gorunur; kod sir olsa da admin gormeli, dagitim onun isi)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    batch = (batch or "").strip()[:60]
    if not batch or credits <= 0 or credits > 1000 or adet <= 0 or adet > 1000:
        return []
    satirlar = [{"code": promo_kodu_uret(), "credits": credits, "batch": batch,
                 "expires_at": expires_at} for _ in range(adet)]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/promo_codes",
                headers={**_headers(), "Prefer": "return=representation"},
                json=satirlar, timeout=20,
            )
            if r.status_code in (200, 201):
                return [x["code"] for x in r.json()]
            logger.warning(f"promo_toplu_uret {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"promo_toplu_uret error: {e}")
    return []


async def promo_parti_ozeti() -> list:
    """Admin: parti bazinda uretilen/dagitilan/kullanilan sayilari."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/promo_codes"
                "?select=batch,credits,expires_at,issued_to,redeemed_by&limit=5000",
                headers=_headers(), timeout=20,
            )
            if r.status_code != 200:
                return []
            ozet: dict = {}
            for x in r.json():
                b = ozet.setdefault(x["batch"], {
                    "batch": x["batch"], "credits": x["credits"],
                    "expires_at": x.get("expires_at"),
                    "toplam": 0, "dagitilan": 0, "kullanilan": 0})
                b["toplam"] += 1
                if x.get("issued_to"):
                    b["dagitilan"] += 1
                if x.get("redeemed_by"):
                    b["kullanilan"] += 1
            return sorted(ozet.values(), key=lambda x: x["batch"])
    except Exception as e:
        logger.warning(f"promo_parti_ozeti error: {e}")
    return []


async def admin_set_is_admin(user_id: str, is_admin_flag: bool) -> bool:
    """Grant or revoke admin panel access for a user."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"is_admin": is_admin_flag}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_is_admin error: {e}")
    return False


_ADMIN_SCOPE_FIELDS = {"users", "tickets", "campaigns"}


_admin_scope_cache: dict[str, tuple[bool, float]] = {}


async def has_admin_scope(user_id: str, scope: str) -> bool:
    """Narrower than is_strict_admin: also requires the specific
    admin_scope_<scope> flag, so a full admin can hand a limited admin
    (e.g. just ticket operations) without giving them everything. Cached
    briefly like is_strict_admin - a tab with several widgets fires this
    concurrently for the same user+scope."""
    if scope not in _ADMIN_SCOPE_FIELDS or not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    cache_key = f"{user_id}:{scope}"
    cached = _admin_scope_cache.get(cache_key)
    if cached and time.monotonic() - cached[1] < _TOKEN_CACHE_TTL:
        return cached[0]
    field = f"admin_scope_{scope}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_admin,{field}",
                headers=_headers(), timeout=8,
            )
            if r.status_code == 200 and r.json():
                row = r.json()[0]
                result = bool(row.get("is_admin")) and bool(row.get(field))
                _admin_scope_cache[cache_key] = (result, time.monotonic())
                return result
    except Exception as e:
        logger.warning(f"has_admin_scope error: {e}")
    return False


async def admin_set_admin_scopes(user_id: str, scopes: dict) -> bool:
    """scopes: {"users": bool, "tickets": bool, "campaigns": bool} - only
    known fields are ever written, so an unexpected key can't add an
    arbitrary column to the PATCH payload."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    payload = {f"admin_scope_{k}": bool(v) for k, v in scopes.items() if k in _ADMIN_SCOPE_FIELDS}
    if not payload:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json=payload, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_admin_scopes error: {e}")
    return False


async def admin_get_user_detail(user_id: str) -> dict | None:
    """Profile + expert verified/rejected counts for the admin panel's user
    detail view. Recent scans/transactions/tickets are separate paginated
    endpoints (admin_get_user_audits/transactions/tickets) - bundling them
    here would mean this single call could never be paginated per-list."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            profile_r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if profile_r.status_code != 200 or not profile_r.json():
                return None
            profile = profile_r.json()[0]

            expert_stats = None
            expert_specialization_ids = []
            if profile.get("is_expert"):
                verified_r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/tickets?assigned_expert_id=eq.{user_id}&status=eq.verified&select=id",
                    headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"}, timeout=10,
                )
                rejected_r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/tickets?assigned_expert_id=eq.{user_id}&status=eq.rejected&select=id",
                    headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"}, timeout=10,
                )
                disputed_r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/tickets?assigned_expert_id=eq.{user_id}&status=eq.disputed&select=id",
                    headers={**_headers(), "Prefer": "count=exact", "Range": "0-0"}, timeout=10,
                )
                def _count_from_range(resp):
                    cr = resp.headers.get("content-range", "")
                    total = cr.split("/")[-1] if "/" in cr else ""
                    return int(total) if total.isdigit() else 0
                expert_stats = {"verified": _count_from_range(verified_r),
                                "rejected": _count_from_range(rejected_r),
                                "disputed": _count_from_range(disputed_r)}
                expert_specialization_ids = await get_expert_ticket_type_ids(user_id)

        emails = await _fetch_all_auth_emails()
        profile["email"] = emails.get(user_id, "")

        auth_user = await _fetch_auth_user(user_id)
        profile["last_sign_in_at"] = auth_user.get("last_sign_in_at") if auth_user else None

        return {"profile": profile, "expert_stats": expert_stats,
                "expert_specialization_ids": expert_specialization_ids}
    except Exception as e:
        logger.warning(f"admin_get_user_detail error: {e}")
        return None


async def _paginated_get(url: str, headers: dict) -> tuple[list, int]:
    """Shared helper: PostgREST count=exact + Range pagination -> (rows, total).
    206 is the correct success status for a satisfied Range request (not 200)."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, headers=headers, timeout=10)
            if r.status_code not in (200, 206):
                return [], 0
            rows = r.json()
            cr = r.headers.get("content-range", "")
            total_s = cr.split("/")[-1] if "/" in cr else ""
            total = int(total_s) if total_s.isdigit() else len(rows)
            return rows, total
    except Exception as e:
        logger.warning(f"_paginated_get error ({url}): {e}")
        return [], 0


async def admin_get_user_audits(user_id: str, limit: int = 8, offset: int = 0) -> dict:
    rows, total = await _paginated_get(
        f"{SUPABASE_URL}/rest/v1/audits?user_id=eq.{user_id}&select=id,type,domain,name,score,credits_spent,status,created_at"
        f"&order=created_at.desc&limit={limit}&offset={offset}",
        {**_headers(), "Prefer": "count=exact"},
    )
    return {"items": rows, "total": total}


async def admin_get_user_transactions(user_id: str, limit: int = 8, offset: int = 0) -> dict:
    rows, total = await _paginated_get(
        f"{SUPABASE_URL}/rest/v1/credit_transactions?user_id=eq.{user_id}&select=id,amount,type,description,external_id,amount_paid,currency_paid,created_at"
        f"&order=created_at.desc&limit={limit}&offset={offset}",
        {**_headers(), "Prefer": "count=exact"},
    )
    # Iade edilen satin almalari isaretle (iade kaydi refund_<external_id>
    # kuraliyla tutulur) - arayuz butonu gizleyip "iade edildi" gosterir.
    purchase_exts = [r["external_id"] for r in rows if r.get("type") == "purchase" and r.get("external_id")]
    if purchase_exts:
        try:
            async with httpx.AsyncClient() as client:
                refund_ids = ",".join(f'"refund_{e}"' for e in purchase_exts)
                rr = await client.get(
                    f"{SUPABASE_URL}/rest/v1/credit_transactions?select=external_id&external_id=in.({refund_ids})",
                    headers=_headers(), timeout=10,
                )
                refunded = {x["external_id"] for x in rr.json()} if rr.status_code == 200 else set()
            for r in rows:
                if r.get("type") == "purchase":
                    r["refunded"] = f"refund_{r.get('external_id')}" in refunded
        except Exception as e:
            logger.warning(f"refund flag lookup error: {e}")
    return {"items": rows, "total": total}


async def admin_get_user_tickets(user_id: str, limit: int = 8, offset: int = 0) -> dict:
    rows, total = await _paginated_get(
        f"{SUPABASE_URL}/rest/v1/tickets?user_id=eq.{user_id}&select=id,ticket_type_id,status,token_cost,created_at"
        f"&order=created_at.desc&limit={limit}&offset={offset}",
        {**_headers(), "Prefer": "count=exact"},
    )
    if rows:
        types = await list_ticket_types(active_only=False)
        type_by_id = {t["id"]: t["name"] for t in types}
        for tk in rows:
            tk["ticket_type_name"] = type_by_id.get(tk.get("ticket_type_id"), "")
    return {"items": rows, "total": total}


async def admin_set_user_notes(user_id: str, notes: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"admin_notes": notes}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_user_notes error: {e}")
    return False


async def admin_set_suspended(user_id: str, suspended: bool) -> bool:
    """Blocks the account from spending credits (brand-check, checkout) -
    checked in main.py's _require_user, so it takes effect immediately on
    every authenticated endpoint that uses it, not just new logins."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"is_suspended": suspended}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_suspended error: {e}")
    return False


async def is_user_suspended(user_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=is_suspended",
                headers=_headers(), timeout=8,
            )
            if r.status_code == 200 and r.json():
                return bool(r.json()[0].get("is_suspended", False))
    except Exception as e:
        logger.warning(f"is_user_suspended error: {e}")
    return False


_AUDIT_SORT_FIELDS = {"email", "type", "target", "score", "credits_spent", "created_at"}


def _audit_sort_key(a: dict, field: str):
    if field == "email":
        return (a.get("email") or "").lower()
    if field == "target":
        return (a.get("domain") or a.get("name") or "").lower()
    if field == "score":
        return a.get("score") if a.get("score") is not None else -1
    if field == "credits_spent":
        return a.get("credits_spent") or 0
    if field == "type":
        return a.get("type") or ""
    return a.get("created_at") or ""


_audits_cache = {"value": None, "fetched_at": None}


async def admin_get_audit(audit_id: str) -> dict | None:
    """Full row (including result_json) for one audit - the list endpoints
    deliberately omit result_json (too heavy to send for every row), so the
    admin panel's "view this scan" click fetches it on demand."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits?id=eq.{audit_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"admin_get_audit error: {e}")
    return None


async def get_latest_web_audit_by_domain(domain: str) -> dict | None:
    """En son TAMAMLANMIS 'web' taramasi - llms_robots bilet otomasyonu
    icin marka/konu/sayfa verisini buradan cekiyoruz. Eski taramalarda
    'pages' alani olmayabilir (bu alan sonradan eklendi) - cagiran taraf
    bunu graceful fallback ile ele almali."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            # F-ORTA-10: domain kullanici girdisi (bilet target'i). f-string'e
            # gomulunce &/,/( gibi karakterlerle PostgREST filtre enjeksiyonu
            # (yanlis satir/veri sizintisi) mumkundu; httpx encode etsin diye
            # params ile gonder.
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits",
                params={
                    "domain": f"eq.{domain}",
                    "type": "eq.web",
                    "status": "eq.complete",
                    "select": "*",
                    "order": "created_at.desc",
                    "limit": "1",
                },
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"get_latest_web_audit_by_domain error: {e}")
    return None


async def get_latest_audit_by_target(target: str) -> dict | None:
    """En son TAMAMLANMIS taramayi hedefe gore dondur — TİPTEN BAĞIMSIZ.
    Web taramalari hedefi `domain` kolonunda, kişi/marka/sosyal taramalari `name`
    kolonunda tutar (bkz. create_pending_audit type=web|person|brand|social). Entity
    hizmetleri (content/citation/wikidata) domain-olmayan hedeflerde de çalıştığı için
    bu hizmetlerin fulfill'i hammaddesini (sov/opportunities/top_topics) buradan çeker.

    PostgREST filtre-enjeksiyonuna (F-ORTA-10) karşı `or=(...)` yerine domain ve name
    için AYRI param'lı (httpx-encode'lu) sorgu atılır; en yeni created_at kazanır."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    t = (target or "").strip()
    if not t:
        return None
    rows: list[dict] = []
    try:
        async with httpx.AsyncClient() as client:
            for col in ("domain", "name"):
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/audits",
                    params={
                        col: f"eq.{t}",
                        "status": "eq.complete",
                        "select": "*",
                        "order": "created_at.desc",
                        "limit": "1",
                    },
                    headers=_headers(), timeout=10,
                )
                if r.status_code == 200 and r.json():
                    rows.append(r.json()[0])
    except Exception as e:
        logger.warning(f"get_latest_audit_by_target error: {e}")
        return None
    if not rows:
        return None
    rows.sort(key=lambda a: a.get("created_at") or "", reverse=True)
    return rows[0]


async def admin_list_audits(
    search: str = "", sort_by: str = "created_at", sort_dir: str = "desc", limit: int = 50, offset: int = 0
) -> dict:
    """Full cross-user audit/brand-check log for the admin panel.
    Search/sort/pagination done in-process (mirrors admin_list_users) -
    email lives in Supabase Auth, not the audits table, so it can't be
    filtered/sorted via a plain PostgREST query anyway. The full 2000-row
    fetch is cached briefly (like admin_list_users) so search/sort/paging
    don't re-fetch it on every interaction."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"audits": [], "total": 0}

    now = datetime.now(timezone.utc)
    if _audits_cache["value"] is not None and _audits_cache["fetched_at"] and now - _audits_cache["fetched_at"] < _LIST_CACHE_TTL:
        audits = [dict(a) for a in _audits_cache["value"]]
    else:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/audits?select=id,user_id,type,domain,name,score,credits_spent,status,created_at&order=created_at.desc&limit=2000",
                    headers=_headers(),
                    timeout=15,
                )
                audits = r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.warning(f"admin_list_audits fetch failed: {e}")
            audits = []

        emails = await _fetch_all_auth_emails()
        for a in audits:
            a["email"] = emails.get(a.get("user_id"), "") if a.get("user_id") else ""

        _audits_cache["value"] = audits
        _audits_cache["fetched_at"] = now
        audits = [dict(a) for a in audits]

    if search:
        s = search.lower()
        audits = [
            a for a in audits
            if s in (a.get("email") or "").lower()
            or s in (a.get("domain") or "").lower()
            or s in (a.get("name") or "").lower()
        ]

    sort_field = sort_by if sort_by in _AUDIT_SORT_FIELDS else "created_at"
    audits.sort(key=lambda a: _audit_sort_key(a, sort_field), reverse=(sort_dir != "asc"))

    total = len(audits)
    return {"audits": audits[offset:offset + limit], "total": total}


# ── Bilet (ticket) sistemi ────────────────────────────────────────────────
# Tarama motorunun bulduğu eksiklikleri (şema, entity, içerik vb.) somut,
# token ile satın alınabilen düzeltme işlerine çevirir. Bir bilet: musteri
# satin alir (token dusulur) -> admin bir uzmana atar -> uzman kanit/link ile
# teslim eder -> admin dogrular. Musteriye/uzmana ozel gorunum icin
# ticket_type adi ve alici/uzman e-postasi ayri sorgularla eklenir (ticket'lar
# tablosu FK'lari sadece id tutuyor, e-posta Supabase Auth'ta ayri yasiyor).

async def list_ticket_types(active_only: bool = True, lang: str = "tr",
                            include_internal: bool = False) -> list:
    """include_internal=True YALNIZ admin ucundan: uzman odemesi ic maliyettir,
    musteri yanitinda yer almaz."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            url = f"{SUPABASE_URL}/rest/v1/ticket_types?select=*&order=token_cost.asc"
            if active_only:
                url += "&is_active=eq.true"
            r = await client.get(url, headers=_headers(), timeout=10)
            if r.status_code == 200:
                rows = r.json()
                # İngilizce istenirse name/description'i EN karsiligiyla degistir
                # (yoksa TR'ye geri duser). Boylece istemci tarafinda ek is yok.
                for row in rows:
                    # Hangi tarama hedefine uygulanabilir (UI/öneri filtresi için).
                    row["applicable_targets"] = applicable_targets_for(row.get("key", ""))
                    # "1200 token" tek basina olcek vermiyordu (kurucu geri
                    # bildirimi 2026-07-25). USD karsiligi HESAPLANMIYOR:
                    # money_price zaten hizmetin GERCEK fiyati (dogrudan satin
                    # alma / IAP fiyati). Token kurundan turetseydik uydurma bir
                    # sayi olurdu (paketler arasi birim fiyat %40 degisiyor) ve
                    # musterinin baska yerde gordugu fiyatla CELISIRDI.
                    row["usd_value"] = float(row["money_price"]) if row.get("money_price") else None
                    # Uzman odemesi musteriye AIT DEGIL — ic maliyet, sizdirma.
                    if not include_internal:
                        row.pop("expert_payout_usd", None)
                if lang == "en":
                    for row in rows:
                        if row.get("name_en"):
                            row["name"] = row["name_en"]
                        if row.get("description_en"):
                            row["description"] = row["description_en"]
                return rows
    except Exception as e:
        logger.warning(f"list_ticket_types error: {e}")
    return []


async def _get_ticket_type(ticket_type_id: int) -> dict | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_types?id=eq.{ticket_type_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"_get_ticket_type error: {e}")
    return None


async def get_ticket_type_by_key(key: str) -> dict | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_types?key=eq.{key}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"get_ticket_type_by_key error: {e}")
    return None


async def mark_ticket_submitted(ticket_id: int) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(), json={"status": "submitted"}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"mark_ticket_submitted error: {e}")
        return False


# Hizmet bagimliligi: iki temel hizmet (AI bot erisimi + schema) otonom teslim
# edilir ve TEMELDIR. Ileri hizmetler (guvenilir kaynaklarda gorunurluk, icerik,
# entity) bu ikisi yapilmadan bos kalir — o yuzden once bunlar alinmali.
# Web-YÜZEYİ hizmetleri: llms.txt/robots.txt/schema.org bir WEB SİTESİNE uygulanır.
# Bunlar hem "temel/ön-koşul" hizmetleridir hem de yalnızca DOMAIN hedefinde
# geçerlidir (kişi/marka/sosyal ismi/@handle için anlamsız — bkz. normalize_domain).
FOUNDATION_SERVICE_KEYS = ("llms_robots", "schema_setup")
DOMAIN_ONLY_SERVICE_KEYS = FOUNDATION_SERVICE_KEYS  # aynı küme


def normalize_domain(target: str) -> str | None:
    """Hedefi temiz bir web-sitesi domaini'ne indirger; GEÇERLİ domain değilse
    None döner. Kişi/marka/sosyal hedefleri (isim, @handle, boşluk içeren) None
    döner → domain gerektiren hizmetler (llms_robots/schema_setup) bunlara
    satılmaz/uygulanmaz, çöp dosya üretilmez. scheme/path/www sıyrılır, lowercase
    (B-3: "geoni.ai" ve "www.geoni.ai" aynı normalize edilir → ön-koşul eşleşmesi)."""
    t = (target or "").strip().lower()
    if not t or "@" in t or " " in t:
        return None
    t = re.sub(r"^https?://", "", t).split("/")[0].split("?")[0].split("#")[0]
    if t.startswith("www."):
        t = t[4:]
    t = t.strip(".")
    if "." in t and re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", t):
        return t
    return None


# Hizmet × hedef-tipi matrisi (kurgu düzeltmesi): hangi hizmet hangi tarama
# hedefine uygulanabilir. "domain" = web taraması / web sitesi olan hedef.
# İlk iki hizmet WEB-YÜZEYİ (yalnız domain); diğer üçü VARLIK (entity) hizmeti
# (kişi/marka/sosyal dahil her hedefe uygulanır — sosyal influencer için asıl
# değer buradadır). UI/öneri bu matrise göre filtreler; bilinmeyen key → hepsi.
SERVICE_APPLICABLE_TARGETS = {
    "llms_robots":        ["domain"],
    "schema_setup":       ["domain"],
    "wikidata_entity":    ["domain", "person", "brand", "social"],
    "content_package":    ["domain", "person", "brand", "social"],
    "citation_placement": ["domain", "person", "brand", "social"],
}
_ALL_TARGET_KINDS = ["domain", "person", "brand", "social"]


def applicable_targets_for(service_key: str) -> list[str]:
    return SERVICE_APPLICABLE_TARGETS.get(service_key, _ALL_TARGET_KINDS)


async def missing_service_prerequisites(user_id: str, service_key: str, target: str = "") -> list[str]:
    """Ileri bir hizmet icin, kullanicinin (ayni hedef icin) henuz almadigi TEMEL
    hizmet key'lerini dondurur. Temel hizmette ya da yapilandirma yoksa bos liste.
    Hata olursa bos liste (fail-open — gecici hata odeme/UX'i engellemesin)."""
    if service_key in FOUNDATION_SERVICE_KEYS or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    # Ileri hizmet HEDEFSIZ alinamaz: on-kosul hedef bazlidir; hedef yoksa
    # dogrulanamaz. Ayrica bu, "temeli baska hedefte al, ileriyi bos hedefle
    # gecir" baypasini kapatir. Hedefsiz -> hepsi eksik say (engelle).
    t = (target or "").strip()
    if not t:
        return list(FOUNDATION_SERVICE_KEYS)  # ileri hizmet hedefsiz alınamaz
    # Koşullu ön-koşul: hedef bir DOMAIN değilse (kişi/marka/sosyal isim/@handle),
    # web-yüzeyi temel hizmetleri bu hedefe UYGULANMAZ → ön-koşul yok, merdiven
    # açılır (web sitesi olmayan müşteri anlamsız hizmet almak zorunda kalmaz).
    dom = normalize_domain(t)
    if dom is None:
        return []
    try:
        async with httpx.AsyncClient() as client:
            fr = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_types?key=in.({','.join(FOUNDATION_SERVICE_KEYS)})&select=id,key",
                headers=_headers(), timeout=10)
            key_by_id = {row["id"]: row["key"] for row in (fr.json() if fr.status_code == 200 else [])}
            if not key_by_id:
                return []
            # Normalize edilmiş domain ile eşleştir ("geoni.ai" == "www.geoni.ai").
            q = f"{SUPABASE_URL}/rest/v1/tickets?user_id=eq.{user_id}&select=ticket_type_id"
            q += f"&target=eq.{quote(dom, safe='')}"
            tr = await client.get(q, headers=_headers(), timeout=10)
            have = {row["ticket_type_id"] for row in (tr.json() if tr.status_code == 200 else [])}
            # Temel hizmetin adini (name) da dondurebilmek icin key yeter; cagiran cevirir.
            return [k for i, k in key_by_id.items() if i not in have]
    except Exception as e:
        logger.warning(f"missing_service_prerequisites: {e}")
        return []


async def _telafi_et(user_id: str, cost: int, ticket_type: dict, sebep: str) -> None:
    """Bilet acilamadiginda dusulen kontoru geri verir VE DEFTERE YAZAR.

    Neden ayri fonksiyon: iki cikis yolundan (kotu HTTP yaniti / istisna)
    cagriliyor ve ikisinde de AYNI davranis gerekiyor.

    Neden ledger satiri: `deduct_credits_if_enough` yalnizca bakiyeyi degistirir,
    deftere yazmaz. Eskiden telafi sonrasi defterde "-cost ticket_purchase"
    satiri kaliyor, karsiligi olmuyordu -> credit_transactions toplami
    profiles.credit_balance ile UYUSMUYORDU (2026-07-26 denetimi, bulgu 6).
    Kendi HTTP istemcisini acar: cagiran istemci timeout'ta bozulmus olabilir.
    """
    try:
        async with httpx.AsyncClient() as c2:
            r = await c2.post(
                f"{SUPABASE_URL}/rest/v1/rpc/deduct_credits_if_enough",
                headers=_headers(), json={"p_user_id": user_id, "p_amount": -cost}, timeout=10,
            )
            # 2xx'in TAMAMI basari: RPC 200 doner ama PostgREST 201/204 de
            # dondurebiliyor; "!= 200" demek basarili bir telafiyi BASARISIZ
            # sayip krediyi geri vermis oldugumuz halde ledger'i atlardi.
            if r.status_code >= 300:
                # Telafi BASARISIZ: elle mutabakat gerekir, sessiz gecme.
                logger.error(f"TELAFI BASARISIZ user={user_id} cost={cost} sebep={sebep} http={r.status_code}")
                return
            await c2.post(
                f"{SUPABASE_URL}/rest/v1/credit_transactions",
                headers=_headers(),
                json={"user_id": user_id, "amount": cost, "type": "ticket_refund",
                      "description": f"[{ticket_type.get('key','')}] iade — hizmet açılamadı ({sebep})"},
                timeout=10,
            )
            logger.warning(f"purchase_ticket telafi edildi: user={user_id} cost={cost} sebep={sebep}")
    except Exception as e:
        logger.error(f"TELAFI BASARISIZ (istisna) user={user_id} cost={cost} sebep={sebep}: {e}")


async def purchase_ticket(user_id: str, ticket_type_id: int, audit_id: str | None = None,
                          target: str = "", request_id: str | None = None) -> dict:
    """Deducts token_cost from the buyer's balance and creates the ticket -
    both steps must succeed together, so balance is checked and the profile
    patched before the ticket row is inserted (best-effort atomicity without
    a DB transaction, matching the rest of this file's pattern)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    ticket_type = await _get_ticket_type(ticket_type_id)
    if not ticket_type or not ticket_type.get("is_active"):
        return {"success": False, "error": "invalid_ticket_type"}
    key = ticket_type.get("key", "")
    # Domain kapısı: web-yüzeyi hizmetleri (llms_robots/schema) yalnız GEÇERLİ bir
    # web sitesine uygulanır. İsim/@handle hedefi (kişi/marka/sosyal) çöp dosya
    # üretir — para alındıktan SONRA reddetmemek için satın alma anında doğrula.
    if key in DOMAIN_ONLY_SERVICE_KEYS:
        dom = normalize_domain(target)
        if dom is None:
            return {"success": False, "error": "invalid_target_domain"}
        target = dom  # normalize'lı domain'i sakla → ön-koşul/fulfillment tutarlı
    else:
        # Gelişmiş hizmet: hedef domain ise normalize et (temel hizmetle aynı
        # forma gelsin, ön-koşul eşleşsin); isim/@handle ise olduğu gibi kalır.
        dom = normalize_domain(target)
        if dom is not None:
            target = dom
    # Ileri hizmet: once iki temel hizmet alinmis olmali (yoksa bos kalir).
    missing = await missing_service_prerequisites(user_id, key, target)
    if missing:
        return {"success": False, "error": "prereq_missing", "missing": missing}
    cost = ticket_type["token_cost"]

    # TEKRAR-DENEME KORUMASI. Kullanici hata alip "tekrar dene" dediginde
    # eskiden IKINCI KEZ kontor dusuyordu (en pahali hizmet 1500 kontor).
    # Istemci ayni deneme icin ayni request_id'yi gonderir; daha once basarili
    # olmus bir deneme varsa yeni bilet ACILMAZ, mevcut bilet donulur.
    # NEDEN ON-KONTROL + DB KISITI BIRLIKTE: on-kontrol TOCTOU'ya acik (iki
    # istek ayni anda gecerse ikisi de "yok" gorur), bu yuzden asil garanti
    # tickets.request_id uzerindeki kismi UNIQUE indekstir; insert 409 verirse
    # asagida yaristan kaybeden taraf mevcut bileti okur ve KONTORU GERI VERIR.
    if request_id:
        try:
            async with httpx.AsyncClient() as c0:
                r0 = await c0.get(
                    f"{SUPABASE_URL}/rest/v1/tickets"
                    f"?request_id=eq.{urllib.parse.quote(request_id)}"
                    f"&select=id,ticket_type_id&limit=1",
                    headers=_headers(), timeout=10)
                if r0.status_code == 200 and r0.json():
                    var = r0.json()[0]
                    logger.info(f"purchase_ticket idempotent: request_id={request_id} -> ticket={var['id']}")
                    return {"success": True, "error": None, "ticket_id": var["id"],
                            "ticket_type_key": ticket_type.get("key"), "idempotent": True}
        except Exception as e:
            logger.warning(f"purchase_ticket idempotency on-kontrol hatasi: {e}")

    # Telafi izleyicisi: kredi DUSTUYSE ve sonrasinda is bitmediyse geri almaliyiz.
    # Eskiden telafi YALNIZ "ticket insert bir HTTP yanitiyla dondu ama durumu
    # kotu" dalindaydi; timeout/baglanti kopmasi dogrudan `except`e dusuyor ve
    # KREDI GERI ALINMADAN cikiliyordu (2026-07-26 denetimi). En pahali hizmet
    # 1500 kontor: kullanici hem tokeni kaybediyor hem bilet alamiyor, tekrar
    # deneyince bir daha dusuyordu.
    dusuldu = False
    try:
        async with httpx.AsyncClient() as client:
            # Atomik kosullu dusum: yeterli bakiye varsa TEK UPDATE ile duser ve
            # yeni bakiyeyi doner; yoksa satir donmez. Eszamanli satin almalarda
            # double-spend'i onler (eski oku-kontrol-yaz yarisa acikti).
            rpc_r = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/deduct_credits_if_enough",
                headers=_headers(),
                json={"p_user_id": user_id, "p_amount": cost},
                timeout=10,
            )
            if rpc_r.status_code != 200 or not rpc_r.json():
                return {"success": False, "error": "insufficient_balance"}
            dusuldu = True  # bu noktadan sonra her cikis yolunda telafi gerekir

            await client.post(
                f"{SUPABASE_URL}/rest/v1/credit_transactions",
                headers=_headers(),
                json={
                    "user_id": user_id, "amount": -cost, "type": "ticket_purchase",
                    # O7 (Fable 2026-07-19): [service_key] ön-eki ile client i18n çevirir
                    # (EN kullanıcı TR hizmet adı görmesin); admin için okunur ad korunur.
                    "description": f"[{ticket_type['key']}] {ticket_type['name']}",
                },
                timeout=10,
            )

            ticket_r = await client.post(
                f"{SUPABASE_URL}/rest/v1/tickets",
                headers={**_headers(), "Prefer": "return=representation"},
                json={
                    "user_id": user_id, "audit_id": audit_id, "ticket_type_id": ticket_type_id,
                    "target": target or None, "token_cost": cost,
                    "request_id": request_id,
                },
                timeout=10,
            )
            # 409 = ayni request_id ile baska bir istek yarisi kazandi (TOCTOU).
            # Bilet ZATEN var; kontoru geri ver ve var olani dondur.
            if ticket_r.status_code == 409 and request_id:
                await _telafi_et(user_id, cost, ticket_type, "idempotent_yaris")
                dusuldu = False
                r2 = await client.get(
                    f"{SUPABASE_URL}/rest/v1/tickets"
                    f"?request_id=eq.{urllib.parse.quote(request_id)}&select=id&limit=1",
                    headers=_headers(), timeout=10)
                if r2.status_code == 200 and r2.json():
                    return {"success": True, "error": None, "ticket_id": r2.json()[0]["id"],
                            "ticket_type_key": ticket_type.get("key"), "idempotent": True}
                return {"success": False, "error": "ticket_create_failed"}
            if ticket_r.status_code not in (200, 201) or not ticket_r.json():
                # Bilet olusmadi ama kredi dustu -> atomik geri al (refund).
                await _telafi_et(user_id, cost, ticket_type, "ticket_create_failed")
                dusuldu = False
                return {"success": False, "error": "ticket_create_failed"}
            new_ticket = ticket_r.json()[0]
            dusuldu = False  # is tamamlandi, telafi gerekmiyor
            await _clone_ticket_tasks(client, new_ticket["id"], ticket_type_id)
            return {"success": True, "error": None, "ticket_id": new_ticket["id"], "ticket_type_key": ticket_type.get("key")}
    except Exception as e:
        # KRITIK: buraya timeout/baglanti kopmasiyla da dusuluyor. Kredi dustuyse
        # MUTLAKA geri al — yoksa kullanici hem tokeni hem hizmeti kaybeder.
        logger.error(f"purchase_ticket error (dusuldu={dusuldu}): {e}")
        if dusuldu:
            await _telafi_et(user_id, cost, ticket_type, "exception")
        return {"success": False, "error": "exception"}


async def get_ticket_type_by_apple_product_id(product_id: str) -> dict | None:
    """Look up a service (ticket type) by its App Store / RevenueCat product
    id, so the IAP webhook knows which service a direct purchase buys."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not product_id:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_types?select=*&apple_product_id=eq.{product_id}",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"get_ticket_type_by_apple_product_id error: {e}")
    return None


async def create_iap_intent(user_id: str, product_id: str, target: str = "") -> bool:
    """Kullanicinin IAP ile birazdan ne alacagini (urun + hedef) magaza
    satin almasindan hemen once kaydeder; webhook bileti dogru hedefe acsin.

    K5 (2026-07-30): eskiden ayni urunun bekleyen niyetlerini `superseded`
    yapiyordu ("aramayi en tazeye indir"). Bu, gecikmis bir webhook'un KENDI
    niyetini bulmasini imkansiz kiliyordu: 1. alim teslim edilmeden 2. alim
    yapilirsa 1. niyet superseded olup aramadan dusuyor, 1. bilet 2. hedefe
    aciliyordu. Artik satirlar oldugu gibi birakilir; dogru satiri
    `consume_iap_intent` SATIN ALMA zamanina gore secer (created_at <=
    purchased_at) ve tuketilmeyenler IAP_INTENT_TTL_SECONDS ile yaslanip duser.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/iap_intents",
                headers=_headers(),
                json={"user_id": user_id, "product_id": product_id, "target": target or None},
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"create_iap_intent error: {e}")
    return False


# K5 (2026-07-30): bir niyet en fazla bu kadar sure "o satin almaya ait"
# sayilir. Kullanici magaza ekraninda VAZGECERSE niyet pending kalir (mobil
# niyeti satin almadan ONCE yazar, bkz. lib/iap.ts) ve TTL olmadan sonsuza dek
# bekler; aylar sonraki ilgisiz bir alim o bayat hedefi tuketebilir. Odeme
# akisinin makul ust siniri: kart/3DS/mağaza onayi dakikalar surer, saatler degil.
IAP_INTENT_TTL_SECONDS = 30 * 60


def _pgrest_ts(dt: "datetime") -> str:
    """PostgREST sorgu dizesi icin URL-guvenli UTC zaman damgasi.

    `datetime.isoformat()` "+00:00" uretir; sorgu dizesinde "+" BOSLUK olarak
    cozulur ve filtre bozulur. Depoda dogru kalip zaten vardi (bkz. istatistik
    sorgularindaki `strftime("%Y-%m-%dT%H:%M:%SZ")`), tek yerde tekrar edilmesin
    diye buraya alindi. Naive datetime UTC varsayilir."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def consume_iap_intent(user_id: str, product_id: str,
                             purchased_at: "datetime | None" = None) -> str | None:
    """Bu satin almaya ait niyeti alir, consumed isaretler ve hedefini doner
    (niyet yoksa bos dize).

    K5: eskiden yalniz `(user_id, product_id)`'nin EN TAZE pending satirini
    aliyordu — webhook hangi satin almaya ait oldugunu bilmiyordu. Gecikmis
    bir webhook (RevenueCat retry/ag) daha SONRA olusturulmus bir niyeti
    tuketip bileti YANLIS hedefe aciyor, ardindan gelen ikinci webhook ise
    hedefsiz kaliyordu (target="" -> main.py'deki oto-teslimat kosulu da
    dusuyor: musteri odedi, hicbir sey olmuyor).

    Cozum: eslesme teslim anina degil SATIN ALMA anina gore yapilir —
    `created_at <= purchased_at` olan en taze niyet. `purchased_at` yoksa
    (magaza vermedi) eski davranisa duser; TTL her iki durumda da uygulanir.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        # TTL alt siniri satin alma anina gore hesaplanir; webhook saatler sonra
        # gelse bile o an gecerli olan niyet hala esleşebilsin.
        ref = purchased_at or datetime.now(timezone.utc)
        en_eski = _pgrest_ts(ref - timedelta(seconds=IAP_INTENT_TTL_SECONDS))
        # Satin almadan SONRA olusturulan niyet bu satin almaya ait OLAMAZ.
        ust_sinir = (f"&created_at=lte.{_pgrest_ts(purchased_at)}"
                     if purchased_at else "")
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/iap_intents?user_id=eq.{user_id}&product_id=eq.{product_id}"
                f"&status=eq.pending&created_at=gte.{en_eski}{ust_sinir}"
                f"&select=id,target&order=created_at.desc&limit=1",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return ""
            row = r.json()[0]
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/iap_intents?id=eq.{row['id']}",
                headers=_headers(),
                json={"status": "consumed", "consumed_at": datetime.now(timezone.utc).isoformat()},
                timeout=10,
            )
            return row.get("target") or ""
    except Exception as e:
        logger.warning(f"consume_iap_intent error: {e}")
    return ""


async def create_paid_ticket(user_id: str, ticket_type_id: int, target: str, external_id: str,
                             amount_paid: float, currency: str, channel: str = "ios") -> dict:
    """A service bought directly with money (IAP). Modelled as two ledger
    steps so the wallet reads clearly: (1) the money grants the service's
    token value (+token_cost, a 'purchase' row carrying the real amount paid),
    then (2) those tokens are immediately spent on the ticket (-token_cost,
    which also opens the ticket). Net balance change is zero, but the history
    shows a transparent '+N token / -N hizmet' pair instead of a bare 0.

    Idempotent on external_id: the grant row carries it, so a retried webhook
    finds it and skips BOTH steps (no double credit, no second ticket)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    if await transaction_exists(external_id):
        logger.info(f"create_paid_ticket: external_id {external_id} already recorded, skipping")
        return {"success": True, "duplicate": True}
    ticket_type = await _get_ticket_type(ticket_type_id)
    if not ticket_type or not ticket_type.get("is_active"):
        return {"success": False, "error": "invalid_ticket_type"}
    credits = ticket_type["token_cost"]

    # IAP para ZATEN alindi (webhook post-payment) → burada sert reddetme; hedef
    # domain ise normalize et (temel hizmetle tutarli). Sert domain kapisi INTENT
    # endpoint'inde (pre-payment). Domain-only hizmete isim/@handle geldiyse
    # fulfill savunmasi cop uretmeyi engeller (bilet 'open' kalir).
    dom = normalize_domain(target)
    if dom is not None:
        target = dom

    # 1) Para -> token: cuzdana kredi (ledger'da +token, odenen tutarla).
    ok = await record_purchase(
        user_id, credits, amount_paid, currency, external_id, channel=channel,
        description=f"[{ticket_type['key']}] {ticket_type['name']}",  # O7: client i18n çevirir
    )
    if not ok:
        return {"success": False, "error": "credit_failed"}

    # 2) Token -> hizmet: bileti ac ve token'i dus (ledger'da -token).
    #    Yeni kredilenmis bakiye maliyeti karsilar; ayni bilet-acma + checklist
    #    + auto-fulfill (llms_robots) yolunu tekrar kullanir.
    return await purchase_ticket(user_id, ticket_type_id, None, target)


async def _clone_ticket_tasks(client: httpx.AsyncClient, ticket_id: int, ticket_type_id: int) -> None:
    """Copies the standard checklist template onto this specific ticket at
    purchase time - a snapshot, not a live reference, so later edits to the
    template (or a new template version) never change tickets already sold."""
    try:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/ticket_type_tasks?ticket_type_id=eq.{ticket_type_id}&select=title,sort_order,how_to&order=sort_order.asc",
            headers=_headers(), timeout=10,
        )
        templates = r.json() if r.status_code == 200 else []
        if not templates:
            return
        rows = [{"ticket_id": ticket_id, "title": t["title"], "sort_order": t["sort_order"], "how_to": t.get("how_to")} for t in templates]
        await client.post(f"{SUPABASE_URL}/rest/v1/ticket_tasks", headers=_headers(), json=rows, timeout=10)
    except Exception as e:
        logger.warning(f"_clone_ticket_tasks error: {e}")


async def list_ticket_tasks(ticket_id: int) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_tasks?ticket_id=eq.{ticket_id}&select=*&order=sort_order.asc",
                headers=_headers(), timeout=10,
            )
            return r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"list_ticket_tasks error: {e}")
        return []


async def toggle_ticket_task(task_id: int, ticket_id: int, done: bool) -> bool:
    """ticket_id is required (not just task_id from the URL) so a caller
    with access to ticket A can't toggle a task belonging to ticket B by
    guessing task ids - the endpoint already checked access to ticket_id,
    this scopes the actual write to match."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/ticket_tasks?id=eq.{task_id}&ticket_id=eq.{ticket_id}",
                headers=_headers(),
                json={"is_done": done, "done_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if done else None},
                timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"toggle_ticket_task error: {e}")
        return False


async def _get_unread_ticket_ids(ticket_ids: list, viewer_id: str) -> set:
    if not ticket_ids:
        return set()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/get_tickets_unread",
                headers=_headers(), json={"p_ticket_ids": ticket_ids, "p_user_id": viewer_id}, timeout=10,
            )
            if r.status_code == 200:
                return {row["ticket_id"] for row in r.json()}
    except Exception as e:
        logger.warning(f"_get_unread_ticket_ids error: {e}")
    return set()


async def mark_ticket_read(ticket_id: int, user_id: str) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/ticket_message_reads",
                headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
                json={"ticket_id": ticket_id, "user_id": user_id, "last_read_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                timeout=10,
            )
    except Exception as e:
        logger.warning(f"mark_ticket_read error: {e}")


async def _enrich_tickets(tickets: list, viewer_id: str = "") -> list:
    """Adds ticket_type_name, user_email, expert_email to each row -
    ticket_type is a simple join, but user/expert emails live in Supabase
    Auth (profiles has no email column), so they're merged in from the
    already-cached _fetch_all_auth_emails(). When viewer_id is given, also
    adds has_unread (a message from someone else, posted after the
    viewer's own last_read_at for that ticket)."""
    if not tickets:
        return tickets
    types = await list_ticket_types(active_only=False)
    type_by_id = {t["id"]: t for t in types}
    emails = await _fetch_all_auth_emails()
    for t in tickets:
        tt = type_by_id.get(t.get("ticket_type_id"), {})
        t["ticket_type_name"] = tt.get("name", "")
        t["ticket_type_key"] = tt.get("key", "")
        t["delivery_template"] = tt.get("delivery_template", "")
        t["user_email"] = emails.get(t.get("user_id"), "")
        t["expert_email"] = emails.get(t.get("assigned_expert_id"), "") if t.get("assigned_expert_id") else ""
    # Kirilim ilerlemesi TOPLU: eskiden her kanban karti kendi /tasks
    # istegini atiyordu (N bilet = N yetki-kontrollu istek) - panonun gec
    # acilmasinin ana nedeni. Simdi tek PostgREST cagrisiyla geliyor.
    try:
        ids = ",".join(str(t["id"]) for t in tickets)
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_tasks?ticket_id=in.({ids})&select=ticket_id,is_done",
                headers=_headers(), timeout=10,
            )
            rows = r.json() if r.status_code in (200, 206) else []
        agg = {}
        for row in rows:
            a = agg.setdefault(row["ticket_id"], [0, 0])
            a[1] += 1
            if row["is_done"]:
                a[0] += 1
        for t in tickets:
            done, total = agg.get(t["id"], (0, 0))
            t["tasks_done"], t["tasks_total"] = done, total
    except Exception as e:
        logger.warning(f"_enrich_tickets task progress error: {e}")
    if viewer_id:
        unread_ids = await _get_unread_ticket_ids([t["id"] for t in tickets], viewer_id)
        for t in tickets:
            t["has_unread"] = t["id"] in unread_ids
    return tickets


async def list_user_tickets(user_id: str) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?user_id=eq.{user_id}&select=*&order=created_at.desc",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                tickets = await _enrich_tickets(r.json(), user_id)
                for t in tickets:
                    t.pop("delivery_template", None)
                    # Uzmanin kimligini (e-posta) musteriye ASLA sizdirma - sadece
                    # admin/uzman endpoint'leri expert_email dondurur.
                    t.pop("expert_email", None)
                return tickets
    except Exception as e:
        logger.warning(f"list_user_tickets error: {e}")
    return []


async def get_ticket_by_id(ticket_id: int) -> dict | None:
    """A-1: bilet satırını (target/audit_id/type dahil) getirir — uzman audit
    bağlamı için. Erişim kontrolü ÇAĞIRANIN sorumluluğu (endpoint _require_ticket_access)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200 and r.json():
                return r.json()[0]
    except Exception as e:
        logger.warning(f"get_ticket_by_id error: {e}")
    return None


async def list_expert_tickets(expert_id: str) -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?assigned_expert_id=eq.{expert_id}&select=*&order=created_at.desc",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return await _enrich_tickets(r.json(), expert_id)
    except Exception as e:
        logger.warning(f"list_expert_tickets error: {e}")
    return []


async def start_ticket_work(ticket_id: int, expert_id: str) -> dict:
    """assigned -> in_progress. Musteri/admin de bu gecisi gorup uzmanin
    ise gercekten basladigini anlar - eskiden bu durum hic kullanilmiyordu."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=assigned_expert_id,status",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "not_found"}
            row = r.json()[0]
            if row.get("assigned_expert_id") != expert_id:
                return {"success": False, "error": "not_assigned"}
            if row.get("status") != "assigned":
                return {"success": False, "error": "invalid_status"}
            patch_r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(), json={"status": "in_progress"}, timeout=10,
            )
            return {"success": patch_r.status_code in (200, 204), "error": None}
    except Exception as e:
        logger.warning(f"start_ticket_work error: {e}")
        return {"success": False, "error": "exception"}


async def submit_ticket_evidence(ticket_id: int, expert_id: str, evidence_url: str, evidence_note: str = "") -> dict:
    """Only the expert this ticket is actually assigned to may submit -
    checked here rather than trusted from the request, since the endpoint
    takes the ticket_id from the URL and the expert's identity from their
    own auth token, not from client-supplied data."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=assigned_expert_id,status",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "not_found"}
            row = r.json()[0]
            if row.get("assigned_expert_id") != expert_id:
                return {"success": False, "error": "not_assigned"}
            if row.get("status") not in ("assigned", "in_progress"):
                return {"success": False, "error": "invalid_status"}

            patch_r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(),
                json={
                    "status": "submitted", "evidence_url": evidence_url, "evidence_note": evidence_note,
                    "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                timeout=10,
            )
            success = patch_r.status_code in (200, 204)
            if success:
                # Kanit ayrica konusma akisina da mesaj olarak eklenir -
                # boylece musteri Biletlerim'i actiginda kaniti gormek icin
                # ayri bir alan/UI'a bakmasi gerekmez, tek yerde gorur.
                await add_ticket_message(
                    ticket_id, expert_id, "expert",
                    body=evidence_note or "İşlem tamamlandı, kanıt eklendi.",
                    attachment_url=evidence_url,
                )
            return {"success": success, "error": None}
    except Exception as e:
        logger.warning(f"submit_ticket_evidence error: {e}")
        return {"success": False, "error": "exception"}


async def admin_list_tickets(status: str = "", admin_id: str = "") -> list:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            url = f"{SUPABASE_URL}/rest/v1/tickets?select=*&order=created_at.desc&limit=500"
            if status:
                url += f"&status=eq.{status}"
            r = await client.get(url, headers=_headers(), timeout=15)
            if r.status_code == 200:
                return await _enrich_tickets(r.json(), admin_id)
    except Exception as e:
        logger.warning(f"admin_list_tickets error: {e}")
    return []


async def admin_assign_ticket(ticket_id: int, expert_id: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(),
                json={
                    "assigned_expert_id": expert_id, "status": "assigned",
                    "assigned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_assign_ticket error: {e}")
    return False


async def _build_delivery_report(ticket_id: int) -> str:
    """Türkçe İş Teslim Raporu - onaylanan bir bilette, checklist'in
    tamamlanma durumu + sureyi kalici bir kayit olarak konusma akisina
    ekler (musteri de gorur, ayri bir alan aramasi gerekmez)."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return ""
            ticket = r.json()[0]
        types = await list_ticket_types(active_only=False)
        tt = next((t for t in types if t["id"] == ticket.get("ticket_type_id")), {})
        tasks = await list_ticket_tasks(ticket_id)
        emails = await _fetch_all_auth_emails()
        expert_email = emails.get(ticket.get("assigned_expert_id"), "—") if ticket.get("assigned_expert_id") else "—"

        opened = ticket.get("created_at", "")
        closed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            opened_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
            closed_dt = datetime.now(timezone.utc)
            delta = closed_dt - opened_dt
            hours = delta.total_seconds() / 3600
            duration = f"{delta.days} gün {int(hours % 24)} saat" if delta.days else f"{hours:.1f} saat"
        except Exception:
            duration = "—"

        lines = [
            "## İş Teslim Raporu",
            f"**Hizmet:** {tt.get('name', '—')}",
            f"**Hedef:** {ticket.get('target') or '—'}",
            f"**Açılış:** {opened[:16].replace('T', ' ')}",
            f"**Tamamlanma:** {closed[:16].replace('T', ' ')}",
            f"**Toplam süre:** {duration}",
            # C-4: bu rapor müşteriye GÖRÜNÜR threade eklenir; uzman kimliği
            # (e-posta) ASLA müşteriye gösterilmez (anonimlik tasarımı).
            "**Uzman:** GEONI Uzmanı",
        ]
        if tasks:
            lines.append("\n**Tamamlanan iş kırılımı:**")
            for tsk in tasks:
                mark = "✓" if tsk.get("is_done") else "—"
                lines.append(f"- [{mark}] {tsk['title']}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"_build_delivery_report error: {e}")
        return ""


async def _record_delivery_payout(client, ticket_id: int) -> bool:
    """Onaylanan teslim icin uzman kazanc satiri yazar.

    ODEME MODELI: yuzde DEGIL, hizmet basina SABIT ucret
    (ticket_types.expert_payout_usd — kurucu karari 2026-07-25).
    Guncel skala: icerik $10, wikidata $20, guvenilir kaynaklar $30;
    llms_robots/schema_setup NULL (ucretli uzmana atanmaz). Yuzde
    denenmisti ama matrahi belirsizdi: token'in tek fiyati yok (paketler arasi
    %40 fark), ustune Apple %15-30 alinca "%33" fiili tahsilatin yarisina
    cikiyordu ve hediye tokenla alinan iste gelir $0 iken nakit cikiyordu.
    Sabit ucrette odedigimiz rakami dogrudan biz belirliyoruz.

    basis_amount = hizmetin liste degeri (yalniz BAGLAM icin: defterde
    "$96'lik is, $35 odendi" okunabilsin). amount = fiilen odenecek tutar.

    Sessizce atlanan durumlar (hata degil):
      - biletin atanmis uzmani yok -> odenecek kimse yok
      - hizmetin expert_payout_usd'si NULL -> ucretli uzmana atanmayan hizmet
      - bu bilet icin zaten odeme satiri var -> tekrar onayda IKINCI BORC YOK
    """
    try:
        tr = await client.get(
            f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{int(ticket_id)}"
            f"&select=id,user_id,assigned_expert_id,ticket_type_id,token_cost",
            headers=_headers(), timeout=10,
        )
        rows = tr.json() if tr.status_code == 200 else []
        if not rows:
            return False
        t = rows[0]
        expert_id = t.get("assigned_expert_id")
        if not expert_id:
            return False

        tt = await client.get(
            f"{SUPABASE_URL}/rest/v1/ticket_types?id=eq.{t['ticket_type_id']}"
            f"&select=key,name,expert_payout_usd,token_cost,money_price",
            headers=_headers(), timeout=10,
        )
        types = tt.json() if tt.status_code == 200 else []
        if not types or types[0].get("expert_payout_usd") in (None, ""):
            return False
        tip = types[0]
        tutar = float(tip["expert_payout_usd"])

        # Liste degeri yalniz BAGLAM ("$107.99'luk is, $35 odendi"). Hizmetin
        # GERCEK fiyati (money_price) kullanilir; token kurundan turetmek
        # uydurma bir sayi verirdi. Fiyat tanimli degilse token referansina
        # duser — defter satiri yine de bir baglam tasisin.
        liste = (float(tip["money_price"]) if tip.get("money_price")
                 else round(float(tip.get("token_cost") or 0) * TOKEN_REFERENCE_USD, 2))
        simdi = datetime.now(timezone.utc)
        payload = {
            "expert_id": expert_id,
            "kind": "delivery",
            "ticket_id": int(ticket_id),
            "customer_id": t.get("user_id"),
            "basis_amount": liste,
            # rate bilgi amacli: sabit ucretin liste degerine orani.
            "rate": round(tutar / liste, 4) if liste else 0,
            "amount": tutar,
            "currency": "USD",
            "status": "pending",
            "period_month": simdi.date().replace(day=1).isoformat(),
            "note": f"{tip.get('name') or tip.get('key')} — onaylanan teslim",
        }
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/expert_payouts",
            headers={**_headers(), "Prefer": "return=minimal"},
            json=payload, timeout=10,
        )
        # 409 = kismi benzersiz indeks devrede: bu bilete zaten odeme yazilmis.
        # Yeniden onaylandi demektir; ikinci borc ACILMAZ ve bu bir hata degil.
        if r.status_code == 409:
            logger.info(f"_record_delivery_payout ticket {ticket_id}: odeme zaten var")
            return False
        if r.status_code not in (200, 201, 204):
            logger.warning(f"_record_delivery_payout {r.status_code}: {r.text[:150]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"_record_delivery_payout error: {e}")
        return False


async def admin_verify_ticket(ticket_id: int, admin_id: str, approve: bool, reject_reason: str = "") -> bool:
    """Onaylanirsa 'verified'e gecer + Is Teslim Raporu threade eklenir.
    Reddedilirse 'rejected' TERMINAL bir durum degil - 'assigned'a geri
    doner ki uzman gerekcesini gorup duzeltip tekrar teslim edebilsin
    (eskiden reddedilen bir bilet sonsuza kadar kilitli kaliyordu)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            if approve:
                payload = {
                    "status": "verified",
                    "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "verified_by": admin_id,
                }
            else:
                payload = {"status": "assigned", "reject_reason": reject_reason}
                # RED = teslim kabul edilmedi -> uzman kazanci da IPTAL.
                # Onayda yazilan borc red'de duruyordu; musteri itiraz edip admin
                # haklı bulunca sirket hem geliri iade edip hem uzmana odemeye
                # devam ediyordu (2026-07-26 denetimi). Uzman duzeltip yeniden
                # teslim eder ve tekrar onaylanirsa YENI satir acilir (kismi
                # benzersiz indeks yalniz void-olmayanlari sayar).
                await void_delivery_payout(ticket_id, sebep="teslim reddedildi")
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(), json=payload, timeout=10,
            )
            success = r.status_code in (200, 204)
            if success and approve:
                # Kazanc ONAYDA dogar, teslimde degil: reddedilen teslim bilete
                # 'assigned' olarak GERI DONUYOR (yukaridaki else dali), yani
                # 'submitted'da yazsaydik onaylanmayan ise borc acilirdi.
                await _record_delivery_payout(client, ticket_id)
                report = await _build_delivery_report(ticket_id)
                if report:
                    await add_ticket_message(ticket_id, None, "system", body=report)
            elif success and reject_reason:
                await add_ticket_message(ticket_id, admin_id, "admin", body=f"Teslim düzeltme için geri gönderildi:\n{reject_reason}")
            return success
    except Exception as e:
        logger.warning(f"admin_verify_ticket error: {e}")
    return False


async def admin_create_ticket_type(key: str, name: str, description: str, token_cost: int, verification_type: str = "manual") -> dict:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/ticket_types",
                headers=_headers(),
                json={
                    "key": key, "name": name, "description": description,
                    "token_cost": token_cost, "verification_type": verification_type,
                },
                timeout=10,
            )
            if r.status_code in (200, 201):
                return {"success": True, "error": None}
            if r.status_code == 409:
                return {"success": False, "error": "duplicate_key"}
            return {"success": False, "error": f"http_{r.status_code}"}
    except Exception as e:
        logger.warning(f"admin_create_ticket_type error: {e}")
        return {"success": False, "error": "exception"}


async def admin_set_ticket_type_active(ticket_type_id: int, is_active: bool) -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/ticket_types?id=eq.{ticket_type_id}",
                headers=_headers(), json={"is_active": is_active}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"admin_set_ticket_type_active error: {e}")
    return False


async def admin_set_is_expert(user_id: str, is_expert_flag: bool,
                              ticket_type_ids: list[int] | None = None) -> bool:
    """Uzman panel erisimini ver/al. Verirken uzmanlik alanlari
    (ticket_type_ids) da secilir — bu uzmanin YALNIZCA bu gorev turlerine
    atanabilecegini ve yalnizca bunlar icin "yeni gorev" bildirimi
    alacagini belirler. Yetki alinirsa uzmanlik alanlari da temizlenir."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
                headers=_headers(), json={"is_expert": is_expert_flag}, timeout=10,
            )
            if r.status_code not in (200, 204):
                return False
            # Uzmanlik alanlarini yeniden yaz: once sil, sonra ekle.
            await client.delete(
                f"{SUPABASE_URL}/rest/v1/expert_ticket_types?expert_id=eq.{user_id}",
                headers=_headers(), timeout=10,
            )
            if is_expert_flag and ticket_type_ids:
                rows = [{"expert_id": user_id, "ticket_type_id": int(t)} for t in ticket_type_ids]
                await client.post(
                    f"{SUPABASE_URL}/rest/v1/expert_ticket_types",
                    headers=_headers(), json=rows, timeout=10,
                )
            return True
    except Exception as e:
        logger.warning(f"admin_set_is_expert error: {e}")
    return False


async def get_expert_ticket_type_ids(expert_id: str) -> list[int]:
    """Bir uzmanin yapabilecegi gorev turlerinin id listesi."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/expert_ticket_types?expert_id=eq.{expert_id}&select=ticket_type_id",
                headers=_headers(), timeout=10,
            )
            if r.status_code == 200:
                return [row["ticket_type_id"] for row in r.json()]
    except Exception as e:
        logger.warning(f"get_expert_ticket_type_ids error: {e}")
    return []


async def list_experts() -> list:
    """Admin uzman karnesi: her uzman icin kimlik + uzmanlik alanlari +
    itibar (musteri puani ortalamasi, tamamlanan is, itiraz sayisi/orani,
    'hic itiraz almadi' rozeti). Atama listesini ve uzman siralamasini besler."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?select=id,full_name&is_expert=eq.true",
                headers=_headers(), timeout=10,
            )
            experts = r.json() if r.status_code == 200 else []
            if not experts:
                return []
            expert_ids = [e["id"] for e in experts]
            id_list = ",".join(f'"{i}"' for i in expert_ids)
            # Uzmanlik alanlari
            spec = await client.get(
                f"{SUPABASE_URL}/rest/v1/expert_ticket_types?expert_id=in.({id_list})&select=expert_id,ticket_type_id",
                headers=_headers(), timeout=10,
            )
            spec_map: dict[str, list[int]] = {}
            for row in (spec.json() if spec.status_code == 200 else []):
                spec_map.setdefault(row["expert_id"], []).append(row["ticket_type_id"])
            # Bu uzmanlara atanmis biletler (is/itiraz sayimi)
            tk = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?assigned_expert_id=in.({id_list})&select=assigned_expert_id,status",
                headers=_headers(), timeout=10,
            )
            jobs: dict[str, int] = {}
            disputes: dict[str, int] = {}
            done: dict[str, int] = {}
            for row in (tk.json() if tk.status_code == 200 else []):
                eid = row["assigned_expert_id"]
                jobs[eid] = jobs.get(eid, 0) + 1
                if row["status"] == "disputed":
                    disputes[eid] = disputes.get(eid, 0) + 1
                if row["status"] in ("verified", "disputed"):
                    done[eid] = done.get(eid, 0) + 1
            # Musteri puanlari (ratee = uzman, rater_role = customer)
            rt = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_ratings?ratee_id=in.({id_list})&rater_role=eq.customer&select=ratee_id,stars",
                headers=_headers(), timeout=10,
            )
            star_sum: dict[str, int] = {}
            star_cnt: dict[str, int] = {}
            for row in (rt.json() if rt.status_code == 200 else []):
                eid = row["ratee_id"]
                star_sum[eid] = star_sum.get(eid, 0) + row["stars"]
                star_cnt[eid] = star_cnt.get(eid, 0) + 1
    except Exception as e:
        logger.warning(f"list_experts error: {e}")
        return []
    emails = await _fetch_all_auth_emails()
    for e in experts:
        eid = e["id"]
        e["email"] = emails.get(eid, "")
        e["specialization_ids"] = spec_map.get(eid, [])
        e["jobs_total"] = jobs.get(eid, 0)
        e["jobs_done"] = done.get(eid, 0)
        e["dispute_count"] = disputes.get(eid, 0)
        e["dispute_rate"] = round(disputes.get(eid, 0) / done[eid], 3) if done.get(eid) else 0.0
        e["never_disputed"] = done.get(eid, 0) > 0 and disputes.get(eid, 0) == 0
        cnt = star_cnt.get(eid, 0)
        e["avg_rating"] = round(star_sum[eid] / cnt, 2) if cnt else None
        e["rating_count"] = cnt
    # Siralama: once puan (yuksek), sonra itiraz orani (dusuk), sonra is sayisi
    experts.sort(key=lambda x: (-(x["avg_rating"] or 0), x["dispute_rate"], -x["jobs_done"]))
    return experts


# ---- Creator / uzman basvurulari -----------------------------------------
# Basvuru /isbirligi formundan ya da IG DM mulakatindan gelir (ikisi de Vercel
# tarafinda yazar). BURASI yalnizca ADMIN KARARINI yurutur: kabul/red.
# Bot 'interviewed'e kadar tasiyabilir, 'accepted' YALNIZ buradan set edilir.

async def _ensure_expert_contract(client, expert_id: str, mode: str, admin_id: str,
                                  note: str = "") -> bool:
    """Kabul edilen uzman/elci icin 1 YILLIK sozlesme kaydi acar.

    NEDEN (kurucu karari 2026-07-26): "uzmanla sozlesme yapilmasi gerekli, NDA
    vs, yillik". expert_contracts tablosu vardi ama YAZAN KOD YOKTU — admin
    muhasebe ekrani "sozlesme durumu" alanini hep BOS gosteriyordu (2026-07-26
    denetimi, yarim kalmis ozellik).

    Idempotent: ayni uzman+mod icin AKTIF sozlesme varsa yenisini ACMAZ
    (kismi UNIQUE indeks DB seviyesinde de garanti eder). NDA imzasi ve belge
    baglantilari sonradan elle islenir — imzayi kod atamaz.
    """
    try:
        var = await client.get(
            f"{SUPABASE_URL}/rest/v1/expert_contracts"
            f"?expert_id=eq.{expert_id}&mode=eq.{mode}&status=eq.active&select=id&limit=1",
            headers=_headers(), timeout=10)
        if var.status_code == 200 and var.json():
            return False  # zaten aktif sozlesme var
        bugun = datetime.now(timezone.utc).date()
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/expert_contracts",
            headers={**_headers(), "Prefer": "return=minimal"},
            json={
                "expert_id": expert_id, "mode": mode,
                "starts_at": bugun.isoformat(),
                "ends_at": bugun.replace(year=bugun.year + 1).isoformat(),  # 1 yil
                "status": "active", "created_by": admin_id,
                "note": note or "Başvuru kabulünde otomatik açıldı — NDA ve sözleşme belgesi elle işlenecek",
            }, timeout=10)
        if r.status_code == 409:
            return False  # yaris: baska istek acti
        if r.status_code not in (200, 201, 204):
            logger.warning(f"_ensure_expert_contract {r.status_code}: {r.text[:150]}")
            return False
        logger.info(f"sozlesme acildi: expert={expert_id} mode={mode} (1 yil)")
        return True
    except Exception as e:
        logger.warning(f"_ensure_expert_contract error: {e}")
        return False


async def admin_list_creator_applications(status: str | None = None) -> dict:
    """Basvuru listesi + mulakat ozeti. Basvuranin yetenek alanlari bizim
    hizmet anahtarlarimiza cevrilmis geldigi icin (capable_keys), panelde
    okunur hizmet ADIYLA gosterilebilsin diye ticket_types da doner."""
    empty = {"applications": [], "ticket_types": [], "counts": {}}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return empty
    try:
        async with httpx.AsyncClient() as client:
            q = f"{SUPABASE_URL}/rest/v1/creator_applications?select=*&order=created_at.desc&limit=500"
            if status:
                q += f"&status=eq.{status}"
            r = await client.get(q, headers=_headers(), timeout=15)
            rows = r.json() if r.status_code == 200 else []
            tr = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_types?select=id,key,name&order=token_cost",
                headers=_headers(), timeout=10)
            types = tr.json() if tr.status_code == 200 else []
            cr = await client.get(
                f"{SUPABASE_URL}/rest/v1/creator_applications?select=status",
                headers=_headers(), timeout=10)
            counts: dict = {}
            for row in (cr.json() if cr.status_code == 200 else []):
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            return {"applications": rows, "ticket_types": types, "counts": counts}
    except Exception as e:
        logger.warning(f"admin_list_creator_applications error: {e}")
        return empty


async def admin_decide_creator_application(
    app_id: int, admin_id: str, decision: str,
    make_expert: bool = False, ticket_type_ids: list[int] | None = None,
) -> dict:
    """Kabul/red. Kabulde referral kodu (elci mekanigi) baglanir; istenirse
    uzman yetkisi de acilir.

    KOD NEREDEN GELIYOR: referral_code uuid'den DETERMINISTIK turetiliyor
    (_ref_code_for), yani basvuranin bir HESABI olmali. Hesap yoksa kabul
    yine gecerlidir ama kod bos kalir ve kullanici kayit olunca ayni e-posta
    ile eslesip kod uretilir. "Kabul ettim ama link veremedim" durumu
    donuste acikca bildirilir (referral_code=None + note).
    """
    if decision not in ("accepted", "rejected"):
        return {"success": False, "error": "invalid_decision"}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/creator_applications?id=eq.{int(app_id)}&select=*",
                headers=_headers(), timeout=10)
            rows = r.json() if r.status_code == 200 else []
            if not rows:
                return {"success": False, "error": "not_found"}
            app = rows[0]

            user_id = app.get("user_id")
            code = app.get("referral_code")
            note = None
            if decision == "accepted":
                # GUVENLIK (2026-07-30): e-postadan otomatik hesap eslemesi
                # YALNIZ DOGRULANMIS adres icin yapilir.
                # /api/creator-apply kimliksiz ve upsert on_conflict=handle
                # calisiyor; eskiden bir baskasinin @handle'ina POST atip
                # e-postasini uzerine yazmak mumkundu. Burasi user_id'yi tam da
                # o e-postadan esledigi icin saldirgan referral kodunu,
                # sozlesmeyi ve (make_expert ise) is_expert yetkisini
                # devralabiliyordu. Dogrulanmamis adres artik BAGLANMAZ —
                # kabul yine gecerlidir, yalnizca hesap eslemesi beklemede kalir.
                if not user_id and app.get("email"):
                    if app.get("email_verified"):
                        emails = await _fetch_all_auth_emails()
                        hedef = str(app["email"]).strip().lower()
                        for uid, mail in emails.items():
                            if str(mail).strip().lower() == hedef:
                                user_id = uid
                                break
                    else:
                        logger.warning(
                            "creator kabul: e-posta DOGRULANMAMIS, otomatik hesap "
                            "eslemesi atlandi (app=%s)", app_id)
                        note = "eposta_dogrulanmadi"
                if user_id and not code:
                    code = await get_or_create_referral_code(user_id)
                if not user_id and not note:
                    note = "hesap_yok"  # kayit olunca eslenecek

            payload = {
                "status": decision,
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "reviewed_by": admin_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if user_id:
                payload["user_id"] = user_id
            if code:
                payload["referral_code"] = code
            pr = await client.patch(
                f"{SUPABASE_URL}/rest/v1/creator_applications?id=eq.{int(app_id)}",
                headers={**_headers(), "Prefer": "return=representation"},
                json=payload, timeout=10)
            if pr.status_code != 200 or not pr.json():
                return {"success": False, "error": "update_failed"}

            # Uzman yetkisi AYRI bir karar: her kabul edilen creator uzman
            # degildir (barter creator'in teslim yetkinligi olmayabilir).
            if decision == "accepted" and make_expert and user_id:
                await admin_set_is_expert(user_id, True, ticket_type_ids or None)

            # Kabul edilen herkese 1 YILLIK sozlesme kaydi (kurucu karari
            # 2026-07-26). mode: uzman yetkisi verildiyse 'service', yoksa
            # 'referral' (barter/elci). NDA imzasi ve belge baglantilari
            # sonradan ELLE islenir — imzayi kod atamaz.
            if decision == "accepted" and user_id:
                await _ensure_expert_contract(
                    client, user_id,
                    "service" if make_expert else "referral", admin_id,
                    note=f"Creator başvurusu #{app_id} kabulü")

            return {"success": True, "error": None, "user_id": user_id,
                    "referral_code": code, "note": note}
    except Exception as e:
        logger.warning(f"admin_decide_creator_application error: {e}")
        return {"success": False, "error": "exception"}


async def admin_list_contracts() -> dict:
    """Tum uzman/elci sozlesmeleri + kisi adi/e-postasi.

    NEDEN AYRI UC: sozlesme daha once yalnizca admin_get_payouts icinde,
    ODEME KAYDI OLAN uzmanlar icin donuyordu — 0 odeme varken hicbir sozlesme
    gorunmuyordu (2026-07-26). Sozlesme finansal degil HUKUKI bir kayit;
    odemeden bagimsiz listelenmeli.
    """
    empty = {"contracts": []}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return empty
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/expert_contracts?select=*&order=created_at.desc&limit=500",
                headers=_headers(), timeout=15)
            rows = r.json() if r.status_code == 200 else []
            if not rows:
                return empty
            ids = {x["expert_id"] for x in rows if x.get("expert_id")}
            adlar: dict = {}
            if ids:
                id_list = ",".join(f'"{i}"' for i in ids)
                pr = await client.get(
                    f"{SUPABASE_URL}/rest/v1/profiles?id=in.({id_list})&select=id,full_name,is_expert",
                    headers=_headers(), timeout=10)
                for row in (pr.json() if pr.status_code == 200 else []):
                    adlar[row["id"]] = row
            emails = await _fetch_all_auth_emails()
            for x in rows:
                p = adlar.get(x.get("expert_id")) or {}
                x["expert_name"] = p.get("full_name") or (str(x.get("expert_id"))[:8])
                x["expert_email"] = emails.get(x.get("expert_id"), "")
                x["is_expert"] = p.get("is_expert")
            return {"contracts": rows}
    except Exception as e:
        logger.warning(f"admin_list_contracts error: {e}")
        return empty


# Sozlesmede admin'in ELLE isleyebilecegi alanlar. Beyaz liste: expert_id/mode
# gibi kimlik alanlari uctan DEGISTIRILEMEZ (yanlislikla baska uzmanin
# sozlesmesine baglanmasin).
_CONTRACT_EDITABLE = {"nda_signed_at", "nda_doc_url", "contract_url", "status", "ends_at", "note"}
_CONTRACT_STATUS = {"active", "expired", "cancelled"}


async def admin_update_contract(contract_id: int, admin_id: str, fields: dict) -> dict:
    """Sozlesme belgelerini/durumunu gunceller (NDA imza tarihi, belge linkleri).

    Imza tarihini KOD ATAMAZ, admin girer — imza hukuki bir eylem.
    URL alanlari yalnizca https kabul eder (panelde tiklanabilir link olacak;
    javascript:/data: gibi semalar admin tarayicisinda calismasin).
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    temiz = {k: v for k, v in (fields or {}).items() if k in _CONTRACT_EDITABLE}
    if not temiz:
        return {"success": False, "error": "no_fields"}
    if "status" in temiz and temiz["status"] not in _CONTRACT_STATUS:
        return {"success": False, "error": "invalid_status"}
    for alan in ("nda_doc_url", "contract_url"):
        v = temiz.get(alan)
        if v in ("", None):
            temiz[alan] = None
        elif not str(v).startswith("https://"):
            return {"success": False, "error": f"invalid_url:{alan}"}
    temiz["renewed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/expert_contracts?id=eq.{int(contract_id)}",
                headers={**_headers(), "Prefer": "return=representation"},
                json=temiz, timeout=10)
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "update_failed"}
            logger.info(f"sozlesme guncellendi: id={contract_id} admin={admin_id} alanlar={list(temiz)}")
            return {"success": True, "error": None, "contract": r.json()[0]}
    except Exception as e:
        logger.warning(f"admin_update_contract error: {e}")
        return {"success": False, "error": "exception"}


async def admin_get_payouts(period_month: str | None = None) -> dict:
    """Admin muhasebe defteri: uzman/influencer kazanclari (%33 teslim + %10
    referral) kisi-bazli ozet + satirlar + sozlesme durumu. period_month
    'YYYY-MM' ya da 'YYYY-MM-01' verilirse o aya filtreler."""
    empty = {"experts": [], "payouts": [], "totals": {"earned": 0, "paid": 0, "outstanding": 0}}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return empty
    if period_month and len(period_month) == 7:
        period_month = period_month + "-01"
    try:
        async with httpx.AsyncClient() as client:
            q = f"{SUPABASE_URL}/rest/v1/expert_payouts?select=*&order=created_at.desc"
            if period_month:
                q += f"&period_month=eq.{period_month}"
            pr = await client.get(q, headers=_headers(), timeout=15)
            payouts = pr.json() if pr.status_code == 200 else []

            # Ilgili profiller (uzman + musteri) tek seferde
            pids = {p[k] for p in payouts for k in ("expert_id", "customer_id") if p.get(k)}
            profs: dict[str, dict] = {}
            contracts: dict[str, dict] = {}
            if pids:
                id_list = ",".join(f'"{i}"' for i in pids)
                nr = await client.get(
                    f"{SUPABASE_URL}/rest/v1/profiles?id=in.({id_list})&select=id,full_name,instagram_handle,expert_mode",
                    headers=_headers(), timeout=10)
                for row in (nr.json() if nr.status_code == 200 else []):
                    profs[row["id"]] = row
            expert_ids = {p["expert_id"] for p in payouts if p.get("expert_id")}
            if expert_ids:
                cid = ",".join(f'"{i}"' for i in expert_ids)
                cr = await client.get(
                    f"{SUPABASE_URL}/rest/v1/expert_contracts?expert_id=in.({cid})&select=expert_id,mode,starts_at,ends_at,status&order=created_at.desc",
                    headers=_headers(), timeout=10)
                for row in (cr.json() if cr.status_code == 200 else []):
                    contracts.setdefault(row["expert_id"], row)  # order desc -> en yeni
            emails = await _fetch_all_auth_emails()

            def nm(i):
                p = profs.get(i) or {}
                return p.get("full_name") or p.get("instagram_handle") or (str(i)[:8] if i else "")

            lines, agg = [], {}
            for p in payouts:
                eid = p.get("expert_id")
                amt = float(p.get("amount") or 0)
                lines.append({
                    "id": p["id"], "expert_id": eid, "expert_name": nm(eid),
                    "kind": p.get("kind"), "amount": amt,
                    "basis_amount": float(p.get("basis_amount") or 0),
                    "rate": float(p.get("rate") or 0), "currency": p.get("currency") or "USD",
                    "status": p.get("status"), "period_month": p.get("period_month"),
                    "created_at": p.get("created_at"), "paid_at": p.get("paid_at"),
                    "ticket_id": p.get("ticket_id"), "customer_name": nm(p.get("customer_id")),
                    "note": p.get("note"),
                })
                a = agg.setdefault(eid, {
                    "expert_id": eid, "name": nm(eid), "email": emails.get(eid, ""),
                    "expert_mode": (profs.get(eid) or {}).get("expert_mode"),
                    "delivery_earned": 0.0, "referral_earned": 0.0,
                    "total_earned": 0.0, "total_paid": 0.0, "outstanding": 0.0,
                    "contract": contracts.get(eid),
                })
                # F4: void (iptal) satir muhasebe TOPLAMLARINA girmez (earned dahil);
                # satir listesinde yine gorunur. earned = paid + outstanding tutar.
                if p.get("status") != "void":
                    a["total_earned"] += amt
                    a["delivery_earned" if p.get("kind") == "delivery" else "referral_earned"] += amt
                    if p.get("status") == "paid":
                        a["total_paid"] += amt
                    else:
                        a["outstanding"] += amt

            experts = sorted(agg.values(), key=lambda x: x["outstanding"], reverse=True)
            for e in experts:
                for k in ("delivery_earned", "referral_earned", "total_earned", "total_paid", "outstanding"):
                    e[k] = round(e[k], 2)
            totals = {
                "earned": round(sum(e["total_earned"] for e in experts), 2),
                "paid": round(sum(e["total_paid"] for e in experts), 2),
                "outstanding": round(sum(e["outstanding"] for e in experts), 2),
            }
            return {"experts": experts, "payouts": lines, "totals": totals}
    except Exception as e:
        logger.warning(f"admin_get_payouts error: {e}")
        return empty


async def admin_void_payout(payout_id: int, admin_id: str, sebep: str = "") -> bool:
    """Admin bir odeme satirini elle iptal eder.

    NEDEN: denetime kadar (2026-07-26) yanlis/gecersiz bir odeme satirini
    duzeltmenin API yolu YOKTU — yalnizca dogrudan veritabanina dokunmak.
    Ozellikle Apple iadelerinde tutar+musteri heuristigi yanlis satiri
    hedefleyebildigi icin elle duzeltme sart.

    Void GERI ALINMAZ (status=neq.void filtresi): iptal kalicidir, gerekirse
    yeni satir acilir. Boylece defterde iz kaybolmaz.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/expert_payouts?id=eq.{int(payout_id)}&status=neq.void",
                headers={**_headers(), "Prefer": "return=representation"},
                json={"status": "void",
                      "note": f"Admin iptali{(' — ' + sebep) if sebep else ''}"},
                timeout=10,
            )
            if r.status_code != 200:
                return False
            rows = r.json()
            if rows:
                logger.warning(f"admin_void_payout: id={payout_id} admin={admin_id} sebep={sebep}")
            return isinstance(rows, list) and len(rows) > 0
    except Exception as e:
        logger.warning(f"admin_void_payout error: {e}")
        return False


async def admin_mark_payout_paid(payout_id: int, admin_id: str, paid: bool = True) -> bool:
    """Bir payout satirini odendi/beklemede olarak isaretler (admin muhasebe)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            body = ({"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat(), "paid_by": admin_id}
                    if paid else {"status": "pending", "paid_at": None, "paid_by": None})
            # F4: void satir degistirilemez (status=neq.void -> un-void / void'i odeme yok).
            # return=representation ile eslesent satir sayisini dogrula: yok/void id'de
            # PostgREST 0-satirda da 200 doner, sahte basari vermesin.
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/expert_payouts?id=eq.{int(payout_id)}&status=neq.void",
                headers={**_headers(), "Prefer": "return=representation"}, json=body, timeout=10)
            if r.status_code != 200:
                return False
            rows = r.json()
            return isinstance(rows, list) and len(rows) > 0
    except Exception as e:
        logger.warning(f"admin_mark_payout_paid error: {e}")
        return False


async def rate_ticket(ticket_id: int, rater_id: str, stars: int, comment: str = "") -> dict:
    """Cift yonlu puanlama. Rol biletten cikarilir: bilet sahibi ->
    'customer' (uzmani puanlar), atanan uzman -> 'expert' (musteriyi puanlar).
    Yalnizca is bittiginde (submitted/verified/disputed) puanlanabilir; bilet
    basina her yon TEK puan. Uzman kimligi musteriye HIC gosterilmez -
    musteri sadece skoru yazar, kimi puanladigini gormez."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    if not isinstance(stars, int) or not (1 <= stars <= 5):
        return {"success": False, "error": "invalid_stars"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=user_id,assigned_expert_id,status",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "not_found"}
            t = r.json()[0]
            if t["status"] not in ("submitted", "verified", "disputed"):
                return {"success": False, "error": "too_early"}
            if rater_id == t.get("user_id"):
                role, ratee = "customer", t.get("assigned_expert_id")
            elif rater_id == t.get("assigned_expert_id"):
                role, ratee = "expert", t.get("user_id")
            else:
                return {"success": False, "error": "not_participant"}
            if not ratee:
                return {"success": False, "error": "no_counterparty"}
            # QA 2026-07-19: puan "bilet basina her yon TEK" olmali ama upsert
            # (merge-duplicates) suresiz uzerine yazmaya izin veriyordu. Once mevcut
            # puani kontrol et; varsa reddet. Yaris durumunda DB unique(ticket_id,
            # rater_role) kisiti ikinci INSERT'i 409 ile dusurur (asagida yakalanir).
            ex = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_ratings?ticket_id=eq.{ticket_id}&rater_role=eq.{role}&select=id",
                headers=_headers(), timeout=10,
            )
            if ex.status_code == 200 and ex.json():
                return {"success": False, "error": "already_rated"}
            up = await client.post(
                f"{SUPABASE_URL}/rest/v1/ticket_ratings",
                headers={**_headers(), "Prefer": "return=minimal"},
                json={"ticket_id": ticket_id, "rater_role": role, "rater_id": rater_id,
                      "ratee_id": ratee, "stars": stars, "comment": (comment or "").strip() or None},
                timeout=10,
            )
            if up.status_code == 409:  # unique kisiti: eszamanli ikinci puan
                return {"success": False, "error": "already_rated"}
            if up.status_code not in (200, 201, 204):
                return {"success": False, "error": "save_failed"}
            return {"success": True, "role": role, "error": None}
    except Exception as e:
        logger.warning(f"rate_ticket error: {e}")
        return {"success": False, "error": "exception"}


async def get_ticket_rating_state(ticket_id: int, user_id: str) -> dict:
    """Bir kullanicinin bu bileti puanlayip puanlayamayacagi + verdigi puan.
    Musteri karsi tarafin (uzmanin) kimligini ASLA gormez."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=user_id,assigned_expert_id,status",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {}
            t = r.json()[0]
            role = "customer" if user_id == t.get("user_id") else ("expert" if user_id == t.get("assigned_expert_id") else None)
            if not role:
                return {}
            can = t["status"] in ("submitted", "verified", "disputed")
            rr = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_ratings?ticket_id=eq.{ticket_id}&rater_role=eq.{role}&select=stars,comment",
                headers=_headers(), timeout=10,
            )
            mine = rr.json()[0] if rr.status_code == 200 and rr.json() else None
            return {"role": role, "can_rate": can, "my_rating": mine}
    except Exception as e:
        logger.warning(f"get_ticket_rating_state error: {e}")
    return {}


async def get_customer_reputation(user_id: str) -> dict:
    """Bir musterinin uzmanlardan aldigi puan ozeti (rater_role='expert').
    Atanan uzman ve admin bunu gorur — sorunlu musteriyi onceden tanimak icin."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"avg_rating": None, "rating_count": 0}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_ratings?ratee_id=eq.{user_id}&rater_role=eq.expert&select=stars",
                headers=_headers(), timeout=10,
            )
            rows = r.json() if r.status_code == 200 else []
            if not rows:
                return {"avg_rating": None, "rating_count": 0}
            return {"avg_rating": round(sum(x["stars"] for x in rows) / len(rows), 2),
                    "rating_count": len(rows)}
    except Exception as e:
        logger.warning(f"get_customer_reputation error: {e}")
    return {"avg_rating": None, "rating_count": 0}


async def notify_experts_new_task(ticket_id: int) -> None:
    """Yeni (atanmamis) bir gorev musait oldugunda, o gorev turunu yapabilen
    uzmanlarin cihazlarina push gonderir - uzman uygulamayi acip isi gorur.
    Yalnizca uzmanlik alani eslesen uzmanlara gider."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=ticket_type_id,ref_code",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return
            t = r.json()[0]
            tt_id = t["ticket_type_id"]
            # Bu turu yapabilen uzmanlar
            sp = await client.get(
                f"{SUPABASE_URL}/rest/v1/expert_ticket_types?ticket_type_id=eq.{tt_id}&select=expert_id",
                headers=_headers(), timeout=10,
            )
            expert_ids = [row["expert_id"] for row in (sp.json() if sp.status_code == 200 else [])]
            # Gorev turu adi
            tn = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_types?id=eq.{tt_id}&select=name",
                headers=_headers(), timeout=10,
            )
            task_name = (tn.json()[0]["name"] if tn.status_code == 200 and tn.json() else "Yeni görev")
    except Exception as e:
        logger.warning(f"notify_experts_new_task error: {e}")
        return
    if not expert_ids:
        return
    from pushnotify import send_new_task_push
    for eid in expert_ids:
        await send_new_task_push(eid, task_name, t.get("ref_code", ""))


async def get_ticket_role(ticket_id: int, user_id: str) -> tuple[str | None, dict | None]:
    """Returns (role, ticket_row) where role is 'customer' (bought it),
    'expert' (assigned to it), 'admin' (has the tickets scope), or None if
    the caller has no business seeing this ticket at all. is_strict_admin
    and has_admin_scope are checked here rather than trusted from the
    caller, since a ticket's messages can contain real customer/expert
    conversation - access must be verified per-ticket, not just per-role."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None, None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return None, None
            ticket = r.json()[0]
    except Exception as e:
        logger.warning(f"get_ticket_role error: {e}")
        return None, None

    # Oncelik: expert > admin > customer. Ayni hesap hem musteri hem uzman/
    # admin olabildiginde (tek kisilik ekip, test) yazma yetkileri (gorev
    # isaretleme, sablon) kaybolmasin diye en yetkili rol kazanir; musteriye
    # ozel isler (itiraz) zaten role degil user_id sahipligine bakar.
    if ticket.get("assigned_expert_id") == user_id:
        return "expert", ticket
    if await has_admin_scope(user_id, "tickets"):
        return "admin", ticket
    if ticket.get("user_id") == user_id:
        return "customer", ticket
    return None, ticket


async def list_ticket_messages(ticket_id: int, viewer_role: str = "customer") -> list:
    """C-4: author_email mesaj-seviyesinde ROL KAPILI. Müşteri (viewer_role=
    'customer') gerçek e-posta ADRESLERİNİ GÖRMEZ — mesajlar author_role
    etiketiyle ('GEONI Uzmanı' vb.) sunulur; aksi halde atanan uzmanın kişisel
    e-postası müşteriye sızardı. Yalnız staff (expert/admin) e-postaları görür
    (koordinasyon). how_to gating (C-1) ile aynı desen."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/ticket_messages?ticket_id=eq.{ticket_id}&select=*&order=created_at.asc",
                headers=_headers(), timeout=10,
            )
            messages = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"list_ticket_messages error: {e}")
        return []
    if viewer_role in ("expert", "admin"):
        emails = await _fetch_all_auth_emails()
        for m in messages:
            m["author_email"] = emails.get(m.get("author_id"), "")
    else:
        for m in messages:  # müşteri: e-posta sızdırma, yalnız rol etiketi
            m["author_email"] = ""
    return messages


async def add_ticket_message(ticket_id: int, author_id: str | None, author_role: str, body: str = "", attachment_url: str = "", attachment_name: str = "") -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    if not body and not attachment_url:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/ticket_messages",
                headers=_headers(),
                json={
                    "ticket_id": ticket_id, "author_id": author_id, "author_role": author_role,
                    "body": body or None, "attachment_url": attachment_url or None, "attachment_name": attachment_name or None,
                },
                timeout=10,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"add_ticket_message error: {e}")
    return False


async def create_ticket_upload_url(ticket_id: int, filename: str) -> dict | None:
    """Signed upload URL scoped to this ticket's own folder in the
    ticket-attachments bucket. Returns path+token so the frontend can use
    supabase-js's own storage.from(...).uploadToSignedUrl() rather than us
    guessing the raw HTTP verb/headers Storage expects - our backend never
    handles the file bytes either way. The path is namespaced by ticket_id
    so one ticket's uploads can't collide with or overwrite another's.
    Bucket public oldugundan yola TAHMIN EDILEMEZ bir uuid segmenti eklenir:
    ticket_id sirali bir sayi oldugundan onsuz dosya yolu enumerate
    edilebilirdi; uuid ile yalnizca linki bilen erisir (2026-07-14)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-") or "file"
    path = f"{ticket_id}/{uuid.uuid4().hex}_{safe_name}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/storage/v1/object/upload/sign/ticket-attachments/{path}",
                headers=_headers(), json={"expiresIn": 300}, timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                token = parse_qs(urlparse(data.get("url", "")).query).get("token", [""])[0]
                return {
                    "path": path, "token": token,
                    "public_url": f"{SUPABASE_URL}/storage/v1/object/public/ticket-attachments/{path}",
                }
    except Exception as e:
        logger.warning(f"create_ticket_upload_url error: {e}")
    return None


async def upload_ticket_file(ticket_id: int, filename: str, content: str,
                             content_type: str = "text/plain; charset=utf-8") -> str | None:
    """Sunucu tarafindan uretilen bir dosyayi (or. otomatik teslimdeki
    robots.txt/llms.txt) ticket-attachments bucket'ina yukler ve herkese
    acik URL'ini dondurur - musteri bunu siteyi yapan kisiyle paylasabilir.
    Bucket public oldugundan URL kimlik dogrulamasi gerektirmez; bu yuzden
    yola tahmin edilemez uuid segmenti eklenir (ticket_id sirali oldugundan
    onsuz enumerate edilebilirdi, 2026-07-14)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-") or "file"
    path = f"{ticket_id}/{uuid.uuid4().hex}_{safe_name}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/storage/v1/object/ticket-attachments/{path}",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": content_type,
                    "x-upsert": "true",
                },
                content=content.encode("utf-8"),
                timeout=15,
            )
            if r.status_code in (200, 201):
                return f"{SUPABASE_URL}/storage/v1/object/public/ticket-attachments/{path}"
            logger.warning(f"upload_ticket_file bad status {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"upload_ticket_file error: {e}")
    return None


# ── Veri saklama / retention temizligi ───────────────────────────────────
# Ilke: hicbir dosya/rapor sonsuza saklanmaz (depolama maliyeti + KVKK veri-
# minimizasyonu + sizan public URL'nin omrunu sinirlama). Dort is:
#  (A) hesap silinince kullanicinin storage dosyalari da silinir (delete_user_account),
#  (B) verified biletin ekleri RETENTION_ATTACHMENT_DAYS gun sonra silinir
#      (RETENTION_WARN_DAYS gun once bir kez uyari e-postasi),
#  (C) ayni (kullanici+tur+hedef) rapor tekrar taraninca/eskiyince sadelestirilir
#      (apply_audit_retention RPC; skor+tarih trend grafigi icin kalir),
#  (D) prepaid saglayici bakiyesi LOW_BALANCE_THRESHOLD_USD altina dusunce admin uyarilir.
# Gunluk isler (B,C) monitor_loop icinden _claim_daily_job ile TEK sefer kosar
# (cok-instance guvenli). Dusuk-bakiye uyarisi (D) her turda debounce'lu kosar.

RETENTION_ATTACHMENT_DAYS = int(os.environ.get("RETENTION_ATTACHMENT_DAYS", "30"))
RETENTION_WARN_DAYS = int(os.environ.get("RETENTION_WARN_DAYS", "7"))
RETENTION_AUDIT_DAYS = int(os.environ.get("RETENTION_AUDIT_DAYS", "30"))
LOW_BALANCE_THRESHOLD_USD = float(os.environ.get("LOW_BALANCE_THRESHOLD_USD", "5"))
# Saglayici para birimi: Gemini faturalandirmasi Google billing hesabi TRY
# oldugundan hem harcama (BigQuery cost) hem top-up TRY'dir (usd_all_time alan
# adina ragmen deger TRY). Digerleri USD. Esik USD oldugundan TRY bakiye
# USD_TRY_RATE ile USD'ye cevrilip karsilastirilir (yaklasik; env ile guncellenir).
PROVIDER_CURRENCY = {"gemini": "TRY"}
# Fail-safe varsayilan: FX API cekilemezse kullanilir (yaklasik).
USD_TRY_RATE = float(os.environ.get("USD_TRY_RATE", "40"))

_FX_CACHE = {"rate": None, "at": None}
_FX_TTL = timedelta(hours=24)   # kur gunde bir kez yenilenir


async def _get_usd_try_rate() -> float:
    """Guncel USD/TRY kuru — gemini'nin TRY bakiyesini USD esigiyle
    karsilastirmak icin. Ucretsiz/anahtarsiz kaynak (open.er-api.com),
    12 saat cache. Basarisizsa son bilinen degere, o da yoksa USD_TRY_RATE
    env varsayilanina duser (alarm hicbir zaman FX yuzunden patlamaz)."""
    now = datetime.now(timezone.utc)
    if _FX_CACHE["rate"] and _FX_CACHE["at"] and now - _FX_CACHE["at"] < _FX_TTL:
        return _FX_CACHE["rate"]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://open.er-api.com/v6/latest/USD", timeout=10)
            if r.status_code == 200:
                rate = (r.json().get("rates") or {}).get("TRY")
                if rate and float(rate) > 0:
                    _FX_CACHE["rate"] = float(rate)
                    _FX_CACHE["at"] = now
                    return float(rate)
    except Exception as e:
        logger.warning(f"USD/TRY kur cekme hatasi: {e}")
    return _FX_CACHE["rate"] or USD_TRY_RATE


def _to_usd(amount: float, currency: str, rate: float | None = None) -> float:
    r = rate if rate is not None else USD_TRY_RATE
    if currency == "TRY" and r:
        return amount / r
    return amount


def _storage_headers() -> dict:
    # _headers() 'Prefer: return=minimal' iceriyor; storage list yaniti
    # gerektirdiginden ayri (minimal'siz) baslik kullanilir.
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


async def _delete_attachment_files_for_tickets(client: httpx.AsyncClient, ticket_ids: list) -> int:
    """Verilen biletlere ait TUM storage dosyalarini ({ticket_id}/ prefix'i:
    musteri ekleri + uretilen teslimatlar) kalici siler. Storage REST API
    uzerinden silinir ki S3'te yetim dosya kalmasin; en iyi caba (biri patlasa
    da digerleri silinir). Hesap silme (A) ve retention (B) ayni yardimciyi kullanir."""
    deleted = 0
    for tid in ticket_ids:
        try:
            lr = await client.post(
                f"{SUPABASE_URL}/storage/v1/object/list/ticket-attachments",
                headers=_storage_headers(),
                json={"prefix": f"{tid}/", "limit": 1000},
                timeout=15,
            )
            if lr.status_code != 200:
                continue
            paths = [f"{tid}/{o['name']}" for o in lr.json()
                     if isinstance(o, dict) and o.get("name")]
            for i in range(0, len(paths), 100):
                chunk = paths[i:i + 100]
                dr = await client.request(
                    "DELETE",
                    f"{SUPABASE_URL}/storage/v1/object/ticket-attachments",
                    headers=_storage_headers(),
                    json={"prefixes": chunk},
                    timeout=30,
                )
                if dr.status_code == 200:
                    deleted += len(chunk)
        except Exception as e:
            logger.warning(f"attachment purge error (ticket {tid}): {e}")
    return deleted


async def _claim_daily_job(job_key: str) -> bool:
    """Cok-instance guvenli gunluk kilit: app_config'te job satirini BUGUNE
    atomik gunceller. Yalnizca degeri bugunden eski olan instance True alir
    (isi o kosar); ayni gun ikinci cagri False doner. Satir yoksa olusturan
    kazanir (ignore-duplicates)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    now_iso = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"job_last_run:{job_key}"
    try:
        async with httpx.AsyncClient() as client:
            # Kosullu atomik UPDATE: yalnizca value<today olan satiri bugune tasi.
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/app_config?key=eq.{key}&value=lt.{today}",
                headers={**_headers(), "Prefer": "return=representation"},
                json={"value": today, "updated_at": now_iso},
                timeout=10,
            )
            if r.status_code == 200 and r.json():
                return True  # bizim UPDATE'imiz satiri bugune tasidi -> kilit bizde
            # Satir yok VEYA zaten bugun -> yoksa biz olusturmaya calisalim.
            ins = await client.post(
                f"{SUPABASE_URL}/rest/v1/app_config",
                headers={**_headers(),
                         "Prefer": "return=representation,resolution=ignore-duplicates"},
                json={"key": key, "value": today, "updated_at": now_iso},
                timeout=10,
            )
            if ins.status_code in (200, 201) and ins.json():
                return True  # satiri ilk defa biz olusturduk -> kilit bizde
    except Exception as e:
        logger.warning(f"_claim_daily_job({job_key}) error: {e}")
    return False


async def run_audit_retention(user_id: str | None = None) -> int:
    """(C) apply_audit_retention RPC: superseded/eski tam raporlari sadelestirir.
    Tarama bitince user_id ile (o kullaniciyi hemen), gunluk isde None (tum tablo)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/apply_audit_retention",
                headers=_headers(),
                json={"p_retention_days": RETENTION_AUDIT_DAYS, "p_user_id": user_id},
                timeout=20,
            )
            if r.status_code == 200:
                try:
                    return int(r.json())
                except Exception:
                    return 0
    except Exception as e:
        logger.warning(f"run_audit_retention error: {e}")
    return 0


async def run_attachment_retention() -> dict:
    """(B) verified+RETENTION_ATTACHMENT_DAYS dolan biletlerin dosyalarini siler;
    RETENTION_WARN_DAYS gun once bir kez uyari e-postasi atar. Acik/islemdeki
    biletlere dokunmaz. Damgalarla (warned/purged) idempotent."""
    out = {"warned": 0, "purged_tickets": 0, "deleted_files": 0}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return out
    now = datetime.now(timezone.utc)
    warn_cut = (now - timedelta(days=max(RETENTION_ATTACHMENT_DAYS - RETENTION_WARN_DAYS, 0))).isoformat()
    del_cut = (now - timedelta(days=RETENTION_ATTACHMENT_DAYS)).isoformat()
    try:
        async with httpx.AsyncClient() as client:
            # 1) UYARI: silme penceresine RETENTION_WARN_DAYS kala, henuz
            #    uyarilmamis ve silinmemis biletler.
            wr = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets"
                f"?status=eq.verified&verified_at=lte.{warn_cut}"
                f"&attachments_purge_warned_at=is.null&attachments_purged_at=is.null"
                f"&select=id,user_id,ref_code",
                headers=_headers(), timeout=15,
            )
            for t in (wr.json() if wr.status_code == 200 else []):
                tid = t["id"]
                has_files = False
                try:
                    lr = await client.post(
                        f"{SUPABASE_URL}/storage/v1/object/list/ticket-attachments",
                        headers=_storage_headers(),
                        json={"prefix": f"{tid}/", "limit": 1}, timeout=10)
                    has_files = lr.status_code == 200 and bool(lr.json())
                except Exception:
                    pass
                if has_files:
                    email = await get_auth_email(t.get("user_id"))
                    if email:
                        await send_retention_warning_email(
                            email, t.get("ref_code") or f"#{tid}", RETENTION_WARN_DAYS)
                    out["warned"] += 1
                # dosyasiz olsa da isaretle ki her gun tekrar bakilmasin
                await client.patch(
                    f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{tid}",
                    headers=_headers(),
                    json={"attachments_purge_warned_at": now.isoformat()}, timeout=10)
            # 2) SIL: verified_at <= del_cut, henuz silinmemis.
            dr = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets"
                f"?status=eq.verified&verified_at=lte.{del_cut}"
                f"&attachments_purged_at=is.null&select=id",
                headers=_headers(), timeout=15,
            )
            ids = [t["id"] for t in (dr.json() if dr.status_code == 200 else [])]
            if ids:
                out["deleted_files"] = await _delete_attachment_files_for_tickets(client, ids)
                out["purged_tickets"] = len(ids)
                for tid in ids:
                    await client.patch(
                        f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{tid}",
                        headers=_headers(),
                        json={"attachments_purged_at": now.isoformat()}, timeout=10)
    except Exception as e:
        logger.warning(f"run_attachment_retention error: {e}")
    return out


async def get_provider_remaining_balances() -> list:
    """(D) Prepaid saglayici kalan bakiyesi = toplam topup - tum-zaman harcama.
    Yalnizca topup'i loglanmis (prepaid) VE harcamasi hesaplanabilen
    saglayicilar dahil (yanlis alarma girmemek icin). [{provider,remaining,...}]."""
    try:
        from openai_admin import get_openai_cost_summary
        from anthropic_admin import get_anthropic_cost_summary
        from gemini_admin import get_gemini_cost_summary
        from perplexity_admin import get_perplexity_cost_summary
        from grok_admin import get_grok_cost_summary
    except Exception as e:
        logger.warning(f"remaining balance import error: {e}")
        return []
    providers = {
        "openai": get_openai_cost_summary,
        "anthropic": get_anthropic_cost_summary,
        "gemini": get_gemini_cost_summary,
        "perplexity": get_perplexity_cost_summary,
        "grok": get_grok_cost_summary,  # xAI prepaid; topup loglanmissa dusuk-bakiye uyarisina girer
    }
    fx = await _get_usd_try_rate()   # canli USD/TRY (gunde bir cekilir, cache'li)
    out = []
    for name, fn in providers.items():
        try:
            topups = await get_manual_topups_total(name)
            if not topups or topups <= 0:
                continue  # prepaid degil / topup loglanmamis
            summ = await fn()
            spend = (summ or {}).get("usd_all_time")
            if spend is None:
                continue  # harcama hesaplanamadi -> yanlis alarm verme
            currency = PROVIDER_CURRENCY.get(name, "USD")
            remaining = round(topups - spend, 2)
            out.append({
                "provider": name,
                "currency": currency,
                "remaining": remaining,                                   # kendi para biriminde
                "remaining_usd": round(_to_usd(remaining, currency, fx), 2),  # esik karsilastirmasi (canli kur)
                "topups": round(topups, 2),
                "spend": round(spend, 2),
            })
        except Exception as e:
            logger.warning(f"remaining balance ({name}) error: {e}")
    return out


async def run_low_balance_alert() -> list:
    """(D) Kalan bakiyesi LOW_BALANCE_THRESHOLD_USD altina dusen prepaid
    saglayicilar icin admin'e TEK uyari e-postasi. Debounce (app_config):
    bir saglayici esigin altindayken gunde en fazla bir kez uyarilir; esigin
    ustune cikinca durumu silinir (tekrar dusunce yeniden uyarir)."""
    # Esik USD; TRY saglayicilar (gemini) USD karsiligiyla karsilastirilir.
    below = [b for b in await get_provider_remaining_balances()
             if b.get("remaining_usd", b["remaining"]) < LOW_BALANCE_THRESHOLD_USD]
    key = "low_balance_alerted"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_alert: list = []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/app_config?key=eq.{key}&select=value",
                headers=_headers(), timeout=10)
            prev = {}
            if r.status_code == 200 and r.json():
                try:
                    prev = json.loads(r.json()[0].get("value") or "{}")
                except Exception:
                    prev = {}
            # bugun bu saglayici icin daha once uyarilmadiysa uyar
            to_alert = [b for b in below if prev.get(b["provider"]) != today]
            if to_alert:
                for adm in await _ticket_admin_emails():
                    await send_low_balance_alert_email(adm, to_alert, LOW_BALANCE_THRESHOLD_USD)
            # yeni durum: yalnizca esigin ALTINDAKILER bugunun tarihiyle tutulur
            # (ustune cikan otomatik silinir -> tekrar dusunce yeniden uyarilir)
            new_state = {b["provider"]: today for b in below}
            await client.post(
                f"{SUPABASE_URL}/rest/v1/app_config",
                headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
                json={"key": key, "value": json.dumps(new_state),
                      "updated_at": datetime.now(timezone.utc).isoformat()},
                timeout=10)
    except Exception as e:
        logger.warning(f"run_low_balance_alert error: {e}")
    return to_alert


# ── Bilet e-posta bildirimleri ───────────────────────────────────────────
# Bilet sistemindeki en kritik islevsel eksik buydu: mesaj/atama/teslim/
# onay olaylarinda kimseye haber gitmiyordu, herkes panele girip kirmizi
# nokta aramak zorundaydi (ticket #1 gunlerce bu yuzden atanmamis kaldi).
# Hepsi fire-and-forget: asyncio.create_task ile ateslenir, mail servisi
# yavas/kapali olsa bile endpoint yaniti gecikmez.

from mailer import (  # noqa: E402  (dosya sonu import: dairesel degil, mailer db'yi kullanmiyor)
    send_ticket_email,
    send_retention_warning_email,
    send_low_balance_alert_email,
)


async def _ticket_admin_emails() -> list:
    """is_admin + bilet yetkisi olan adminlerin e-postalari."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?is_admin=eq.true&admin_scope_tickets=eq.true&select=id",
                headers=_headers(), timeout=10,
            )
            ids = [row["id"] for row in r.json()] if r.status_code == 200 else []
        emails = await _fetch_all_auth_emails()
        return [emails[i] for i in ids if emails.get(i)]
    except Exception as e:
        logger.warning(f"_ticket_admin_emails error: {e}")
        return []


async def notify_ticket_event(ticket_id: int, event: str, actor_role: str = "") -> None:
    """event: message | assigned | submitted | verified | returned"""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=*",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return
            ticket = r.json()[0]
        emails = await _fetch_all_auth_emails()
        customer = emails.get(ticket.get("user_id"), "")
        expert = emails.get(ticket.get("assigned_expert_id"), "") if ticket.get("assigned_expert_id") else ""
        types = await list_ticket_types(active_only=False)
        tt = next((t for t in types if t["id"] == ticket.get("ticket_type_id")), {})
        name = tt.get("name", "Hizmet")
        # Guvenlik: name + target kullanici girdisi ve e-posta HTML'ine (admin
        # dahil) giriyor. &<> escape (quote=False -> subject satiri temiz kalir).
        _name_e = html.escape(str(name), quote=False)
        _tgt = ticket.get("target")
        ref = f"{ticket.get('ref_code') or ('#' + str(ticket_id))} · {_name_e}" + (f" ({html.escape(str(_tgt), quote=False)})" if _tgt else "")

        sends = []  # (email, subject, heading, lines)
        if event == "message":
            line = [f"{ref} biletinde yeni bir mesaj var."]
            if actor_role == "customer":
                target = expert or None
                if target:
                    sends.append((target, f"Yeni mesaj — {ref}", "Müşteriden yeni mesaj", line))
                else:
                    for adm in await _ticket_admin_emails():
                        sends.append((adm, f"Yeni mesaj — {ref}", "Atanmamış bilette müşteri mesajı", line))
            else:
                if customer:
                    sends.append((customer, f"Yeni mesaj — {ref}", "Biletinize yanıt geldi", line))
        elif event == "assigned":
            if expert:
                sends.append((expert, f"Yeni iş atandı — {ref}", "Size yeni bir iş atandı",
                              [f"{ref} bileti size atandı.", "İş kırılımını inceleyip 'İşe Başladım' ile başlayabilirsiniz."]))
        elif event == "submitted":
            for adm in await _ticket_admin_emails():
                sends.append((adm, f"Onay bekliyor — {ref}", "Teslim onayınızı bekliyor",
                              [f"{ref} bileti teslim edildi ve onayınızı bekliyor."]))
            if customer:
                # C-1: cikti(lar) zaten bilet mesajlarinda GORUNUR; "sonra
                # iletilecek" yanilsamasi verme (otomatik hizmetlerde aninda
                # dusuyor). Dururstce: gorunur + kisa son kontrol.
                sends.append((customer, f"İşiniz teslim edildi — {ref}", "İşiniz tamamlandı, son kontrolde",
                              ["İşiniz teslim edildi — çıktılar bilet mesajlarınızda görünür. Kısa bir son kalite kontrolünden geçiyor."]))
        elif event == "verified":
            if customer:
                sends.append((customer, f"İşiniz hazır — {ref}", "İşiniz onaylandı ve teslim edildi",
                              ["İş teslim raporu ve tüm çıktılar bilet mesajlarınızda."]))
            if expert:
                sends.append((expert, f"Teslim onaylandı — {ref}", "Teslimatınız onaylandı", [f"{ref} bileti onaylandı. Teşekkürler!"]))
        elif event == "returned":
            if expert:
                sends.append((expert, f"Düzeltme istendi — {ref}", "Teslim düzeltme için iade edildi",
                              ["Gerekçe bilet mesajlarında. Düzeltip yeniden teslim edebilirsiniz."]))
        elif event == "disputed":
            for adm in await _ticket_admin_emails():
                sends.append((adm, f"Müşteri itirazı — {ref}", "Onaylanmış işe müşteri itirazı",
                              ["İtiraz gerekçesi bilet mesajlarında. Yeniden onaylayabilir veya uzmana iade edebilirsiniz."]))
            if expert:
                sends.append((expert, f"Müşteri itirazı — {ref}", "Teslimatınıza itiraz edildi",
                              ["Admin kararını bekleyin; gerekirse bilet size iade edilecek."]))

        # Her alici icin dogru sekme + BILETE ozel URL: uzman -> expert
        # sekmesi, musteri -> my_tickets, admin -> tickets. Frontend bu
        # parametrelerle acilista ilgili sekmeyi ve bileti otomatik acar.
        for to, subject, heading, lines in sends:
            if to == expert:
                tab = "expert"
            elif to == customer:
                tab = "my_tickets"
            else:
                tab = "tickets"
            cta_url = f"https://app.geoni.ai/dashboard?tab={tab}&ticket={ticket_id}"
            asyncio.create_task(send_ticket_email(to, subject, heading, lines, cta_url=cta_url))
    except Exception as e:
        logger.warning(f"notify_ticket_event error: {e}")


async def confirm_ticket(ticket_id: int, user_id: str) -> dict:
    """Musteri teslimi onaylar: yalnizca bilet sahibi, yalnizca 'submitted'
    durumda -> 'verified' (is biter). Admin onayini beklemeden musteri kendi
    kapatir; sorun olursa 'verified' uzerinden itiraz edip admin'e tasiyabilir
    (kullanici karari: "musteri tamam aldim desin is bitsin; itiraz varsa
    admin bakar"). verified_by'a musterinin kendi id'si yazilir."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=user_id,status",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "not_found"}
            row = r.json()[0]
            if row.get("user_id") != user_id:
                return {"success": False, "error": "not_owner"}
            if row.get("status") != "submitted":
                return {"success": False, "error": "invalid_status"}
            patch = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(),
                json={
                    "status": "verified",
                    "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "verified_by": user_id,
                },
                timeout=10,
            )
            if patch.status_code not in (200, 204):
                return {"success": False, "error": "update_failed"}
        await add_ticket_message(ticket_id, user_id, "customer", body="✅ Teslimi onayladım, teşekkürler.")
        return {"success": True, "error": None}
    except Exception as e:
        logger.warning(f"confirm_ticket error: {e}")
        return {"success": False, "error": "exception"}


async def dispute_ticket(ticket_id: int, user_id: str, reason: str) -> dict:
    """Musteri itirazi: yalnizca bilet sahibi, yalnizca 'verified' durumda.
    Itiraz gerekcesi konusma akisina musteri mesaji olarak da duser -
    admin karari (yeniden onay ya da uzmana iade) mevcut verify akisiyla
    verilir; is admin onayi olmadan kapanmis sayilmaz (kullanici karari:
    "musteriye birakirsan is bitmez ama itiraz hakki olmali")."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"success": False, "error": "not_configured"}
    if not reason.strip():
        return {"success": False, "error": "reason_required"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}&select=user_id,status",
                headers=_headers(), timeout=10,
            )
            if r.status_code != 200 or not r.json():
                return {"success": False, "error": "not_found"}
            row = r.json()[0]
            if row.get("user_id") != user_id:
                return {"success": False, "error": "not_owner"}
            if row.get("status") != "verified":
                return {"success": False, "error": "invalid_status"}
            patch = await client.patch(
                f"{SUPABASE_URL}/rest/v1/tickets?id=eq.{ticket_id}",
                headers=_headers(), json={"status": "disputed"}, timeout=10,
            )
            if patch.status_code not in (200, 204):
                return {"success": False, "error": "update_failed"}
        await add_ticket_message(ticket_id, user_id, "customer", body=f"⚠ İtiraz: {reason.strip()}")
        return {"success": True, "error": None}
    except Exception as e:
        logger.warning(f"dispute_ticket error: {e}")
        return {"success": False, "error": "exception"}


# ── Izleme v2: haftalik otomatik yeniden tarama (monitor.py kullanir) ────────

async def list_due_watchlist_items(limit: int = 3, interval_days: int = 7) -> list:
    """
    Sirasi gelen izleme kayitlarini dondurur: izleme acik VE hic otomatik
    taranmamis ya da son otomatik taramasi interval_days'ten eski.
    limit dusuk tutulur (donem basina az is) — API kotalarini korur.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=interval_days)).isoformat()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/watchlist",
                headers=_headers(),
                params={
                    "monitor_enabled": "eq.true",
                    "or": f"(last_auto_scan_at.is.null,last_auto_scan_at.lt.{cutoff})",
                    "order": "last_auto_scan_at.asc.nullsfirst",
                    "limit": str(limit),
                },
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
            logger.warning(f"list_due_watchlist_items failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"list_due_watchlist_items error: {e}")
    return []


async def update_watchlist_after_scan(item_id: str, score) -> bool:
    """Otomatik tarama sonrasi izleme kaydini gunceller (zaman + son skor)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    from datetime import datetime, timezone
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/watchlist",
                headers=_headers(),
                params={"id": f"eq.{item_id}"},
                json={
                    "last_auto_scan_at": datetime.now(timezone.utc).isoformat(),
                    **({"last_score": int(score)} if score is not None else {}),
                },
                timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"update_watchlist_after_scan error: {e}")
    return False


async def get_auth_email(user_id: str) -> str:
    """Tek kullanicinin e-postasini auth admin API'sinden ceker (izleme bildirimi icin)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return ""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                headers=_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                return r.json().get("email", "") or ""
    except Exception as e:
        logger.warning(f"get_auth_email error: {e}")
    return ""


async def get_credit_balance(user_id: str) -> int:
    """Kullanicinin guncel token bakiyesi (izleme ucretlendirme kapisi icin)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not user_id:
        return 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=credit_balance",
                headers=_headers(),
                timeout=10,
            )
            if r.status_code == 200 and r.json():
                return int(r.json()[0].get("credit_balance") or 0)
    except Exception as e:
        logger.warning(f"get_credit_balance error: {e}")
    return 0


def hedef_anahtari(s: str) -> str:
    """Hedef (ad/alan adi) kimlik anahtari: bosluk ve buyuk-kucuk harf farkini siler.

    "Sabri Çağrı Çakır", "Sabri çağrı çakır", "Sabri Çağrı Çakır " ve
    "Sabri  Çağrı Çakır" AYNI kisidir. Eskiden `eq.` ile birebir eslesme
    yapiliyordu; 2026-07-30 olcumunde tek kisi 6 FARKLI yazimla 37 tarama
    uretmisti. Sonuc: skor gecmisi parcalaniyor (kullanici "onceki taramam
    nerede" diyor) ve score_stability yanlis/eksik referansa gore hesaplaniyor.
    casefold() lower()'dan guclu: Almanca ß gibi durumlari da normalize eder.

    BILINEN SINIR: casefold() Turkce yerelini bilmez — "ÇAĞRI" (noktasiz I)
    "çağri", "Çağrı" ise "çağrı" olur; tamami buyuk harfle yazilmis Turkce ad
    normal yazimla eslesmez. I/ı/İ/i'yi tek harfe indirmek bunu cozerdi ama
    "Kıran" ile "Kiran"i AYNI kisi yapardi. Bolme hatasi (gecmis kopar)
    birlestirme hatasindan (baskasinin skoru sana yazilir) daha ucuz oldugu icin
    bilincli olarak boyle birakildi (bkz. tests/test_hedef_eslesme.py)."""
    return " ".join((s or "").split()).casefold()


def _ilike_deseni(s: str) -> str:
    """PostgREST `ilike` degeri: joker karakterleri ETKISIZLESTIR.

    PostgREST'te `*` joker olarak yorumlanir, Postgres LIKE'ta `%` ve `_` de
    oyle. Kacirilirsa "%" adini tarayan biri TUM kayitlarla eslesir. Yine de
    tek basina yeterli sayilmaz: donen satirlar Python'da `hedef_anahtari` ile
    BIREBIR dogrulanir (savunma katmani)."""
    duz = " ".join((s or "").split())
    return "".join("\\" + c if c in "*%_\\" else c for c in duz)


async def get_previous_audits(kind: str, target: str, limit: int = 2) -> list:
    """
    Ayni hedefin onceki tamamlanmis taramalari (en yeni once) — skor
    istikrari (stability.py) icin. kind: 'web' -> domain esleme,
    digerleri -> name esleme. Donen: [{"score", "breakdown"}, ...]

    Esleme BUYUK-KUCUK HARF ve BOSLUK duyarsizdir (bkz. hedef_anahtari).
    Alan adlari zaten tanim geregi harf duyarsizdir; kisi/marka adlarinda ise
    duyarlilik gecmisi bolen bir hataydi.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not target:
        return []
    col = "domain" if kind == "web" else "name"
    aranan = hedef_anahtari(target)
    if not aranan:
        return []
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits",
                headers=_headers(),
                params={
                    col: f"ilike.{_ilike_deseni(target)}",
                    "status": "eq.complete",
                    "score": "not.is.null",
                    "order": "created_at.desc",
                    # ilike genis eleyebilir (or. farkli ic bosluk); Python
                    # dogrulamasindan sonra `limit` kadari kalsin diye fazla cek.
                    "limit": str(max(limit * 5, 10)),
                    "select": f"score,result_json,{col}",
                },
                timeout=10,
            )
            if r.status_code == 200:
                out = []
                for row in r.json():
                    # Savunma katmani: ilike yanlislikla genis eslestiyse burada duser.
                    if hedef_anahtari(row.get(col)) != aranan:
                        continue
                    rj = row.get("result_json") or {}
                    out.append({
                        "score": row.get("score"),
                        "breakdown": rj.get("score_breakdown") or {},
                    })
                    if len(out) >= limit:
                        break
                return out
    except Exception as e:
        logger.warning(f"get_previous_audits error: {e}")
    return []


async def get_dataforseo_cost_monthly(lookback_days: int = 365) -> dict[str, float]:
    """DataForSEO (AI Overview) harcamasi: YYYY-MM -> USD.

    NEDEN AUDITS'TEN OKUNUR, AYRI TABLODAN DEGIL: maliyeti zaten saglayici
    yanitindan hesaplayip taramanin kendi sonucuna yaziyoruz
    (`result_json.sov.ai_overview.cost_usd`, bkz. ai_overview.py). Ikinci bir
    defter tutmak ayni sayiyi iki yerde tutmak olur — ve sema degisikligi
    gerektirirdi. Perplexity'de ayri tablo VAR cunku orada maliyet bir taramaya
    degil tek tek LLM cagrilarina ait; burada birim zaten taramadir.

    PostgREST satir limiti tuzagi (perplexity'de yasandi: 1964 satir kesilip
    harcama yariya dusmustu) burada sayfalama ile kapatilir; ayrica yalniz
    ai_overview OLCULMUS satirlar cekilir (bugun 393 taramanin ~5'i).
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {}
    # 🪤 isoformat() "+00:00" ile biter; URL'de ham "+" BOSLUK olarak cozulur ve
    # PostgREST 400 doner (olculdu). Gun hassasiyeti 365 gunluk pencere icin
    # fazlasiyla yeterli — tarih formati bu tuzagi tamamen ortadan kaldirir.
    baslangic = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    aylik: dict[str, float] = {}
    sayfa, ADIM = 0, 1000
    try:
        async with httpx.AsyncClient() as client:
            while True:
                r = await client.get(
                    f"{SUPABASE_URL}/rest/v1/audits"
                    f"?select=created_at,cost:result_json->sov->ai_overview->>cost_usd"
                    f"&result_json->sov->ai_overview->>cost_usd=not.is.null"
                    f"&created_at=gte.{baslangic}&order=created_at.asc",
                    headers={**_headers(),
                             "Range-Unit": "items",
                             "Range": f"{sayfa * ADIM}-{sayfa * ADIM + ADIM - 1}"},
                    timeout=15,
                )
                if r.status_code not in (200, 206):
                    logger.warning(f"get_dataforseo_cost_monthly HTTP {r.status_code}")
                    break
                satirlar = r.json()
                for row in satirlar:
                    try:
                        tutar = float(row.get("cost") or 0)
                    except (TypeError, ValueError):
                        continue
                    ay = (row.get("created_at") or "")[:7]
                    if ay:
                        aylik[ay] = aylik.get(ay, 0.0) + tutar
                if len(satirlar) < ADIM:
                    break
                sayfa += 1
    except Exception as e:
        logger.warning(f"get_dataforseo_cost_monthly error: {e}")
    return {k: round(v, 5) for k, v in aylik.items()}


# ---------- Ozel (private) tarama: sonuc SAKLANMAZ ----------
# Musteriye verilen soz: "sonuc hicbir yerde kaydedilmedi". SQS modunda bu soz
# TUTULMUYORDU: is baska process'te (worker) kostugu icin sonuc, polling'in
# okuyabilmesi adina audits satirina yaziliyordu ve orada KALIYORDU
# (2026-08-03'te olculdu; o gune kadar hic ozel tarama satin alinmamisti, yani
# kimse etkilenmedi). Cozum: sonuc teslim edilir edilmez satirdan silinir.
# Kullanici raporsuz kalmaz — e-posta HER taramada gidiyor (main.py:505),
# yani kalici kopya kendi posta kutusunda, bizde degil.

async def purge_private_result(job_id: str) -> bool:
    """Ozel taramanin sonucunu satirdan siler. Satir SILINMEZ: polling'in ve
    'bu is gercekten vardi' kontrolunun calismasi icin durum kalir, ICERIK gider."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{SUPABASE_URL}/rest/v1/audits?id=eq.{job_id}",
                headers=_headers(), json={"result_json": None, "score": None}, timeout=10,
            )
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"purge_private_result({job_id}) error: {e}")
        return False


async def sweep_private_results(max_age_hours: int = 6) -> int:
    """Teslimde silinemeyenleri supurur.

    NEDEN GEREKLI: silme, sonucun OKUNDUGU ana bagli. Kullanici sekmeyi kapatir
    ya da hic pollemezse sonuc satirda kalirdi — yani soz yalnizca 'polleyen
    kullanici' icin tutulmus olurdu. Bu supurge sozü HERKES icin tutar.
    Pencere kisa degil ki ayni oturumda sayfa yenilemesi calissin."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    sinir = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M:%S")
    silinen = 0
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/audits"
                f"?select=id&result_json->>private=eq.true&created_at=lt.{sinir}"
                f"&result_json=not.is.null&limit=200",
                headers=_headers(), timeout=15,
            )
            if r.status_code != 200:
                return 0
            for row in r.json():
                if await purge_private_result(row["id"]):
                    silinen += 1
    except Exception as e:
        logger.warning(f"sweep_private_results error: {e}")
    if silinen:
        logger.info(f"sweep_private_results: {silinen} ozel tarama sonucu silindi")
    return silinen
