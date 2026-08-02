"""Alintilanan kaynagin sayfa tipi (2026-08-02, GOLGE MOD).

Ilk olcum: AI Overview'in alintiladigi 61 tekil sayfanin ~%49'u liste/
karsilastirma, yalnizca %5'i rehber — ve rehber GEONI'nin urettigi TEK tipti.
Bu dosyadaki ornekler GERCEK olculen basliklardan alindi; elle denetimde
yanlis siniflanan vakalar da ayrica test edildi ki kural geriye gitmesin.
"""
import source_type as st


# ---------- liste / karsilastirma ----------

def test_sayili_en_iyi_listesi():
    for b in ("Türkiye'nin En İyi 10 GEO Ajansı (2026 Güncel Liste)",
              "En İyi 15 Yanıt Motoru Optimizasyonu Aracı - AIMultiple",
              "10 Best AI SEO Tools for 2026 (Ranked + Compared)",
              "7 Best AI SEO Tools 2026 (Comparison & Full Demo)"):
        assert st.siniflandir("https://x.com/a", b) == st.LISTE, b


def test_vs_ve_karsilastirma():
    assert st.siniflandir("https://s.com/blog/ai-gorunurluk-araclari-karsilastirma-2026/",
                          "AI Görünürlük Araçları 2026: GEO Builder vs Rakipler") == st.LISTE


def test_url_kalibindan_liste():
    assert st.siniflandir("https://agencies.semrush.com/list/ai-optimization/turkey/", "") == st.LISTE


def test_ELLE_DENETIMDEN_superlatif_cogul_varlik():
    """
    🪤 Ilk surumde 'diger' kutusuna dusen gercek vakalar: sayi YOK ama sayfa
    listedir. Denetimde yakalandi, kural eklendi.
    """
    for b in ("İstanbul'da AI SEO ve GEO Danışmanlığı Veren Firmalar (2026)",
              "En güvenli mesajlaşma uygulamaları - Kaspersky",
              "Türkiye'de GEO Hizmeti Veren Ajanslar (2026 Güncel Liste)"):
        assert st.siniflandir("https://x.com/a", b) == st.LISTE, b


def test_LINKEDIN_uzerindeki_liste_SOSYAL_degil_LISTE():
    """Sosyal host'a bakip liste kacirmayalim: icerik tipi barindirandan onemli."""
    assert st.siniflandir(
        "https://www.linkedin.com/pulse/10-best-ai-seo-tools-2026-ranked-compared-x",
        "10 Best AI SEO Tools for 2026 (Ranked + Compared) - LinkedIn") == st.LISTE


# ---------- rehber ----------

def test_rehber_taninir():
    for b in ("AI Görünürlük Optimizasyonu (AIVO) Rehberi - Webtures",
              "GEO (Generative Engine Optimization) nedir?",
              "How to create a marketing funnel"):
        assert st.siniflandir("https://x.com/a", b) == st.REHBER, b


# ---------- hizmet / urun ----------

def test_hizmet_sayfasi():
    for u, b in (("https://zeo.org/tr/generative-ai-danismanligi", "Generative AI Danışmanlığı - Zeo"),
                 ("https://yusufads.net/geo-danismanligi", "GEO Uzmanı Yusuf ŞAHİN | AI SEO & GEO Danışmanlığı"),
                 ("https://gercekci.com/geo-hizmeti/", "GEO Hizmeti — AI Arama Motorlarında Görünürlük")):
        assert st.siniflandir(u, b) == st.HIZMET, b


def test_ELLE_DENETIMDEN_ciplak_ana_sayfa_urundur():
    """🪤 'Mextup', 'DocuPost', 'Sprout Social' ilk surumde 'diger'e dusuyordu."""
    for u, b in (("https://www.mextup.com/", "Mextup: Mektup Yaz, Kolayca Gönder"),
                 ("https://docupost.com/", "DocuPost - Send Postal Mail Online"),
                 ("https://xperiencify.com", "Sprout Social")):
        assert st.siniflandir(u, b) == st.HIZMET, b


def test_uzun_baslikli_ana_sayfa_urun_sayilmaz():
    """Ciplak yol + UZUN baslik = muhtemelen makale; korumali kural."""
    uzun = "Yapay zeka ile marka gorunurlugu nasil olculur ve hangi adimlar atilmali detayli inceleme"
    assert st.siniflandir("https://x.com/", uzun) != st.HIZMET


# ---------- diger tipler ----------

def test_sosyal_video():
    assert st.siniflandir("https://www.youtube.com/watch?v=abc", "Kanal tanitimi") == st.SOSYAL
    assert st.siniflandir("https://www.instagram.com/p/XYZ/", "Gonderi") == st.SOSYAL


def test_haber():
    assert st.siniflandir("https://www.sondakika.com/haber/x-19787659/", "Bir gelisme") == st.HABER


# ---------- dagilim ----------

def test_dagilim_TEKIL_url_bazinda():
    """Ayni sayfanin bircok sorguda cikmasi dagilimi SISIRMEMELI."""
    k = [{"url": "https://a.com/en-iyi-10", "title": "En İyi 10 Araç"}] * 5 + \
        [{"url": "https://b.com/geo-danismanligi", "title": "GEO Danışmanlığı"}]
    d = st.dagilim(k)
    assert d["tekil_sayfa"] == 2
    assert d["dagilim"][st.LISTE] == 1


def test_dagilim_baskin_tip_ve_golge_bayragi():
    k = [{"url": f"https://a.com/en-iyi-{i}", "title": f"En İyi {i} Araç"} for i in range(1, 6)]
    k.append({"url": "https://b.com/nedir", "title": "GEO nedir?"})
    d = st.dagilim(k)
    assert d["baskin_tip"] == st.LISTE
    assert d["oran"][st.LISTE] > 0.8
    assert d["shadow"] is True, "golge bayragi dusrese dogrulanmamis olcum skor gibi gosterilir"


def test_bos_girdi_None():
    assert st.dagilim([]) is None
    assert st.dagilim([{"title": "url yok"}]) is None


def test_baslik_yoksa_patlamaz():
    assert st.siniflandir("https://a.com/x", "") in (st.DIGER, st.HIZMET, st.LISTE, st.REHBER)
    assert st.siniflandir("", "") == st.DIGER
