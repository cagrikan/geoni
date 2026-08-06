"""Başarısız tarama SEBEBİYLE birlikte kaydedilir.

NE OLDU (2026-08-06'da canlı veriyle ölçüldü):
`cagricakir.com.tr` 4 Ağustos'ta iki kez `failed` oldu ve **sebebi hiçbir yerde
yoktu**: `result_json` NULL, hata mesajı yok. Sebep yalnız bellekteki
`jobs_store[job_id]["error"]` alanına yazılıyordu; App Runner boşta kalınca
süreç yeniden başlıyor, o sözlük sıfırlanıyor ve geriye teşhis edilemez bir
"status=failed" satırı kalıyor.

Beş başarısızlık yolunun BEŞİ de `update_audit_status(job_id, "failed")` diyordu
— sebep parametresi hiç yoktu.

KURAL: DB'ye yazılan her `failed` satırı sebebini de taşır.
"""
import db


class SahteYanit:
    status_code = 200


class SahteIstemci:
    """httpx.AsyncClient yerine: PATCH gövdesini yakalar."""

    son_patch = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def patch(self, url, headers=None, json=None, timeout=None):
        SahteIstemci.son_patch = json
        return SahteYanit()


def _kur(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "https://ornek.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "test-anahtar")
    monkeypatch.setattr(db.httpx, "AsyncClient", SahteIstemci)
    SahteIstemci.son_patch = None


def test_failed_SEBEBIYLE_kaydedilir(monkeypatch):
    """Asıl dava: sebep verilince result_json'a yazılmalı."""
    import asyncio
    _kur(monkeypatch)
    asyncio.run(db.update_audit_status("j1", "failed", error="insufficient_credits"))
    p = SahteIstemci.son_patch
    assert p["status"] == "failed"
    assert p["result_json"]["error"] == "insufficient_credits"
    assert "failed_at" in p["result_json"]
    assert "completed_at" in p          # eski davranış korunuyor


def test_sebep_YOKSA_result_json_yazilmaz(monkeypatch):
    """Geriye dönük uyum: sebepsiz çağrı eski davranışı korur."""
    import asyncio
    _kur(monkeypatch)
    asyncio.run(db.update_audit_status("j2", "failed"))
    assert "result_json" not in SahteIstemci.son_patch


def test_GERCEK_sonuc_sebebi_EZMEZ(monkeypatch):
    """`result` verilmişse o kazanır — tamamlanan tarama bozulmamalı."""
    import asyncio
    _kur(monkeypatch)
    asyncio.run(db.update_audit_status("j3", "complete", result={"score": 71}, score=71))
    p = SahteIstemci.son_patch
    assert p["result_json"] == {"score": 71}
    assert p["score"] == 71


def test_complete_durumunda_sebep_YAZILMAZ(monkeypatch):
    """Sebep yalnız failed/error dallarında anlamlı."""
    import asyncio
    _kur(monkeypatch)
    asyncio.run(db.update_audit_status("j4", "complete", error="olmamali"))
    assert "result_json" not in SahteIstemci.son_patch


def test_cok_uzun_sebep_KIRPILIR(monkeypatch):
    """Yığın izi satırı şişirmesin."""
    import asyncio
    _kur(monkeypatch)
    asyncio.run(db.update_audit_status("j5", "failed", error="x" * 5000))
    assert len(SahteIstemci.son_patch["result_json"]["error"]) == 500


def test_TUM_cagri_noktalari_sebep_veriyor():
    """🪤 Regresyon kapanı: biri sebep vermeden 'failed' yazarsa burada patlar.
    main.py IMPORT EDILMEZ — CI'in asgari ortamında fastapi yok."""
    import re
    from pathlib import Path
    kaynak = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    sebepsiz = re.findall(r'update_audit_status\([^)]*"failed"\s*\)', kaynak)
    assert not sebepsiz, f"sebep vermeden 'failed' yazan {len(sebepsiz)} çağrı var"
