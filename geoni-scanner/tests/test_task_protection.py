"""ECS Task Scale-In Protection: sayac + yasam dongusu (2026-08-02).

NEDEN VAR: bosta-alarmi CALISAN worker'i tarama ortasinda oldurdu (olculdu:
20:09 desired->0, SIGKILL 137, mesaj kuyruga dondu, ayni is bastan tarandi).
Koruma GOREVE ait, mesaja degil — WORKER_CONCURRENCY=2 iken iki is paralel
kosar ve biri bitince korumayi kaldirmak digerini savunmasiz birakir. Bu
dosyanin asil isi o sayac hatasini bir daha yapmadigimizi kanitlamak.
"""
import asyncio
import os

# worker.py ICE AKTARMA aninda SCAN_QUEUE_URL ister (worker.py:36 os.environ[...]).
# CI bu env'i tanimlamaz -> import KeyError atar ve TUM suite toplanamaz, deploy
# kapisi kirilir. Yakalandi 2026-08-02: tek dosya kosarken gorunmuyordu.
os.environ.setdefault("SCAN_QUEUE_URL",
                      "https://sqs.eu-central-1.amazonaws.com/000000000000/test-queue")

import worker  # noqa: E402


def _izle(monkeypatch):
    """_set_protection cagrilarini kaydeder (ag'a cikmadan)."""
    cagrilar: list[bool] = []

    async def sahte(enabled: bool):
        cagrilar.append(enabled)

    monkeypatch.setattr(worker, "_set_protection", sahte)
    monkeypatch.setattr(worker, "_protect_count", 0)
    return cagrilar


def test_tek_is_ac_kapa(monkeypatch):
    c = _izle(monkeypatch)

    async def akis():
        await worker._acquire_protection()
        await worker._release_protection()
    asyncio.run(akis())
    assert c == [True, False]


def test_paralel_iki_is_arada_korumayi_dusurmez(monkeypatch):
    """
    Regresyon kalkani: ikinci is BITMEDEN birincinin bitmesi korumayi
    kaldirmamali. Kaldirsaydi tam da duzeltmeye calistigimiz olum yeniden
    mumkun olurdu.
    """
    c = _izle(monkeypatch)

    async def akis():
        await worker._acquire_protection()   # is A
        await worker._acquire_protection()   # is B
        await worker._release_protection()   # A bitti — koruma KALMALI
        ara = list(c)
        await worker._release_protection()   # B bitti — simdi kalkar
        return ara
    ara = asyncio.run(akis())
    assert ara == [True], f"A bitince koruma dusmemeliydi: {ara}"
    assert c == [True, False]
    assert worker._protect_count == 0


def test_sayac_negatife_dusmez(monkeypatch):
    """Fazladan release (beklenmedik kod yolu) sayaci bozup sonraki isi
    korumasiz birakmamali."""
    c = _izle(monkeypatch)

    async def akis():
        await worker._release_protection()   # hic acilmadan birak
        await worker._acquire_protection()   # sonraki is
    asyncio.run(akis())
    assert worker._protect_count == 1
    assert c[-1] is True, "fazladan release sonrasi yeni is korumasiz kaldi"


def test_agent_uri_yoksa_sessiz_no_op(monkeypatch):
    """Yerel calistirmada ECS_AGENT_URI yok — worker yine de kosmali."""
    monkeypatch.delenv("ECS_AGENT_URI", raising=False)
    asyncio.run(worker._set_protection(True))   # patlamamali


def test_agent_hatasi_taramayi_dusurmez(monkeypatch):
    """
    Koruma kurulamazsa eski (korumasiz) davranisa duseriz; YENI bir kirilma
    yuzeyi acmayiz. Istisna disari SIZMAMALI.
    """
    monkeypatch.setenv("ECS_AGENT_URI", "http://169.254.170.2/v1/xyz")

    class PatlayanClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def put(self, *a, **k): raise RuntimeError("ag yok")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: PatlayanClient())
    asyncio.run(worker._set_protection(True))   # patlamamali


def test_acilirken_ttl_gonderilir_kapanirken_gonderilmez(monkeypatch):
    """TTL emniyet supabi: finally hic kosmazsa koruma sonsuza kadar kalmasin."""
    monkeypatch.setenv("ECS_AGENT_URI", "http://169.254.170.2/v1/xyz")
    govdeler = []

    class Resp:
        status_code = 200
        text = ""

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def put(self, url, json=None, timeout=None):
            govdeler.append(json)
            return Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: Client())
    asyncio.run(worker._set_protection(True))
    asyncio.run(worker._set_protection(False))
    assert govdeler[0]["ProtectionEnabled"] is True
    assert govdeler[0]["ExpiresInMinutes"] > 0
    assert govdeler[1] == {"ProtectionEnabled": False}
