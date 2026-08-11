"""Otomatik tarama VARSAYILAN KAPALI — bilinçli açılır.

TARİHÇE (ikisi de geçerli, silme):

1. **2026-08-08 — kapatıldı.** Kurucunun cümlesi: "artık otomatik tarama
   istemiyorum". İzleme listesindeki hedefler 15 günde bir kendiliğinden
   yeniden taranıyordu: 11 hedef · 5 kullanıcı · ~$0,31/tarama ≈ **$7/ay**,
   karşılığında sıfır gelir — çünkü izleme taramaları ÜCRETSİZDİ.

2. **2026-08-12 — ücretlendirildi.** Kurucunun cümlesi: "bize maliyet hep
   aynı". İzleme taraması artık elle taramayla aynı kontörü düşüyor
   (`monitor.py` → `deduct=True`, bedel `scan_costs.py`'den). Kapatma
   gerekçesi ortadan kalktı; özellik açılabilir hale geldi.

🪤 VARSAYILAN yine KAPALI olmak zorunda — ama artık BAŞKA bir sebeple.
Eskiden risk "bedava iş"ti; şimdi risk **kullanıcının kontörünün habersiz
düşmesi**. Env okunup "yoksa aç" denseydi yeni bir ortamda (yeni task
definition, yerel koşu, geçici servis) tarama sessizce başlar ve müşteri
parası yanardı. Açmak bilinçli bir hareket olmalı: `MONITOR_AUTO_SCAN=1`.

Kullanıcının ELLE başlattığı taramalar bu karardan hiç etkilenmez.

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
    """Silinirse biri 'bu bayrak ne' deyip açar; bu sefer MUSTERI parasi yanar.

    Iki tarih de kaynakta durmali: kapatma gerekcesi (2026-08-08) ve
    ucretlendirme (2026-08-12). Yalniz biri kalirsa okuyan yanlis sonuc cikarir
    — ornegin sadece kapatma yazsa "bu ozellik olu" sanilir, sadece ucretlendirme
    yazsa "acik olmali" sanilir.
    """
    assert "MONITOR_AUTO_SCAN=1" in KAYNAK
    assert "2026-08-08" in KAYNAK, "kapatma gerekcesi silinmis"
    assert "2026-08-12" in KAYNAK, "ucretlendirme gerekcesi silinmis"


def test_izleme_taramasi_UCRETLI():
    """Bayrak acilinca tarama kontor DUSMELI — yoksa 2026-08-08'e geri doneriz.

    🪤 Bu test once `"deduct=False" not in KAYNAK` diye yazilmisti ve YANLIS
    patladi: o metin kaynakta YORUM icinde geciyor (tarihcenin bir parcasi
    olarak). Substring araması kodu degil aciklamayi olcuyordu — tam da
    "politika kapani" denen hata. AST ile GERCEK cagri argumanina bakiliyor.

    Ayrintili davranissal testler: tests/test_izleme_ucretlendirme.py
    """
    import ast
    agac = ast.parse(KAYNAK)
    degerler = []
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Call):
            continue
        ad = getattr(dugum.func, "id", None) or getattr(dugum.func, "attr", None)
        if ad not in ("save_audit", "save_brand_check"):
            continue
        for kw in dugum.keywords:
            if kw.arg == "deduct":
                degerler.append((ad, ast.literal_eval(kw.value)))

    assert degerler, "monitor.py'de kayit cagrisi bulunamadi"
    for ad, deger in degerler:
        assert deger is True, (
            f"{ad}(deduct={deger}) — izleme ucretsiz hale gelmis; "
            "2026-08-08 kapatma gerekcesi ('bedava is, sifir gelir') geri doner"
        )


def test_UC_YER_KURALI_kaynakta_YAZILI():
    """Sunucu bayragi tek basina yetmez; arayuz bayraklariyla birlikte degisir.

    Sunucu acik + arayuz kapali: is yapiliyor, kontor dusuyor, kullanici
    "Kayitli" goruyor — yapilan isi (ve alinan parayi) SAKLIYORUZ.
    Sunucu kapali + arayuz acik: "15 gunde bir taranir" deniyor, taranmiyor.
    Iki yon de yalan; uyarinin kaynakta durmasi sart.
    """
    assert "otomatikIzleme" in KAYNAK, (
        "arayuz bayragiyla birlikte degismesi gerektigi uyarisi silinmis"
    )
