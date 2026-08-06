"""Tarama kuyruğu: bekleme tahmini, slot sayacı ve ECS soğuma penceresi.

NEDEN VAR (2026-08-07): `scanqueue.py` 167 satır ve **hiç testi yoktu**, oysa
HER tarama oradan geçiyor ve iki şeyi birden yönetiyor:
  * kullanıcının gördüğü "sıradasınız, ~X dk" tahmini,
  * ECS worker'ını ayağa kaldırma çağrıları — yani PARA.

Özellikle soğuma (cooldown) penceresi kritik: bozulursa her tarama isteği
`ecs:UpdateService` çağırır. Ölçülmüş tasarım kararı (modül başlığında yazılı)
kaç kullanıcı tetiklerse tetiklesin en fazla `cooldown` saniyede bir ECS'e
gitmek; testler bunu kilitliyor.

🪤 Modül seviyesinde durum var (`_last_scaleup`, `_last_prewarm`, semafor,
sayaç). Her test kendi durumunu SIFIRLAR — yoksa testler birbirini etkiler.
"""
import asyncio

import pytest

import scanqueue


@pytest.fixture(autouse=True)
def _temiz_durum():
    """Her testten önce modül durumunu bilinen bir noktaya çek."""
    scanqueue._counters["waiting"] = 0
    scanqueue._last_scaleup["t"] = 0.0
    scanqueue._last_prewarm["t"] = 0.0
    scanqueue.scan_semaphore = asyncio.Semaphore(2)
    scanqueue.SCAN_CONCURRENCY = 2
    scanqueue.AVG_SCAN_SECONDS = 150
    yield


# ── bekleme tahmini ──────────────────────────────────────────────────────────

def test_bos_slotta_bekleme_YOK():
    assert scanqueue.estimate_wait_seconds() == 0


def test_bekleyen_varsa_tahmin_uretilir():
    scanqueue._counters["waiting"] = 1
    # (1+1)/2 = 1 parti → 150 sn
    assert scanqueue.estimate_wait_seconds() == 150


def test_tahmin_esszamanlilikla_bolunur():
    """4 kişi bekliyor, 2 eşzamanlı → (4+1)/2 = 3 parti."""
    scanqueue._counters["waiting"] = 4
    assert scanqueue.estimate_wait_seconds() == 3 * 150


def test_tum_slotlar_doluyken_bekleyen_YOKSA_da_tahmin_verilir():
    """🪤 `ahead == 0` tek başına yeterli değil: semafor kilitliyse yeni iş
    yine bekler. Naif `if ahead == 0: return 0` kullanıcıya '0 dk' derdi."""
    async def kilitle():
        await scanqueue.scan_semaphore.acquire()
        await scanqueue.scan_semaphore.acquire()
        return scanqueue.estimate_wait_seconds()
    assert asyncio.run(kilitle()) == 150


# ── slot sayacı ──────────────────────────────────────────────────────────────

def test_slot_alinca_sayac_SIZINTI_birakmaz():
    async def akis():
        await scanqueue.acquire_scan_slot()
        bekleyen_icerde = scanqueue._counters["waiting"]
        scanqueue.release_scan_slot()
        return bekleyen_icerde
    assert asyncio.run(akis()) == 0


def test_iptal_edilen_bekleyis_sayaci_TEMIZLER():
    """🪤 Kullanıcı isteği iptal ederse (bağlantı koptu) sayaç şişip kalmamalı;
    şişerse sonraki herkes olduğundan uzun bir bekleme görür."""
    async def akis():
        await scanqueue.scan_semaphore.acquire()
        await scanqueue.scan_semaphore.acquire()  # slot kalmadı
        gorev = asyncio.create_task(scanqueue.acquire_scan_slot())
        await asyncio.sleep(0)  # görev beklemeye girsin
        assert scanqueue._counters["waiting"] == 1
        gorev.cancel()
        try:
            await gorev
        except asyncio.CancelledError:
            pass
        return scanqueue._counters["waiting"]
    assert asyncio.run(akis()) == 0


