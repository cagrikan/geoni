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
    bir taramaya bakakalmasin."""
    body = json.dumps(payload, ensure_ascii=False)
    await asyncio.to_thread(
        _sqs().send_message, QueueUrl=SCAN_QUEUE_URL, MessageBody=body
    )


_last_prewarm = {"t": 0.0}


async def enqueue_prewarm(cooldown: float = 25.0) -> bool:
    """Worker'i ONCEDEN isit: kuyruga bir no-op mesaji atarak SQS derinligini
    1 yapar; autoscaling worker'i 0->1 baslatir. Boylece kullanici formu
    doldururken worker boot olur ve gercek tarama geldiginde soguk baslangic
    beklemez. Worker bu mesaji tanir ve aninda siler (tarama/LLM yok).

    GLOBAL cooldown: kac kullanici tetiklerse tetiklesin en fazla `cooldown`
    saniyede bir mesaj gider — tek isinma tum kullanicilar icin yeter, boylece
    queue-depth metrigi sismez (asiri worker acilmaz)."""
    import time
    now = time.time()
    if not SCAN_QUEUE_URL or (now - _last_prewarm["t"]) < cooldown:
        return False
    _last_prewarm["t"] = now
    await asyncio.to_thread(
        _sqs().send_message, QueueUrl=SCAN_QUEUE_URL, MessageBody=json.dumps({"kind": "prewarm"})
    )
    return True

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
