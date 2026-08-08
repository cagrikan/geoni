"""Paylaşım verisi sayfa sayısını taşır — rozet okunamayan siteye verilmesin.

NE OLDU (2026-08-08'de canlı veriyle ölçüldü): "AI Friendly · Checked by GEONI"
rozeti YALNIZ skora bakıyordu. Botları engelleyen, sitesinden **tek sayfa bile
okuyamadığımız** bir alan 80 alıp mührü kendi sitesine gömebiliyordu.

    son 30 günde 70+ alan tarama: 19
    bunların SIFIR sayfa olanı:    5   (%26,3)

Örnekler (hepsi bot engelli, 0 sayfa): iyzico.com 82 · parasut.com 80 ·
doktortakvimi.com 80 · n11.com 76.

Lig aynı bütünlük kuralını **zaten** uyguluyordu (`MIN_CRAWLED_PAGES = 3`,
`db.get_ai_friendly_list`) — rozet uygulamıyordu. Tek kural, iki farklı yerde
farklı davranıyordu. Rozet bizim kamuya açık güvenilirlik işaretimiz; okumadığımız
bir siteye vermek onu değersizleştirir.

🪤 `pages` alanı payload'a EKLENDİ, hiçbir alan kaldırılmadı — eski istemciler
kırılmaz. Rozet ucu da alan YOKSA engellemiyor (eski/önbellekli yanıt), yalnız
KESİN olarak yetersiz olduğunda reddediyor.
"""
import re
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
DB = (KOK / "db.py").read_text(encoding="utf-8")
BLOK = DB[DB.index("async def get_share_result"):]
BLOK = BLOK[:BLOK.index("# Ligde gizlenen")]


def test_paylasim_verisi_pages_tasiyor():
    """Rozet ucu sayfa sayısını başka yerden öğrenemez."""
    assert '"pages": _tam_sayi_ya_da_none(rj.get("total_pages"))' in BLOK


def test_kaynak_total_pages_dizinin_UZUNLUGU_DEGIL():
    """🪤 `pages` dizisi 20'de kırpılıyor (ölçüldü: heroku.com total_pages=26,
    dizi=20). Lig `result_json->>total_pages` okuyor — aynı kural aynı alandan."""
    assert 'len(rj.get("pages")' not in BLOK, "kırpılmış diziden sayı üretiliyor"
    assert 'result_json->>total_pages' in DB, "lig tarafı da aynı alanı okumalı"


def test_YOK_ile_SIFIR_ayri():
    """🪤 Asıl tuzak: retention eski satırların result_json'unu tamamen NULL'luyor
    (ölçüldü 2026-08-08: armut.com 77, cagricakir.com.tr 75 — anahtar bile yok).
    `int(v or 0)` yazılsaydı düzgün taranmış ESKİ sitelerin rozeti kırılırdı."""
    from importlib import import_module
    f = import_module("db")._tam_sayi_ya_da_none
    assert f(None) is None, "yok -> None olmalı (rozet engellenmez)"
    assert f(0) == 0, "sıfır -> 0 kalmalı (rozet engellenir)"
    assert f("26") == 26
    assert f("bozuk") is None


def test_mevcut_alanlar_KALDIRILMADI():
    """🪤 Geriye dönük uyum: eski istemciler bu alanları bekliyor."""
    for alan in ("job_id", "type", "label", "score", "recognized", "created_at"):
        assert f'"{alan}"' in BLOK, f"{alan} payload'dan düşmüş"


def test_gerekce_kaynakta_YAZILI():
    """Silinirse biri 'bu alan ne işe yarıyor' deyip kaldırır."""
    assert "ROZET ICIN SART" in BLOK
    assert "MIN_CRAWLED_PAGES" in BLOK


def test_lig_esigi_DEGISMEDI():
    """Rozet ligle aynı eşiği kullanmalı; lig tarafı bozulmamış olsun."""
    assert "MIN_CRAWLED_PAGES = 3" in DB
    assert 'int(row.get("pages") or 0) < MIN_CRAWLED_PAGES' in DB


def test_rozet_ucu_sayfa_kontrolu_yapiyor():
    """🪤 Asıl kapan: rozet JS'i sayfa şartını uygulamalı ve eşik ligle aynı olmalı."""
    js = (KOK.parent / "api" / "badge" / "[id].js").read_text(encoding="utf-8")
    assert "MIN_PAGES = 3" in js
    assert "data.pages" in js
    assert "typeof data.pages === 'number'" in js, "alan yoksa engellememeli"
    m = re.search(r"data\.pages < MIN_PAGES", js)
    assert m, "sayfa karşılaştırması yok"
