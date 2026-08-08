"""Otomatik (kendiliğinden) tarama KAPALI — kurucu kararı 2026-08-08.

Kurucunun cümlesi: **"artık otomatik tarama istemiyorum"**.

İzleme listesindeki hedefler 15 günde bir kendiliğinden yeniden taranıyordu:
11 hedef · 5 kullanıcı · ~$0,31/tarama ≈ **$7/ay**, karşılığında sıfır gelir.
Kullanıcının ELLE başlattığı taramalar bundan etkilenmez.

🪤 VARSAYILAN KAPALI olmak zorunda. Env okunup "yoksa aç" denseydi karar yeni
bir ortamda (yeni task definition, yerel koşu, geçici servis) SESSİZCE geri
gelir ve para yeniden akmaya başlardı — hafızadaki `feedback-sessiz-zayiflama`
dersinin aynısı.

🪤 Döngünün DİĞER işleri durmamalı: düşük-bakiye uyarısı ve retention temizliği
tarama değil bakım; onlar kapansaydı para uyarısını ve veri temizliğini
kaybederdik.

Bu testler `monitor`u IMPORT ETMEZ — modül httpx/fastapi zincirini çekiyor ve
CI'ın asgari ortamında toplama aşamasında düşerdi (bu hafta iki kez yaşandı).
Politika KAYNAK OKUNARAK kilitlenir.
"""
from pathlib import Path

KAYNAK = (Path(__file__).resolve().parent.parent / "monitor.py").read_text(encoding="utf-8")


def test_anahtar_VAR():
    assert "OTOMATIK_TARAMA_ACIK" in KAYNAK


def test_VARSAYILAN_KAPALI():
    """🪤 Asıl kapan: varsayılan '1' olursa karar sessizce geri gelir."""
    assert 'os.environ.get("MONITOR_AUTO_SCAN", "0")' in KAYNAK
    assert '.strip() == "1"' in KAYNAK


def test_kapaliyken_kuyruk_SORGULANMIYOR():
    """Kapalıyken saatlik boş DB sorgusu atmanın anlamı yok."""
    i = KAYNAK.index("async def monitor_loop")
    govde = KAYNAK[i:i + 1400]
    assert "if not OTOMATIK_TARAMA_ACIK:" in govde
    # tarama cagrisi anahtarin ARKASINDA olmali
    kapi = govde.index("if not OTOMATIK_TARAMA_ACIK:")
    assert govde.index("list_due_watchlist_items") > kapi


def test_bakim_isleri_DURMUYOR():
    """Düşük-bakiye uyarısı + retention temizliği tarama değil, bakım."""
    i = KAYNAK.index("async def monitor_loop")
    govde = KAYNAK[i:]
    assert "run_low_balance_alert" in govde
    assert "run_audit_retention" in govde
    # ikisi de anahtarın ETKİ ALANI DIŞINDA (ayrı try blokları)
    assert govde.count("OTOMATIK_TARAMA_ACIK") == 1


def test_elle_tarama_yolu_DEGISMEDI():
    """Kullanıcının kendi başlattığı tarama bu karardan etkilenmez."""
    assert "_process_item" in KAYNAK and "_scan_web_item" in KAYNAK


def test_gerekce_kaynakta_YAZILI():
    """Silinirse biri 'bu bayrak ne' deyip açar ve para yeniden akar."""
    assert "KURUCU KARARI" in KAYNAK
    assert "MONITOR_AUTO_SCAN=1" in KAYNAK
