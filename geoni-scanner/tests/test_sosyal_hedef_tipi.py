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


# ── Tip KANITTAN gelir, handle dizesinden TAHMİN edilmez ─────────────────
# Kurucunun sorusu: "nasıl ayıracaksın?" — haklı. Handle'ın kendisine bakarak
# karar vermek zayıf kanıt (`@kronotrop` hem kavurucu markası hem içerik hesabı).
# `_resolve_social_identity` ZATEN çekilmiş web sonuçlarına bakıp hesabın
# görünen adını ve platformunu çıkarıyor; aynı çağrıya `tip` alanı eklendi —
# **ek maliyet yok**, kanıt çok daha güçlü. Kanıt yoksa boş döner ve SOV kendi
# tahminine düşer (eski davranış).
def test_kimlik_cozumu_TIP_de_donuyor():
    br = (Path(__file__).resolve().parent.parent / "brand_recall.py").read_text(encoding="utf-8")
    i = br.index("async def _resolve_social_identity")
    govde = br[i:i + 2600]
    assert '"tip": "marka|uretici|null"' in govde, "kimlik çözümü tip sormuyor"
    assert 'tip not in ("marka", "uretici")' in govde, "beklenmedik tip değeri elenmiyor"
    assert '"tip": tip' in govde


def test_tip_SOVa_GECIRILIYOR():
    br = (Path(__file__).resolve().parent.parent / "brand_recall.py").read_text(encoding="utf-8")
    assert 'hedef_tipi=((resolved_identity or {}).get("tip") or "")' in br


def test_SOV_kanit_varsa_TAHMIN_ETMEZ():
    """Kanıt geldiyse model yeniden karar vermeye çalışmamalı."""
    assert 'hedef_tipi in ("marka", "uretici")' in KAYNAK
    assert "KANITLA belirlendi" in KAYNAK


def test_kanit_YOKSA_eski_davranis():
    """🪤 Geriye dönük: tip boşsa model yine kendisi karar verir, akış kırılmaz."""
    assert "ONCE karar ver" in KAYNAK


# ── Yapisal sifirin IKINCI kaynagi: cevaplanamayan soru ──────────────────
# @starbucks tip duzeltmesinden sonra 35 -> 58 cikti ama HALA dusuktu. Kirilim
# okundu (5 soru x 3 motor):
#
#   "En iyi kahve markalari hangileri"          -> 2/3 GECTI
#   "Turkiye'de hangi kahve zincirleri"         -> 3/3 GECTI
#   "Kahve turleri arasindaki fark, hangisini secmeliyim" -> 0/2
#   "Ev icin hangi kahve MAKINESI markalari"    -> 0/2
#   "Kahve AKSESUARLARI markalari"              -> 0/2
#
# Yani hedefin gercekten yarisabildigi iki soruda **5/6** geciyor; skoru asagi
# ceken uc soru hedefin YAPISAL OLARAK gecemeyecegi sorulardi:
#   - biri isim degil TUR/ACIKLAMA istiyor (cevabinda hicbir marka gecmez),
#   - ikisi BASKA BIR URUN SINIFI (kahve makinesi, fincan) — kahve zinciri
#     orada aranmaz.
# Bu yanlis sifirlar hedefi degil SORUYU cezalandiriyordu. Agirliga yine
# dokunulmadi; duzeltilen olcum araci.
def test_CEVAP_TESTI_iki_dalda_da_var():
    """Cevabi isim listesi olmayan soru uretilmemeli."""
    assert KAYNAK.count("CEVAP TESTI") == 2, "sosyal ve web dallarindan biri eksik"
    assert "ISIM LISTESI" in KAYNAK


def test_URUN_SINIFI_sabit_kurali_iki_dalda():
    """Komsu soru hedefin pazarini degistirmesin (kahve zinciri -> kahve makinesi)."""
    assert KAYNAK.count("URUN SINIFI SABIT") == 2


def test_sosyal_dalda_somut_ORNEK_var():
    """Soyut kural modele yetmiyor; olculen vakanin kendisi ornek olarak duruyor."""
    i = KAYNAK.index("URUN SINIFI SABIT")
    assert "kahve makinesi" in KAYNAK[i:i + 700]


def test_gerekce_kaynakta_YAZILI():
    assert "yanlis sifir" in KAYNAK
