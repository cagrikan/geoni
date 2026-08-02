"""Alintilanabilirlik + yapisal okunabilirlik (2026-08-02, GOLGE MOD).

NEDEN: urunun vaadi "AI seni ALINTILASIN" ama sayfanin alintilanabilir olup
olmadigina hic bakmiyorduk. claude-seo karsilastirmasinda cikan en buyuk
bosluk buydu (onlarda Citability 0.25 + Structural 0.20; bizde ikisi de yok).

SKORA GIRMEZ. Dayandigi arastirma degerleri (134-167 kelime, atiflarin %44'u
ilk %30'dan) claude-seo'nun KAYNAK GOSTERDIGI degerlerdir, tarafimizdan
dogrulanmadi. Dogrulamadigimiz bir arastirmayi skora sokmak kendi kuralimizi
(olcmediysen soyleme) cignemek olurdu — once veri biriksin.
"""
import citability


def _p(kelime: int) -> str:
    return " ".join(["kelime"] * kelime)


def _sayfa(**kw) -> dict:
    d = {"headings": [{"level": 1, "text": "Baslik"}], "paragraphs": [_p(150)],
         "list_count": 0, "table_count": 0, "faq": False}
    d.update(kw)
    return d


# ---------- pasaj uzunlugu ----------

def test_ideal_uzunluktaki_pasaj_sayilir():
    a = citability.analiz_et(_sayfa(paragraphs=[_p(150), _p(140)]))
    assert a["ideal_pasaj"] == 2


def test_cok_kisa_ve_cok_uzun_pasaj_ideal_sayilmaz():
    a = citability.analiz_et(_sayfa(paragraphs=[_p(15), _p(600)]))
    assert a["ideal_pasaj"] == 0
    assert a["kabul_pasaj"] == 0


def test_kabul_araligi_idealden_genis():
    """Kenarlar yumusak: 100-220 kabul, disi yalnizca isaretlenir."""
    a = citability.analiz_et(_sayfa(paragraphs=[_p(110), _p(210)]))
    assert a["ideal_pasaj"] == 0 and a["kabul_pasaj"] == 2


# ---------- konum: atiflarin %44'u ilk %30'dan ----------

def test_ust_bolumde_alintilanabilir_blok_yakalanir():
    a = citability.analiz_et(_sayfa(paragraphs=[_p(150)] + [_p(10)] * 9))
    assert a["ust_bolumde_alintilanabilir"] is True


def test_alintilanabilir_blok_sayfanin_dibindeyse_yakalanmaz():
    """Gomulmus cevap: AI atifi ust kisimdan aliyor, dipteki blok ise yaramaz."""
    a = citability.analiz_et(_sayfa(paragraphs=[_p(10)] * 9 + [_p(150)]))
    assert a["ust_bolumde_alintilanabilir"] is False


# ---------- yapi ----------

def test_soru_basligi_TR_ve_EN_taninir():
    a = citability.analiz_et(_sayfa(headings=[
        {"level": 2, "text": "GEO nedir?"},
        {"level": 2, "text": "How does it work"},
        {"level": 2, "text": "Fiyatlandirma"},
    ]))
    assert a["soru_basligi"] == 2


def test_hiyerarsi_atlamasi_yakalanir():
    a = citability.analiz_et(_sayfa(headings=[{"level": 1, "text": "A"}, {"level": 4, "text": "B"}]))
    assert a["hiyerarsi_atlamasi"] is True


def test_duzgun_hiyerarsi_atlama_sayilmaz():
    a = citability.analiz_et(_sayfa(headings=[
        {"level": 1, "text": "A"}, {"level": 2, "text": "B"}, {"level": 3, "text": "C"}]))
    assert a["hiyerarsi_atlamasi"] is False


def test_bos_sayfa_None():
    assert citability.analiz_et({"headings": [], "paragraphs": []}) is None


# ---------- site ozeti ----------

def test_site_ozeti_oranlari():
    iyi = _sayfa(paragraphs=[_p(150), _p(140)], list_count=2, faq=True,
                 headings=[{"level": 1, "text": "Nedir?"}])
    kotu = _sayfa(paragraphs=[_p(700)], headings=[{"level": 1, "text": "X"}, {"level": 4, "text": "Y"}])
    o = citability.site_ozeti([iyi, kotu])
    assert o["sayfa"] == 2
    assert 0 < o["alintilanabilir_oran"] < 1
    assert o["hiyerarsi_bozuk_oran"] == 0.5
    assert o["faq_oran"] == 0.5
    assert o["soru_basligi_toplam"] == 1


def test_site_ozeti_GOLGE_MOD_isaretli():
    """
    Istemci 'deneysel · skora katilmiyor' etiketi basabilsin. Bu bayrak
    dusarse dogrulanmamis bir arastirma sessizce skor gibi gosterilir.
    """
    o = citability.site_ozeti([_sayfa()])
    assert o["shadow"] is True


def test_veri_yoksa_None():
    assert citability.site_ozeti([]) is None
    assert citability.site_ozeti([{"headings": [], "paragraphs": []}]) is None
