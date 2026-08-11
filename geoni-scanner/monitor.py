"""
GEONI Scanner - Izleme v2 (haftalik otomatik yeniden tarama)

Kullanicinin izleme listesine (watchlist) ekledigi hedefler 15 gunde bir
otomatik olarak yeniden taranir:
  - type=web    -> tam site denetimi (crawl + indexing + recall + skor v3)
  - type=person/brand -> marka bilinirligi kontrolu (recall + SOV)

Kurallar:
  - Izleme taramalari ELLE TARAMAYLA AYNI BEDELI ODER (deduct=True).
    Gerekce kurucunun kendi cumlesi (2026-08-12): "bize maliyet hep ayni" —
    otomatik tarama da ~$0,32'ye mal oluyor, bedelinin farkli olmasi icin
    sebep yok. Bedel tipine gore: web/marka 20, sosyal 10 (scan_costs.py).
    ONCEDEN UCRETSIZDI ve bu yuzden 2026-08-08'de ozellik tamamen kapatildi
    ("11 hedef x 15 gunde bir x ~$0,31 ≈ $7/ay, karsiliginda gelir yok").
    Krediye baglanmasi o gerekceyi ortadan kaldirdigi icin ozellik geri aciliyor.
  - Ucretsiz deneme donemi YOK: ilk otomatik taramadan itibaren ucretlidir.
  - Bakiye yetmezse tarama ATLANIR, hedef listede KALIR ve kullaniciya
    "izleme duraklatildi" bildirimi gider; jeton yuklenince kendiliginden
    devam eder (bkz. _process_item).
  - Kayitlar audits tablosuna normal tarama gibi yazilir; dashboard'daki
    trend/sparkline ayni gecmisten beslenmeye devam eder.
  - Skor son otomatik skora gore ±5 puan degistiyse kullaniciya
    mail@geoni.ai'dan bildirim gider.
  - Donem basina az is (varsayilan 3 kayit/saat) — Tavily/LLM kotalarini
    korumak icin. Kuyruk uzunsa sonraki saatlerde siraya devam edilir.

main.py startup'ta monitor_loop() gorevini baslatir. Bu modul main'i
IMPORT ETMEZ (dairesel bagimlilik yok).
"""

import os
import asyncio
import logging
import uuid
from datetime import datetime

from crawler import crawl_domain
from indexing import check_indexing_status
from scoring import compute_ai_visibility_score
from brand_recall import check_brand_recall, infer_brand_identity
from topics import generate_topics_and_opportunities
from db import (
    list_due_watchlist_items, update_watchlist_after_scan, get_auth_email,
    save_audit, save_brand_check, get_credit_balance,
    _claim_daily_job, run_attachment_retention, run_audit_retention, run_low_balance_alert,
    sweep_private_results,
)
from mailer import send_monitor_email, send_ticket_email
from scan_costs import WEB_SCAN_COST, BRAND_SCAN_COST, SOCIAL_SCAN_COST
from pushnotify import send_score_change_push
from stability import build_stability
from audit_payload import build_audit_result_payload
from scanqueue import acquire_scan_slot, release_scan_slot

logger = logging.getLogger(__name__)

MONITOR_CYCLE_SECONDS = 3600   # saatte bir kontrol
MONITOR_INTERVAL_DAYS = 15     # hedef basina en cok 15 gunde bir otomatik tarama
MONITOR_BATCH_SIZE = 3         # donem basina en cok 3 hedef (kota korumasi)
MONITOR_PAGE_LIMIT = 30        # izleme taramasi hafif crawl
SCORE_CHANGE_THRESHOLD = 5     # bildirim esigi (puan) — web/kisi/marka

