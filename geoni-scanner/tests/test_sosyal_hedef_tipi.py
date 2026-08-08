"""Sosyal SOV sorusu HEDEF TİPİNE göre yazılır — marka "takip" sorusuna düşmez.

CANLIDA YAKALANDI (2026-08-08, kurucu): `@starbucks` / niş "Kahve" taraması
**35/100** verdi. Kırılım:

    claude 81.6 · gemini 89.9 · chatgpt 82.5 · perplexity 88.2
    yanit_kalitesi 85.0
    kategori_gorunurlugu (SOV) 0.0     ← skoru 35'e çeken tek şey

Dört motor da markayı ~85 tanıyor. SOV neden 0? Çünkü sosyal mod ÜRETİLEN
BEŞ SORUNUN HEPSİNDE *"kahve için hangi hesapları takip etmeliyim"* diye
soruyordu. Starbucks bir **marka**, içerik üreticisi değil — AI o soruya doğru
cevabı veriyor (James Hoffmann, Kahve Sakal, kavurucular) ve marka geçmiyor.
**Geçmemesi de doğru.** Bulunan "rakipler" de bunu ele veriyordu:
`@coffeesesh`, `@jgagneastrocoffee`, `@whole_latte_coffee` — hepsi içerik hesabı.

Sosyal modda SOV ağırlığı **0.55** (WEIGHTS_SOCIAL) olduğu için tek başına
skorun yarısından fazlası. Yani mod, hedef bir markaysa onu yapısal olarak
sıfıra mahkûm ediyordu.

🪤 **Ağırlık DÜŞÜRÜLMEDİ.** Gerçek bir influencer kategori sorusunda hiç
geçmiyorsa SOV=0 **doğru sinyaldir**; ağırlığı kırpmak o gerçeği de susturur.
Yanlış olan ölçüm değil, SORUYDU.

🪤 Test `sov`u import etmez (httpx zinciri; CI'ın asgari ortamı toplama
aşamasında düşerdi). Kaynak okunarak kilitlenir.
"""
from pathlib import Path

KAYNAK = (Path(__file__).resolve().parent.parent / "sov.py").read_text(encoding="utf-8")
BLOK = KAYNAK[KAYNAK.index("    elif social:"):]
BLOK = BLOK[:BLOK.index("    else:\n        prompt = (")]


def test_hedef_tipi_ONCE_sorulur():
    """Soru yazılmadan önce marka mı üretici mi kararı verilmeli."""
    assert "ONCE karar ver" in BLOK
    assert "MARKA/sirket hesabi mi" in BLOK
    assert "ICERIK URETICISI" in BLOK


def test_MARKA_dalinda_takip_sorusu_YASAK():
    """🔴 Asıl kapan: marka hedefte 'kimi takip etmeliyim' sorulursa SOV yapısal 0."""
    i = BLOK.index("MARKA/sirket ise")
    marka = BLOK[i:i + 400]
    assert "markalari hangileri" in marka or "MARKA/URUN/MEKAN" in marka
    assert "'Kimi takip etmeliyim' sorusu YAZMA" in marka


def test_URETICI_dalinda_takip_sorusu_KORUNDU():
    """Gerçek içerik hesabında eski davranış aynen kalmalı."""
    i = BLOK.index("ICERIK URETICISI/kisi ise")
    uretici = BLOK[i:i + 300]
    assert "kimi takip etmeliyim" in uretici


def test_hedef_adi_prompta_GECIYOR():
    """Tip kararı verilebilmesi için hesabın adı prompt'ta olmalı."""
    assert "Hedef hesap: '{name}'" in BLOK


def test_hedef_tipi_JSON_ciktisinda():
    """Tanıda görünsün: hangi tip seçildi sonradan ölçülebilmeli."""
    assert '"hedef_tipi"' in BLOK


def test_AGIRLIK_DUSURULMEDI():
    """🪤 Belirtiyi bastırma kapanı: SOV'un sosyal ağırlığı 0.55 kalmalı."""
    br = (Path(__file__).resolve().parent.parent / "brand_recall.py").read_text(encoding="utf-8")
    i = br.index("WEIGHTS_SOCIAL")
    assert "0.55" in br[i:i + 400], "sosyal SOV ağırlığı değişmiş — belirti bastırılıyor olabilir"


def test_gerekce_kaynakta_YAZILI():
    assert "starbucks" in BLOK.lower()
    assert "yapisal olarak sifir" in BLOK
