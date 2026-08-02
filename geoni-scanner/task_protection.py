"""
ECS Task Scale-In Protection — calisan taramanin oldurulmesini engeller.

NEDEN: bosta-alarmi CALISAN worker'i tarama ortasinda oldurdu (olculdu
2026-08-02: 20:09'da desired->0, gorev SIGKILL 137, mesaj kuyruga dondu, ayni
is BASTAN tarandi -> 3 gunde 12 isin 2'si iki kez kostu, her tekrar ~$0.31 ve
skor bile degisti 61->63). Alarm metrigi dogru (vis+inf, uçustaki dahil); suc
CloudWatch'in ~3 dk gecikmesinde: kuyruk dolmadan ONCEKI bosluk, is basladiktan
SONRA alarmi atesliyor. Alarmi genisletmek yarisi daraltir, KAPATMAZ.
Korumali gorev scale-in ile OLDURULEMEZ.

NEDEN AYRI MODUL: worker.py ice aktarma aninda boto3 + SCAN_QUEUE_URL istiyor;
CI deploy kapisi minimal ortam kuruyor (pytest httpx cbor2 cryptography) ve
boto3 YOK. Koruma mantiginin SQS ile ilgisi olmadigindan burada durur: test
edilebilir, CI'i agirlastirmaz. (Yasandi: worker.py'yi import eden test
ModuleNotFoundError: boto3 ile TUM suite'i dusurdu ve deploy kapisini kirdi.)
"""

import asyncio
import logging
import os

logger = logging.getLogger("task_protection")

# Sayac SART: koruma GOREVE ait, mesaja degil. WORKER_CONCURRENCY=2 iken iki is
# paralel kosar; biri bitince korumayi kaldirirsak digeri savunmasiz kalir.
_lock = asyncio.Lock()
_count = 0

# TTL emniyet supabi: `finally` hic kosmazsa (process crash) koruma sonsuza
# kadar kalmasin — suresi dolunca kendiliginden duser. En kotu durum "worker
# biraz fazla yasar", ki bu zaten bugunku davranis.
PROTECT_MINUTES = int(os.environ.get("SCAN_PROTECT_MINUTES", "60"))


async def set_protection(enabled: bool) -> None:
    """ECS agent'a koruma durumunu bildirir. Agent URI yoksa (yerel calistirma,
    eski agent) sessizce no-op — worker yerelde de kosabilmeli."""
    uri = os.environ.get("ECS_AGENT_URI")
    if not uri:
        return
    body: dict = {"ProtectionEnabled": enabled}
    if enabled:
        body["ExpiresInMinutes"] = PROTECT_MINUTES
    try:
        import httpx
        async with httpx.AsyncClient() as c:
            r = await c.put(f"{uri}/task-protection/v1/state", json=body, timeout=10)
        if r.status_code != 200:
            logger.warning(f"task protection {enabled} basarisiz: {r.status_code} {r.text[:200]}")
        else:
            logger.info(f"task protection -> {enabled}")
    except Exception as e:
        # Koruma kurulamazsa tarama YINE DE kosar: eski (korumasiz) davranisa
        # duseriz, yeni bir kirilma yuzeyi acmayiz.
        logger.warning(f"task protection {enabled} hata: {e}")


async def acquire() -> None:
    global _count
    async with _lock:
        _count += 1
        if _count == 1:
            await set_protection(True)


async def release() -> None:
    global _count
    async with _lock:
        _count = max(0, _count - 1)   # fazladan release sayaci bozmasin
        if _count == 0:
            await set_protection(False)
