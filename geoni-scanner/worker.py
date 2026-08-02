"""
GEONI Scan Worker — SQS tuketicisi.

API (main.py) taramalari SQS'e yazar; bu process ceker ve mevcut
run_audit_job pipeline'ini AYNEN calistirir (main.py import edilir, uvicorn
baslatilmaz). Durum guncellemeleri set_job_status uzerinden audits satirina
gider; API status endpoint'i oradan okur.

Kesinti/crash guvenligi: mesaj ancak is bittikten sonra silinir. Process
olurse visibility timeout dolunca mesaj geri gorunur, baska worker alir
(redrive 3 denemeden sonra DLQ'ya atar). Uygulama-ici hatalar (or. site
crawl edilemedi) 'failed' olarak KAYDEDILIR ve mesaj silinir — bunlari
tekrar denemek LLM parasi yakar, kazandirmaz.

Calistirma: python worker.py  (SCAN_QUEUE_URL zorunlu; WORKER_CONCURRENCY
varsayilan 2 — scanqueue.SCAN_CONCURRENCY semaforuyla ayni sayi olmali)
"""

import asyncio
import json
import logging
import os
import signal

import boto3

import main  # pipeline + jobs_store; uvicorn calismaz, sadece modul
# Scale-in korumasi ayri modulde: worker.py boto3 + SCAN_QUEUE_URL istedigi icin
# CI'nin minimal test ortaminda import EDILEMIYOR (bkz. task_protection.py).
import task_protection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")

# A4-5: Sentry (SENTRY_DSN yoksa no-op). Worker unhandled exception'lari da toplanir.
from observability import init_sentry
init_sentry("worker")

QUEUE_URL = os.environ["SCAN_QUEUE_URL"]
WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "2"))
VISIBILITY_SECONDS = int(os.environ.get("SCAN_VISIBILITY_SECONDS", "180"))
_region = QUEUE_URL.split(".")[1] if "sqs." in QUEUE_URL else None
sqs = boto3.client("sqs", region_name=_region)

_shutdown = asyncio.Event()

async def heartbeat(receipt: str, stop: asyncio.Event):
    """Uzun taramalarda mesajin baska worker'a gorunmesini engelle:
    visibility suresinin yarisinda bir uzat."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=VISIBILITY_SECONDS / 2)
        except asyncio.TimeoutError:
            try:
                await asyncio.to_thread(
                    sqs.change_message_visibility,
                    QueueUrl=QUEUE_URL, ReceiptHandle=receipt,
                    VisibilityTimeout=VISIBILITY_SECONDS,
                )
            except Exception as e:
                logger.warning(f"visibility uzatilamadi: {e}")


async def process_message(body: str, receipt: str):
    stop = asyncio.Event()
    hb = asyncio.create_task(heartbeat(receipt, stop))
    # Korumayi mesaji ISLEMEDEN once al: prewarm/bilinmeyen tur dallari erken
    # return ediyor ve finally'ye dusuyor, sayac orada dengeleniyor.
    await task_protection.acquire()
    delete_after = True
    try:
        payload = json.loads(body)
        kind = payload.get("kind")
        if kind == "prewarm":
            # Isitma no-op: worker'i ayakta tutmak icin gonderilmis; tarama yok.
            # delete_after=True kalir -> mesaj silinir, kuyruk temizlenir.
            logger.info("prewarm mesaji alindi (no-op, worker isindi)")
            return
        job_id = payload["job_id"]
        if kind != "web_audit":
            logger.error(f"bilinmeyen is turu: {kind} ({job_id}) — DLQ'ya birak")
            delete_after = False
            return
        request = main.AuditRequest(**payload["request"])
        # run_audit_job jobs_store girisini bekler (API'dekiyle ayni sekil)
        main.jobs_store[job_id] = {"job_id": job_id, "status": "queued",
                                   "domain": request.domain, "email": request.email,
                                   "created_at": "", "result": None, "error": None}
        logger.info(f"tarama basliyor: {job_id} {request.domain}")
        await main.run_audit_job(job_id, request, payload.get("token", ""))
        status = main.jobs_store.get(job_id, {}).get("status")
        logger.info(f"tarama bitti: {job_id} status={status}")
        # failed dahil siliyoruz: uygulama-ici hata deterministiktir, retry
        # sadece maliyet uretir. Silinmeyen tek durum process crash'i —
        # o zaman bu satira hic gelinmez, SQS redrive devreye girer.
    except Exception as e:
        # Parse/beklenmedik hata: mesaji birak, redrive->DLQ yakalasin.
        logger.error(f"mesaj islenemedi: {e}")
        delete_after = False
    finally:
        stop.set()
        await hb
        if "payload" in dir():
            main.jobs_store.pop(payload.get("job_id", ""), None)
            main.audit_events.pop(payload.get("job_id", ""), None)
        if delete_after:
            try:
                await asyncio.to_thread(sqs.delete_message, QueueUrl=QUEUE_URL, ReceiptHandle=receipt)
            except Exception as e:
                logger.warning(f"delete_message basarisiz: {e}")
        # Koruma EN SONDA birakilir: mesaj silindikten sonra. Once biraksaydik,
        # silme ile koruma kalkmasi arasindaki pencerede gorev oldurulup mesaj
        # tekrar teslim edilebilirdi — kapatmaya calistigimiz yarisin aynisi.
        await task_protection.release()


async def run():
    logger.info(f"worker basladi (concurrency={WORKER_CONCURRENCY}, queue={QUEUE_URL})")
    inflight: set[asyncio.Task] = set()
    while not _shutdown.is_set():
        inflight = {t for t in inflight if not t.done()}
        slots = WORKER_CONCURRENCY - len(inflight)
        if slots <= 0:
            await asyncio.sleep(1)
            continue
        try:
            resp = await asyncio.to_thread(
                sqs.receive_message,
                QueueUrl=QUEUE_URL, MaxNumberOfMessages=min(slots, 10),
                WaitTimeSeconds=20, VisibilityTimeout=VISIBILITY_SECONDS,
            )
        except Exception as e:
            logger.warning(f"receive_message hatasi: {e}")
            await asyncio.sleep(5)
            continue
        for m in resp.get("Messages", []):
            inflight.add(asyncio.create_task(process_message(m["Body"], m["ReceiptHandle"])))
    # Zarif kapanis: eldeki taramalar bitsin (Spot kesintisinde 120 sn taninir;
    # yetismezse mesaj silinmemis olur ve baska worker devralir — veri kaybi yok).
    if inflight:
        logger.info(f"kapanis: {len(inflight)} tarama bekleniyor")
        await asyncio.gather(*inflight, return_exceptions=True)


def _on_signal(*_):
    _shutdown.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    asyncio.run(run())