# ── ECS soğuma penceresi (PARA) ──────────────────────────────────────────────

def test_env_yoksa_ECS_cagrilmaz(monkeypatch):
    """Yapılandırılmamışsa sessizce devre dışı — eski davranış korunur."""
    monkeypatch.setattr(scanqueue, "ECS_CLUSTER", "")
    monkeypatch.setattr(scanqueue, "ECS_WORKER_SERVICE", "")
    cagrildi = {"n": 0}
    monkeypatch.setattr(scanqueue, "_scale_worker_to_one",
                        lambda: cagrildi.__setitem__("n", cagrildi["n"] + 1) or True)
    assert asyncio.run(scanqueue.ensure_worker_running()) is False
    assert cagrildi["n"] == 0


def test_sogumada_IKINCI_cagri_ECS_e_GITMEZ(monkeypatch):
    """🪤 Asıl koruma bu: aynı anda 10 kullanıcı tarama başlatsa bile ECS'e
    tek çağrı gider. Bozulursa her istek `ecs:UpdateService` çağırır."""
    monkeypatch.setattr(scanqueue, "ECS_CLUSTER", "kume")
    monkeypatch.setattr(scanqueue, "ECS_WORKER_SERVICE", "worker")
    cagrildi = {"n": 0}

    def sahte():
        cagrildi["n"] += 1
        return True
    monkeypatch.setattr(scanqueue, "_scale_worker_to_one", sahte)

    async def akis():
        a = await scanqueue.ensure_worker_running(cooldown=30.0)
        b = await scanqueue.ensure_worker_running(cooldown=30.0)
        c = await scanqueue.ensure_worker_running(cooldown=30.0)
        return a, b, c
    a, b, c = asyncio.run(akis())
    assert (a, b, c) == (True, False, False)
    assert cagrildi["n"] == 1


def test_ECS_hatasi_YUTULUR_tarama_bozulmaz(monkeypatch):
    """Ölçeği büyütememek taramayı bozmaz, yalnız yavaşlatır — kullanıcı
    isteği bu yüzden 500 almamalı."""
    monkeypatch.setattr(scanqueue, "ECS_CLUSTER", "kume")
    monkeypatch.setattr(scanqueue, "ECS_WORKER_SERVICE", "worker")

    def patla():
        raise RuntimeError("ecs erisilemedi")
    monkeypatch.setattr(scanqueue, "_scale_worker_to_one", patla)
    assert asyncio.run(scanqueue.ensure_worker_running()) is False


def test_prewarm_kendi_GLOBAL_sogumasini_uygular(monkeypatch):
    """prewarm `ensure_worker_running(cooldown=0)` çağırıyor; koruma prewarm'ın
    KENDİ penceresinde. Bu ayrım bozulursa form dolduran her kullanıcı ECS'e
    ayrı bir çağrı üretir."""
    monkeypatch.setattr(scanqueue, "ECS_CLUSTER", "kume")
    monkeypatch.setattr(scanqueue, "ECS_WORKER_SERVICE", "worker")
    cagrildi = {"n": 0}
    monkeypatch.setattr(scanqueue, "_scale_worker_to_one",
                        lambda: cagrildi.__setitem__("n", cagrildi["n"] + 1) or True)

    async def akis():
        return (await scanqueue.enqueue_prewarm(cooldown=60.0),
                await scanqueue.enqueue_prewarm(cooldown=60.0))
    ilk, ikinci = asyncio.run(akis())
    assert ilk is True and ikinci is False
    assert cagrildi["n"] == 1


# ── SQS anahtarı ─────────────────────────────────────────────────────────────

def test_sqs_env_bosken_kapali(monkeypatch):
    monkeypatch.setattr(scanqueue, "SCAN_QUEUE_URL", "")
    assert scanqueue.sqs_enabled() is False


def test_sqs_env_doluyken_acik(monkeypatch):
    monkeypatch.setattr(scanqueue, "SCAN_QUEUE_URL",
                        "https://sqs.eu-central-1.amazonaws.com/1/kuyruk")
    assert scanqueue.sqs_enabled() is True