# SOSYALDE ESIK FARKLI (kurucu karari 2026-08-04). Sosyal taramada crawl/indeks/
# skorlama yok; genel skor dogrudan brand_recall'dan gelir ve orada SOV'un
# agirligi 0.55 (WEIGHTS_SOCIAL) — site taramasindaki 0.075'in yedi kati.
# Olculdu: SOV birincil hucresi 8, yani TEK hucrenin oynamasi SOV'u 12,5 puan,
# genel skoru 12,5 x 0,55 = ~6,9 puan degistiriyor. Esik 5 iken bu, hicbir sey
# degismemisken "skorun dustu" bildirimi demekti — olcum gurultusu.
# 10 esigi ikisini ayirir:  1 hucre = 6,9 (susar) · 2 hucre = 13,8 (duyulur).
#
# ⚠️ Bu sayi SOV agirligina ve hucre sayisina bagli. Ikisinden biri degisirse
# yeniden hesaplanmali: esik, tek hucrenin genel skordaki karsiligindan BUYUK,
# iki hucreninkinden KUCUK olmali.
SOCIAL_SCORE_CHANGE_THRESHOLD = 10
# FREE_MONITOR_DAYS KALDIRILDI (2026-08-12, kurucu karari): "ilk 30 gun
# ucretsiz" donemi yok. Izleme ilk otomatik taramadan itibaren ucretli.
# Eski davranis: ilk ay bedelsiz + sonrasinda yalnizca "bakiye > 0" sarti
# aranıyordu ama tarama yine kontor DUSMUYORDU — yani hicbir zaman
# ucretlendirilmiyordu. Bedel artik gercekten dusuluyor.

# OTOMATIK TARAMA — env ile aciliyor. TARIHCE onemli, silme:
#
# 2026-08-08'de KAPATILDI (kurucu: "artik otomatik tarama istemiyorum").
# Sebep para: 11 hedef x 15 gunde bir x ~$0,31 ≈ $7/ay, karsiliginda SIFIR
# gelir — cunku izleme taramalari o zaman UCRETSIZDI (deduct=False).
#
# 2026-08-12'de bedel eklendi (kurucu: "bize maliyet hep ayni") — izleme
# taramasi artik elle taramayla ayni kontoru duser. Kapatma gerekcesi boylece
# ortadan kalkti; ozellik acilabilir.
#
# 🪤 VARSAYILAN yine KAPALI: env yoksa acilirsa, yeni bir ortamda (yeni task
# def, yerel kosu, gecici servis) tarama SESSIZCE baslar ve kullanicilarin
# kontoru habersiz duser. Acmak BILINCLI bir hareket olmali: MONITOR_AUTO_SCAN=1.
#
# 🔴 IKI BAYRAK BIRLIKTE DEGISIR: burasi acilirken arayuz tarafindaki
# geoni-frontend/src/lib/otomatikIzleme.js -> OTOMATIK_IZLEME_ACIK da true
# yapilmali. Biri unutulursa arayuz yalan soyler (bir yon: is yapiliyor ama
# "Kayitli" yaziyor; oteki yon: "izleniyor" yaziyor ama tarama yok).
#
# Dongunun DIGER isleri (dusuk-bakiye uyarisi, retention temizligi) calismaya
# DEVAM EDER — onlar tarama degil, bakim.
OTOMATIK_TARAMA_ACIK = os.environ.get("MONITOR_AUTO_SCAN", "0").strip() == "1"


