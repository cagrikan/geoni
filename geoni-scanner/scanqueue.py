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
"""

import asyncio
import math
import os

SCAN_CONCURRENCY = int(os.environ.get("SCAN_CONCURRENCY", "2"))
AVG_SCAN_SECONDS = int(os.environ.get("AVG_SCAN_SECONDS", "150"))  # kaba ortalama

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
