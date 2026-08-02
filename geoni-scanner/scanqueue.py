"""
GEONI Scanner - Tarama Kuyrugu (eszamanlilik siniri + bekleme tahmini)

Gercek trafik oncesi koruma: her tarama Playwright crawl + onlarca LLM
cagrisi demek. Sinirsiz eszamanlilik tek ECS gorevini bogar ve API
kotalarini yakar. Global semafor ile ayni anda en cok SCAN_CONCURRENCY
tarama calisir; digerleri sirada bekler ve kullaniciya "siradasiniz,
tahmini bekleme ~X dk" mesaji gosterilir. Kullanici taramalari ve izleme
(monitor) taramalari AYNI kuyrugu paylasir — izleme hicbir zaman
kullaniciyla yarisip kaynak tuketemez.

Kalici/dagitik kuyruk (SQS vb.) gerektiginde tek degisim noktasi burasi.

SQS MODU (SCAN_QUEUE_URL doluysa): API taramayi kendisi calistirmaz; isi SQS'e
yazar, ayri worker servisi (worker.py) ceker. Semafor worker icinde ayni sekilde
calisir (worker basina SCAN_CONCURRENCY). Env bos birakilirsa eski in-process
davranis aynen korunur — guvenli geri donus yolu budur.
"""

import asyncio
import json
import math
import os

SCAN_CONCURRENCY = int(os.environ.get("SCAN_CONCURRENCY", "2"))
AVG_SCAN_SECONDS = int(os.environ.get("AVG_SCAN_SECONDS", "150"))  # kaba ortalama
SCAN_QUEUE_URL = os.environ.get("SCAN_QUEUE_URL", "")

_sqs_client = None


def sqs_enabled() -> bool:
    return bool(SCAN_QUEUE_URL)


def _sqs():
    global _sqs_client
    if _sqs_client is None:
        import boto3
        # Bolge kuyruk URL'sinden cikarilir (https://sqs.eu-central-1.amazonaws.com/...)
        region = SCAN_QUEUE_URL.split(".")[1] if "sqs." in SCAN_QUEUE_URL else None
        _sqs_client = boto3.client("sqs", region_name=region)
    return _sqs_client


async def enqueue_scan(payload: dict) -> None:
    """Is tanimini SQS'e yazar. boto3 senkron oldugu icin thread'e atilir;
    hata yutulmaz — cagiran taraf 500 dondursun ki kullanici kaybolmus
    bir taramaya bakakalmasin.

    Yaziyi attiktan SONRA worker'in ayakta oldugundan emin olunur: prewarm
    tetiklenmemis bir tarama (dogrudan API cagrisi, derin baglanti) sifir
    gorevle karsilasirsa CloudWatch alarmini beklemek zorunda kalirdi."""
    body = json.dumps(payload, ensure_ascii=False)
    await asyncio.to_thread(
        _sqs().send_message, QueueUrl=SCAN_QUEUE_URL, MessageBody=body
    )
    await ensure_worker_running()


# ── Worker'i dogrudan ayaga kaldirma ─────────────────────────────────────────
# NEDEN alarma guvenilmiyor: olculdu (2026-08-02, iki gercek tarama) — mesaj
# kuyruga dustukten sonra `geoni-scan-queue-has-work` alarminin ALARM'a
# gecmesi 164 sn ve 207 sn surdu. Sebep SQS'in CloudWatch'a metrik yollama
# gecikmesi (~3 dk). Tarama 150 sn surdugu icin ALARM ates ederken is coktan
# bitmisti. Yani otomatik olcekleme sifirdan kalkis icin FIILEN ise yaramiyor.
# Cozum: olcegi olayin kendisinden tetikle — UpdateService ile desiredCount'u
# 1 yap. Gorev 44 sn'de RUNNING, +2 sn python = ~46 sn'de hazir.
# Asagi inisi yine alarm yapiyor (`geoni-scan-queue-idle`, 10 dk bos -> 0);
# orada gecikmenin zarari yok, sadece birkac dakika fazla calisir.
ECS_CLUSTER = os.environ.get("ECS_CLUSTER", "")
ECS_WORKER_SERVICE = os.environ.get("ECS_WORKER_SERVICE", "")
_ecs_client = None
_last_scaleup = {"t": 0.0}