async def _scan_web_item(item: dict) -> int | None:
    """Web hedefi icin tam denetim; audits'e kaydeder, yeni skoru dondurur."""
    # `target` duz METIN de gelebiliyor (bkz. _hedef_sozlugu). Web yolunda
    # bugun cokmedi cunku o satirlarin target'i sozlukttu, ama ayni bomba
    # buradaydi: metin gelen bir web hedefi eklenirse aynen coker.
    domain = _hedef_sozlugu(item).get("domain") or item.get("label")
    if not domain:
        return None

    crawl_result = await crawl_domain(domain, MONITOR_PAGE_LIMIT)
    # Fable 2026-07-23: domain= GECILMEZSE crawl 0 sayfa dondugunde (bot-korumali/JS-agir)
    # indexing.py erken-donus dali robots/llms/brave/google kontrollerini ATLAR ve ai_access
    # sahte-yuksek/bot_protection sahte-False kalir (main.py bunu domain gecerek zaten cozmustu,
    # monitor/self-scan yolu atlanmisti). Domain gecirildi -> izleme + geoni.ai self-scan duzelir.
    indexing_status = await check_indexing_status(crawl_result["pages"], domain=domain)
    item_lang = item.get("lang") or "tr"
    identity = await infer_brand_identity(domain, crawl_result.get("pages", []), item_lang)
    brand_recall_result = await check_brand_recall(
        identity["name"], topic=identity["topic"],
        custom_queries=item.get("custom_queries"),
        website=domain,
        lang=item_lang,
        # Web izleme taramasi da konulari topics.py'den aliyor (asagida) ->
        # brand_recall'in topic-gen cagrisi bosa gider; yalniz rakip yedegi
        # gerekince yapilsin. main.py'deki web yolu ile AYNI davranis.
        need_topics=False,
    )
    score_result = await compute_ai_visibility_score(crawl_result, indexing_status, brand_recall_result)
    topics = await generate_topics_and_opportunities(domain, crawl_result["pages"], item_lang)

    # Payload artik ELLE KURULMAZ: main.run_audit_job ile AYNI fonksiyondan gecer
    # (audit_payload.py). Onceden iki kopya vardi ve monitor kopyasi geride
    # kalmisti — bkz. audit_payload.py'deki `platforms.google` tuzagi.
    result_payload = await build_audit_result_payload(
        domain=domain, lang=item_lang, crawl_result=crawl_result,
        indexing_status=indexing_status, brand_recall_result=brand_recall_result,
        score_result=score_result, topics=topics, identity=identity,
        auto_monitor=True)

    job_id = str(uuid.uuid4())
    # deduct=True (2026-08-12): izleme taramasi da kontor duser. Dusum
    # save_audit'in ICINDE ve YALNIZ kayit basarili olursa yapilir; dusum
    # basarisiz olursa credits_spent geri sifirlanir (db.py _maliyeti_sifirla).
    # Yani basarisiz tarama kullanicinin parasini yakmaz.
    await save_audit(job_id, {"domain": domain, "email": ""}, result_payload,
                     item.get("user_id"), deduct=True)
    return score_result["overall_score"]


def _hedef_sozlugu(item: dict) -> dict:
    """watchlist.target'i HER ZAMAN sozluk olarak dondurur.

    🪤 CANLI VERIDE IKI BICIM VAR (2026-08-12'de olculdu):
      - cogu satir: {"name": "...", "topic": "..."} / {"domain": "..."}
      - bazi satirlar: duz METIN, ornegin "Filiz Alkan"
    Kod yalnizca sozluk varsayiyordu; metin gelen satirlarda
    `target.get(...)` -> "'str' object has no attribute 'get'" ile COKUYORDU.
    Izleme 2026-08-08'den beri kapali oldugu icin bu yol hic kosmamis, hata
    da gorunmemisti; ozellik acilir acilmaz ODEYEN MUSTERININ iki hedefi
    ust uste basarisiz oldu (skor None, kontor dusmedi).

    Metin gelirse ad olarak kabul edilir — etiketle ayni anlama gelir.
    Beklenmedik tip (liste/sayi) gelirse bos sozluk; cagiran `label`e duser.
    """
    t = item.get("target")
    if isinstance(t, dict):
        return t
    if isinstance(t, str) and t.strip():
        return {"name": t.strip()}
    return {}


