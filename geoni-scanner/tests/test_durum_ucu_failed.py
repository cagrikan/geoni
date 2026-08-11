"""Durum uçları: `failed` = 200 + gövde; web ucunda terk-edilmiş iş kontrolü.

İKİ BULGU (fonksiyonel denetim 2026-08-12):

K2 — Web durum ucu non-terminal satırı YAŞ KONTROLÜ olmadan aynen döndürüyordu.
Worker 3 kez çöküp mesaj DLQ'ya düşünce (ya da durum PATCH'i sessizce yutulunca)
satır sonsuza dek "crawling"de kalıyor, istemci tavana kadar polluyordu.
Marka ucundaki 20 dk kuralı web'e de uygulandı.

Ö7 — `failed` her yerde 500'dü. İstemciler 500'ü "geçici sunucu hatası" sayıp
yeniden deniyor; terminal başarısızlık geçici hatadan ayırt edilemiyor ve sebep
(`insufficient_credits`) hiçbir yüzeye ulaşmıyordu — web/mobil `status=='failed'`
dalları ölü koddu. Artık 200 + `{status:"failed", error:<kod>}`; ham istisna
metni SIZMAZ, yalnız `_GUVENLI_HATA_KODLARI` listesindekiler aynen geçer.

Testler endpoint coroutine'lerini doğrudan çağırır (ağ yok, DB sahte).
🪤 `main` MODÜL SEVİYESİNDE import edilmez (deploy kapısının minimal ortamında
fastapi yok; bkz. test_dependency_surface) — `ana` fixture'ı yükler.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def ana():
    pytest.importorskip("fastapi", reason="deploy kapısının minimal ortamında yok")
    import main
    return main


def _jid():
    return str(uuid.uuid4())


def _kur(monkeypatch, main, row):
    async def _row(job_id):
        return row
    monkeypatch.setattr(main, "sqs_enabled", lambda: True)
    monkeypatch.setattr(main, "get_audit_row", _row)


def _eski_damga(dakika):
    return (datetime.now(timezone.utc) - timedelta(minutes=dakika)).isoformat()


# ── failed: 200 + gövde, sebep güvenli koda süzülür ─────────────────────────

def test_web_failed_SEBEBIYLE_200_doner(monkeypatch, ana):
    _kur(monkeypatch, ana, {"status": "failed",
                            "result_json": {"error": "insufficient_credits"}})
    r = asyncio.run(ana.get_audit_status(_jid()))
    assert r["status"] == "failed"
    assert r["error"] == "insufficient_credits", \
        "sebep istemciye ulaşmıyor — 'kredi yükle' CTA'sı ölü kalır"


def test_web_failed_HAM_istisna_metni_SIZMAZ(monkeypatch, ana):
    """update_audit_status DB'ye 'TypeError: ...' yazabiliyor; istemciye
    yalnız bilinen makine kodları gider, gerisi jenerik koda maskelenir."""
    _kur(monkeypatch, ana, {"status": "failed",
                            "result_json": {"error": "TypeError: sunucu içi detay"}})
    r = asyncio.run(ana.get_audit_status(_jid()))
    assert r["status"] == "failed"
    assert r["error"] == "tarama_basarisiz"
    assert "TypeError" not in str(r)


def test_web_failed_result_json_NULL_patlamaz(monkeypatch, ana):
    """Eski failed satırlarında result_json NULL (sebep yazılmıyordu)."""
    _kur(monkeypatch, ana, {"status": "failed", "result_json": None})
    r = asyncio.run(ana.get_audit_status(_jid()))
    assert r == {"job_id": r["job_id"], "status": "failed",
                 "error": "tarama_basarisiz"}


def test_web_bellekteki_failed_de_200_ve_guvenli_kod(ana):
    jid = _jid()
    ana.jobs_store[jid] = {"job_id": jid, "status": "failed",
                           "error": "insufficient_credits"}
    try:
        r = asyncio.run(ana.get_audit_status(jid))
        assert r["status"] == "failed" and r["error"] == "insufficient_credits"
    finally:
        ana.jobs_store.pop(jid, None)


def test_brand_failed_SEBEBIYLE_200_doner(monkeypatch, ana):
    _kur(monkeypatch, ana, {"status": "failed",
                            "result_json": {"error": "insufficient_credits"}})
    r = asyncio.run(ana.get_brand_check_status(_jid()))
    assert r["status"] == "failed" and r["error"] == "insufficient_credits"


def test_brand_bellekteki_failed_ham_metin_SIZMAZ(ana):
    jid = _jid()
    ana.brand_checks_store[jid] = {"job_id": jid, "status": "failed",
                                   "error": "KeyError: 'gizli_detay'"}
    try:
        r = asyncio.run(ana.get_brand_check_status(jid))
        assert r["status"] == "failed" and r["error"] == "tarama_basarisiz"
    finally:
        ana.brand_checks_store.pop(jid, None)


# ── K2: web ucunda terk edilmiş iş ──────────────────────────────────────────

def test_web_21dk_lik_crawling_satiri_FAILED_doner(monkeypatch, ana):
    """🔴 Asıl dava: kimsenin güncellemeyeceği satır sonsuz 'crawling' dönmesin."""
    _kur(monkeypatch, ana, {"status": "crawling", "result_json": None,
                            "created_at": _eski_damga(21)})
    r = asyncio.run(ana.get_audit_status(_jid()))
    assert r["status"] == "failed"
    assert r["error"] == "tarama_zaman_asimi"


def test_web_taze_crawling_satiri_AYNEN_gecer(monkeypatch, ana):
    """Eşik çalışan işi öldürmesin: 20 dk'dan genç satır dokunulmadan döner."""
    _kur(monkeypatch, ana, {"status": "crawling", "result_json": None,
                            "created_at": _eski_damga(2)})
    r = asyncio.run(ana.get_audit_status(_jid()))
    assert r["status"] == "crawling"


def test_web_damga_cozulemezse_ESKI_SAYILMAZ(monkeypatch, ana):
    """🪤 Güvenli taraf: created_at okunamıyorsa çalışan işi düşürme
    (_satir_yasi_sn None → kontrol atlanır) — marka ucundaki kuralın aynısı."""
    _kur(monkeypatch, ana, {"status": "scoring", "result_json": None,
                            "created_at": "bozuk-damga"})
    r = asyncio.run(ana.get_audit_status(_jid()))
    assert r["status"] == "scoring"


def test_brand_terk_edilmis_is_de_200_failed_govdesi(monkeypatch, ana):
    """Marka ucu 500 yerine artık aynı gövdeyi döner (istemci simetrisi)."""
    _kur(monkeypatch, ana, {"status": "queued", "result_json": None,
                            "created_at": _eski_damga(25)})
    monkeypatch.setattr(ana.job_store, "oku", lambda job_id: None)
    r = asyncio.run(ana.get_brand_check_status(_jid()))
    assert r["status"] == "failed" and r["error"] == "tarama_zaman_asimi"
