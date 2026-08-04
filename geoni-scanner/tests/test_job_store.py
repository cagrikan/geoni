"""Özel tarama sonucunun çok-instance'ta kaybolmaması (kör denetim 2026-08-04).

NE OLDU: `private=True` kişi/marka/sosyal taramaların sonucu bilinçli olarak
DB'ye yazılmaz ("hiçbir yerde kaydedilmedi" sözü). Ama bu uçlar SQS'e düşmez,
istekle aynı süreçte koşar. Poll isteği BAŞKA bir instance'a düşerse:
    bellekte yok -> DB'de de yok -> 404
Kullanıcı kredisini ödemiştir (düşüm sonuçtan ÖNCE), raporunu asla göremez.
App Runner MaxSize=25; bugün tek instance'ta gizli, ölçekte kaçınılmaz.

ÇÖZÜM: TTL'li paylaşımlı depo. Kalıcı kayıt DEĞİL — süresi dolunca silinen bir
teslim kuyruğu, yani "kaydedilmedi" sözü korunuyor.
"""
from pathlib import Path

import job_store

_KOK = Path(__file__).resolve().parent.parent


def test_redis_yoksa_sessizce_devre_disi():
    """Redis kurulu değilse modül çalışmayı ENGELLEMEMELİ — eski davranış
    (yalnız bellek) sürer, hiçbir şey kırılmaz."""
    if job_store.aktif():
        return  # bu ortamda Redis var, fallback yolu ayrıca test edilemez
    assert job_store.yaz("x", {"a": 1}) is False
    assert job_store.oku("x") is None
    job_store.sil("x")   # patlamamalı


def test_ttl_makul():
    """TTL en uzun taramayı (ölçülen max 44 dk) karşılamalı ama kalıcı olmamalı."""
    assert 1800 <= job_store.TTL_SANIYE <= 21600


def test_ozel_tarama_paylasimli_depoya_yaziliyor():
    """main.py private dalında job_store.yaz çağrılmalı; yoksa çok-instance'ta
    kullanıcı ödediği raporu göremez."""
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    kod = "\n".join(l for l in kaynak.splitlines() if not l.strip().startswith("#"))
    assert "job_store.yaz(job_id" in kod, "özel tarama paylaşımlı depoya yazılmıyor"
    assert "job_store.oku(job_id)" in kod, "bellek miss'inde paylaşımlı depo okunmuyor"


def test_ozel_tarama_dbye_yazilmiyor():
    """Söz korunmalı: private dal audits'e KAYIT yazmamalı."""
    kaynak = (_KOK / "main.py").read_text(encoding="utf-8")
    bas = kaynak.index("async def run_brand_check_job")
    son = kaynak.index("async def ", bas + 10)
    govde = "\n".join(l for l in kaynak[bas:son].splitlines()
                      if not l.strip().startswith("#"))
    # private dalında save_brand_check çağrısı olmamalı
    ozel_bas = govde.index("if request.private:")
    ozel_son = govde.index("else:", ozel_bas)
    assert "save_brand_check" not in govde[ozel_bas:ozel_son], \
        "özel tarama DB'ye yazılıyor — 'hiçbir yere kaydedilmedi' sözü bozuldu"