async def _scan_brand_item(item: dict) -> int | None:
    """Kisi/marka hedefi icin bilinirlik kontrolu; audits'e kaydeder."""
    target = _hedef_sozlugu(item)
    name = target.get("name") or item.get("label")
    if not name:
        return None

    result = await check_brand_recall(
        name=name,
        topic=target.get("topic") or "",
        entity_type=item.get("type") or "person",
        custom_queries=item.get("custom_queries"),
        website=target.get("website") or "",
    )
    if not result.get("checked"):
        return None

    result_payload = {
        "name": name,
        "topic": target.get("topic") or "",
        "recognized": result.get("recognized", False),
        "recognition_count": result.get("recognition_count", 0),
        "score": result.get("score", 0),
        "score_legacy": result.get("score_legacy"),
        # shadow_compare sinyali icin (bkz. result_contract.build_brand_payload notu):
        # izleme yeniden-taramasi da golge skoru tasisin, yoksa bu yoldan gelen
        # taramalar shadow_deltas'a katilmaz.
        "score_shadow": result.get("score_shadow"),
        "scoring_version": result.get("scoring_version"),
        "score_breakdown": result.get("score_breakdown", {}),
        "model_results": result.get("model_results", {}),
        "google_result_count": result.get("google_result_count", 0),
        "performing_topics": result.get("performing_topics", []),
        "opportunity_topics": result.get("opportunity_topics", []),
        "checked": True,
        "raw_list": result.get("raw_list"),
        "sov": result.get("sov"),
        "stability": await build_stability(item.get("type") or "person", name,
                                           result.get("score"),
                                           result.get("score_breakdown", {})),
        "auto_monitor": True,
        "created_at": datetime.now().isoformat(),
    }

    job_id = str(uuid.uuid4())
    await save_brand_check(
        job_id,
        {"type": item.get("type") or "person", "name": name, "topic": target.get("topic") or ""},
        result_payload,
        item.get("user_id"),
        # deduct=True (2026-08-12): web yolundaki ile ayni gerekce — izleme
        # taramasi elle taramayla ayni bedeli oder.
        deduct=True,
    )
    return result.get("score")


def _izleme_bedeli(item: dict) -> int:
    """Bu hedefin izleme taramasinin kontor bedeli.

    Elle taramayla AYNI: bedel scan_costs.py'den gelir, burada elle yazilmaz.
    (Sayiyi ikinci bir yere yazmak, scan_costs.py'nin bastaki notunda anlatilan
    'alti ayri yerde elle yazili' hatasini geri getirirdi.)
    """
    tip = (item.get("type") or "").lower()
    if tip == "web":
        return WEB_SCAN_COST
    if tip == "social":
        return SOCIAL_SCAN_COST
    return BRAND_SCAN_COST      # person / brand


async def _duraklatma_bildir(user_id: str, label: str, bedel: int, bakiye: int):
    """'Izleme duraklatildi' bildirimi — kullanici basina GUNDE EN COK BIR kez.

    Debounce sart: dongü saatlik ve bakiyesiz hedef her turda yeniden siraya
    gelir; debounce olmasa kullaniciya saatte bir e-posta giderdi.
    _claim_daily_job zaten cok-instance guvenli tek-sefer kilidi sagliyor.
    """
    if not await _claim_daily_job(f"monitor_duraklama_{user_id}"):
        return
    email = await get_auth_email(user_id)
    if not email:
        return
    await send_ticket_email(
        email,
        subject="GEONI: izleme duraklatıldı — jeton bitti",
        heading="İzleme duraklatıldı",
        lines=[
            f"<b>{label}</b> hedefinin otomatik taraması yapılamadı.",
            f"Bu tarama {bedel} jeton gerektiriyor, bakiyeniz {bakiye} jeton.",
            "Hedef listenizden <b>çıkarılmadı</b> — jeton yüklediğinizde "
            "izleme kaldığı yerden kendiliğinden devam eder.",
        ],
        cta_label="Jeton yükle",
        cta_url="https://app.geoni.ai",
    )