def _ecs():
    global _ecs_client
    if _ecs_client is None:
        import boto3
        region = SCAN_QUEUE_URL.split(".")[1] if "sqs." in SCAN_QUEUE_URL else None
        _ecs_client = boto3.client("ecs", region_name=region)
    return _ecs_client


def _scale_worker_to_one() -> bool:
    """Senkron govde (thread'e atilir). desiredCount zaten >=1 ise API'yi hic
    cagirmaz — bos yere UpdateService yagdirmak hem kotayi hem servisin olay
    gecmisini kirletir."""
    svc = _ecs().describe_services(cluster=ECS_CLUSTER, services=[ECS_WORKER_SERVICE])
    services = svc.get("services") or []
    if not services:
        raise RuntimeError(f"ECS servisi bulunamadi: {ECS_WORKER_SERVICE}")
    if services[0].get("desiredCount", 0) >= 1:
        return False
    _ecs().update_service(cluster=ECS_CLUSTER, service=ECS_WORKER_SERVICE, desiredCount=1)
    return True


async def ensure_worker_running(cooldown: float = 20.0) -> bool:
    """Worker'i simdi ayaga kaldir. Yapilandirilmamissa (env bos) sessizce
    devre disi — eski davranis korunur.

    Hata YUTULUR: olcegi buyutememek taramayi bozmaz, yalnizca yavaslatir
    (kuyruk mesaji duruyor, alarm er ya da gec kaldirir). Bu yuzden kullanici
    isteginin basarisiz olmasina sebep olmasin."""
    import time
    now = time.time()
    if not (ECS_CLUSTER and ECS_WORKER_SERVICE) or (now - _last_scaleup["t"]) < cooldown:
        return False
    _last_scaleup["t"] = now
    try:
        return await asyncio.to_thread(_scale_worker_to_one)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"worker olcegi buyutulemedi: {e}")
        return False


_last_prewarm = {"t": 0.0}


async def enqueue_prewarm(cooldown: float = 25.0) -> bool:
    """Tarama niyeti sinyali: worker'i ONCEDEN ayaga kaldir ki kullanici formu
    doldururken (~10-30 sn) gorev boot olsun ve gercek tarama geldiginde soguk
    baslangic beklemesin.

    ESKI YONTEM (kuyruga no-op mesaj atip autoscaling'in gormesini beklemek)
    KALDIRILDI: alarm 164-207 sn sonra atesliyor, tarama bundan once bitiyor.
    Artik ECS'e dogrudan soyluyoruz.

    GLOBAL cooldown: kac kullanici tetiklerse tetiklesin en fazla `cooldown`
    saniyede bir ECS'e gidilir."""
    import time
    now = time.time()
    if (now - _last_prewarm["t"]) < cooldown:
        return False
    _last_prewarm["t"] = now
    return await ensure_worker_running(cooldown=0.0)

scan_semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
_counters = {"waiting": 0}


def estimate_wait_seconds() -> int:
    """
    Siraya girmek uzere olan bir is icin kaba bekleme tahmini.
    0 donerse slot bos — beklemeden baslar. Tahmin; taahhut degil.
    """
    ahead = _counters["waiting"]
    if ahead == 0 and not scan_semaphore.locked():
        return 0
    batches = math.ceil((ahead + 1) / SCAN_CONCURRENCY)
    return batches * AVG_SCAN_SECONDS


async def acquire_scan_slot():
    """Slot bekler; bekleyen sayacini dogru tutar."""
    _counters["waiting"] += 1
    try:
        await scan_semaphore.acquire()
    finally:
        _counters["waiting"] -= 1


def release_scan_slot():
    scan_semaphore.release()
