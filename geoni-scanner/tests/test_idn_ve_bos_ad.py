"""Fonksiyonel testte (2026-08-08) çıkan iki kusur — kapanları.

## 1) Türkçe karakterli (IDN) alan adları HİÇ taranamıyordu
`normalize_domain` deseni yalnız ASCII kabul ediyordu; `örnek.com` gibi bir
hedef `None` dönüyor, uç **422 "Geçersiz web sitesi adresi"** veriyordu.
Pazar Türkiye — ö/ü/ç/ş/ğ/ı içeren bir alan adının sahibi ürünü hiç
kullanamıyordu ve nedenini de anlamıyordu.

🪤 DNS zaten punycode ister; dönüştürüp devam etmek hem doğru hem de kanonik
anahtarı tekilleştirir (`örnek.com` ile `xn--rnek-4qa.com` aynı satıra düşer).
Bu değişiklik mevcut anahtarları BOZMAZ: o girdiler eskiden `None` dönüyordu,
yani hiçbir kayıt onlara bağlı değildi.

## 2) Boş isimle marka/kişi taraması başlatılabiliyordu
`name` alanında yalnız `max_length` vardı. `""` ve `"  "` şemayı geçiyordu;
ücretsiz hak kapısı da geçilirse dört AI motoruna **boş isim** sorulup ~$0,31
boşa yanıyor, kullanıcı anlamsız bir rapor alıyordu.

🪤 `min_length` HAM dizeyi sayar — `"  "` (iki boşluk) sınavı geçer. Bu yüzden
ayrıca kırpılmış uzunluk doğrulanır ve kırpılmış değer döndürülür.

🪤 Testler `main`i import ETMEZ (fastapi zinciri; CI'ın asgari ortamı toplama
aşamasında düşerdi). `db` import edilir — o zaten httpx ile yetiniyor.
"""
import re
from pathlib import Path

import db

KAYNAK = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


# ── 1) IDN ────────────────────────────────────────────────────────────────
def test_turkce_karakterli_alan_KABUL_EDILIYOR():
    assert db.normalize_domain("örnek.com") == "xn--rnek-4qa.com"
    assert db.normalize_domain("şişli-emlak.com.tr") == "xn--ili-emlak-z2bb.com.tr"


def test_IDN_buyuk_harf_ve_semali_biçim_de_ayni_sonuca_iner():
    """Kanonik anahtar tek olmalı: aynı site iki satıra bölünmesin."""
    hedef = "xn--rnek-4qa.com"
    for g in ("ÖRNEK.com", "http://örnek.com/a?b=1", "www.örnek.com", "  örnek.com  "):
        assert db.normalize_domain(g) == hedef, g


def test_ASCII_davranisi_DEGISMEDI():
    """🪤 Regresyon: mevcut anahtarlar aynı kalmalı."""
    assert db.normalize_domain("geoni.ai") == "geoni.ai"
    assert db.normalize_domain("www.geoni.ai") == "geoni.ai"
    assert db.normalize_domain("Cagricakir.com.tr") == "cagricakir.com.tr"


def test_alan_adi_OLMAYAN_hedefler_hala_None():
    """Kişi/marka/sosyal hedefleri domain sanılmamalı."""
    for g in ("ali@x.com", "boşluklu ad", " ", "", "sadecekelime", None):
        assert db.normalize_domain(g) is None, g


def test_cozulemeyen_IDN_None_doner():
    """Dönüştürülemeyen girdi patlamamalı, sessizce elenmeli."""
    assert db.normalize_domain("​​.com") is None


# ── 2) Boş ad ─────────────────────────────────────────────────────────────
def test_name_alaninda_min_length_VAR():
    m = re.search(r"name:\s*str\s*=\s*Field\(\.\.\.,\s*min_length=(\d+),\s*max_length=200\)", KAYNAK)
    assert m, "BrandCheckRequest.name'de min_length yok"
    assert int(m.group(1)) >= 2


def test_bosluktan_ibaret_ad_da_ELENIYOR():
    """🪤 min_length ham dizeyi sayar; '  ' onu geçer. Kırpma doğrulaması şart."""
    i = KAYNAK.index("def _ad_bos_olamaz")
    govde = KAYNAK[i:i + 400]
    assert 'v = (v or "").strip()' in govde
    assert "len(v) < 2" in govde
    assert "raise ValueError" in govde


def test_gerekce_kaynakta_YAZILI():
    assert "min_length ZORUNLU" in KAYNAK
    assert "IDN" in (Path(__file__).resolve().parent.parent / "db.py").read_text(encoding="utf-8")


# ── 3) GÖSTERİM biçimi (2026-08-08, yarım kalan işin ikinci yarısı) ────────
# IDN desteği eklenince `örnek.com` yazan kullanıcı raporunda ve paylaşım
# kartında `xn--rnek-4qa.com` görüyordu — teknik olarak doğru, insan için
# anlamsız. Depolama punycode KALIR (DNS + kanonik anahtar), gösterim çözülür.
def test_gorunen_domain_punycode_cozuyor():
    assert db.gorunen_domain("xn--rnek-4qa.com") == "örnek.com"
    assert db.gorunen_domain("xn--ili-emlak-z2bb.com.tr") == "şişli-emlak.com.tr"


def test_gorunen_domain_ASCII_ve_bozuk_degeri_BOZMAZ():
    assert db.gorunen_domain("geoni.ai") == "geoni.ai"
    assert db.gorunen_domain("") == ""
    assert db.gorunen_domain(None) == ""
    # 🪤 "xn--" ile baslamayan degere hic dokunulmaz
    assert db.gorunen_domain("bozuk--xn.com") == "bozuk--xn.com"


def test_paylasim_etiketi_ve_lig_GOSTERIM_bicimini_kullaniyor():
    kaynak = (Path(__file__).resolve().parent.parent / "db.py").read_text(encoding="utf-8")
    assert 'gorunen_domain(row.get("domain"))' in kaynak, "paylasim etiketi ham domain gosteriyor"
    assert '"domain": gorunen_domain(d)' in kaynak, "lig ham domain gosteriyor"


def test_DEPOLAMA_hala_punycode():
    """🪤 Kanonik anahtar DEĞİŞMEMELİ: normalize hâlâ punycode üretir."""
    assert db.normalize_domain("örnek.com") == "xn--rnek-4qa.com"