async def _process_item(item: dict):
    label = item.get("label", "?")
    user_id = item.get("user_id")

    # ── UCRETLENDIRME KAPISI ────────────────────────────────────────────────
    # Izleme taramasi elle taramayla AYNI bedeli oder (kurucu karari 2026-08-12:
    # "bize maliyet hep ayni"). Ucretsiz deneme donemi YOK.
    #
    # Bakiye yetmezse: tarama ATLANIR, hedef listede KALIR, kullaniciya gunde
    # en cok bir kez bildirim gider.
    #
    # 🔴 last_auto_scan_at'e DOKUNULMAZ. Onceki kod atlarken de zamani
    # guncelliyordu; bu, jeton yukleyen kullaniciyi 15 gun daha bekletirdi
    # ("odedim ama hala taranmiyor"). Zamani guncellemeyince hedef sirada
    # kalir ve bakiye gelir gelmez bir sonraki turda taranir. Bedeli: bakiyesiz
    # hedef her turda bir bakiye sorgusu — ucuz ve dogru taraf.
    bedel = _izleme_bedeli(item)
    bakiye = await get_credit_balance(user_id) if user_id else 0
    if bakiye < bedel:
        logger.info(f"monitor: '{label}' atlandi — bakiye {bakiye} < bedel {bedel}")
        if user_id:
            await _duraklatma_bildir(user_id, label, bedel, bakiye)
        return

    try:
        # Kullanici taramalariyla ayni kuyruk: izleme asla kaynaklari bogamaz
        await acquire_scan_slot()
        try:
            if item.get("type") == "web":
                new_score = await _scan_web_item(item)
            else:
                new_score = await _scan_brand_item(item)
        finally:
            release_scan_slot()
    except Exception as e:
        logger.warning(f"monitor: '{label}' taramasi basarisiz: {e}")
        # Basarisiz denemede de zamani guncelle — bozuk hedef her saat
        # kuyrugun basini tikamasin, sirasi haftaya yeniden gelsin.
        await update_watchlist_after_scan(item.get("id"), None)
        return

    old_score = item.get("last_score")
    await update_watchlist_after_scan(item.get("id"), new_score)
    logger.info(f"monitor: '{label}' otomatik tarandi, skor={new_score} (onceki={old_score})")

    esik = (SOCIAL_SCORE_CHANGE_THRESHOLD if (item.get("type") == "social")
            else SCORE_CHANGE_THRESHOLD)
    if (
        new_score is not None and old_score is not None
        and abs(new_score - old_score) >= esik
    ):
        email = await get_auth_email(item.get("user_id"))
        if email:
            await send_monitor_email(email, label, int(old_score), int(new_score))
        # Mobil push (kayitli cihazlara) - e-postaya ek olarak.
        await send_score_change_push(item.get("user_id"), label, int(old_score), int(new_score))


async def monitor_loop():
    """Saatlik dongu: sirasi gelen izleme kayitlarini kucuk partiler halinde tarar."""
    await asyncio.sleep(120)  # acilista servis otursun
    logger.info("monitor: izleme dongusu basladi")
    while True:
        try:
            if not OTOMATIK_TARAMA_ACIK:
                # Kurucu karari: kendiliginden tarama yok. Kuyrugu SORGULAMIYORUZ
                # bile — bos donguyu her saat DB'ye vurdurmanin anlami yok.
                pass
            else:
                items = await list_due_watchlist_items(limit=MONITOR_BATCH_SIZE, interval_days=MONITOR_INTERVAL_DAYS)
                if items:
                    logger.info(f"monitor: {len(items)} hedef sirada")
                for item in items:
                    await _process_item(item)
        except Exception as e:
            logger.warning(f"monitor: dongu hatasi: {e}")
        # Prepaid saglayici dusuk-bakiye uyarisi: her turda (saatlik) kontrol,
        # debounce db tarafinda (ayni saglayici gunde bir kez uyarilir).
        try:
            alerted = await run_low_balance_alert()
            if alerted:
                logger.info(f"monitor: dusuk-bakiye uyarisi -> {[a['provider'] for a in alerted]}")
        except Exception as e:
            logger.warning(f"monitor: dusuk-bakiye kontrol hatasi: {e}")
        # Gunluk retention temizligi: cok-instance guvenli kilitle TEK sefer.
        try:
            if await _claim_daily_job("retention"):
                att = await run_attachment_retention()
                slim = await run_audit_retention(None)
                # Ozel taramanin sonucu teslim aninda silinir; ama silme OKUMA
                # anina bagli — kullanici sekmeyi kapatir ya da hic pollemezse
                # sonuc satirda kalirdi, yani soz yalniz "polleyen kullanici"
                # icin tutulmus olurdu. Bu supurge sozu HERKES icin tutar.
                ozel = await sweep_private_results()
                logger.info(f"monitor: retention -> ekler {att}, rapor sadelestirme {slim}, "
                            f"ozel tarama sonucu {ozel}")
        except Exception as e:
            logger.warning(f"monitor: retention hatasi: {e}")
        await asyncio.sleep(MONITOR_CYCLE_SECONDS)
