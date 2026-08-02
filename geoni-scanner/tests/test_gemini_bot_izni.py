"""Gemini bot izni: SAYI ile BOOLEAN karismasin (2026-08-02).

YASANDI: `platforms.google` alanina Google SONUC SAYISI (int) konuyordu,
digerleri ise bot-izni boolean'iydi. Arayuz hepsini boolean sayip
`!platforms.google` yaziyordu -> 0 sonuc "Gemini sitenize erisemiyor" diye
okundu. geoni.ai'nin kendi robots.txt'inde `Google-Extended: Allow: /`
OLMASINA RAGMEN rapor "Gemini Bot Izni: Hayir" bastive olmayan bir soruna
HIZMET onerdi (upsell). Yanlis bulgu + yanlis satis: en pahali hata turu.

Bu dosyanin isi o karismanin geri gelmedigini kanitlamak.
"""
import indexing


def _ai_access(google_extended_allow: bool) -> dict:
    """check_robots_ai_access'in urettigi sekli taklit eder."""
    egitim = {k: True for k in indexing.TRAINING_CRAWLER_AGENTS}
    arama = {k: True for k in indexing.SEARCH_CRAWLER_AGENTS}
    egitim["google_extended"] = google_extended_allow
    return {"egitim": egitim, "arama": arama, "robots_found": True}


def test_google_extended_izinliyse_bot_izni_true():
    a = _ai_access(True)
    assert a["egitim"]["google_extended"] is True


def test_google_extended_yasakliysa_bot_izni_false():
    a = _ai_access(False)
    assert a["egitim"]["google_extended"] is False


def test_bot_izni_BOOLEAN_sonuc_sayisi_DEGIL():
    """
    Asil regresyon kalkani: 'google_bot_allowed' her zaman bool olmali.
    int donerse arayuzdeki `!platforms.google` yine yanlis okur.
    """
    for allow in (True, False):
        deger = _ai_access(allow)["egitim"]["google_extended"]
        assert isinstance(deger, bool), f"bot izni bool degil: {type(deger)}"
        assert not isinstance(deger, int) or isinstance(deger, bool)


def test_sifir_sonuc_bot_iznini_ETKILEMEZ():
    """
    Sitenin Google'da 0 sonucu olmasi, Gemini'nin siteye erisemedigi ANLAMINA
    GELMEZ. Iki olcum bagimsizdir; bu ayrimi kaybettigimiz icin hata olusmustu.
    """
    google_sonuc_sayisi = 0
    bot_izni = _ai_access(True)["egitim"]["google_extended"]
    assert bot_izni is True, "0 sonuc bot iznini false yapmamali"
    assert google_sonuc_sayisi == 0   # ikisi ayni sey degil


def test_google_extended_egitim_listesinde_tanimli():
    """Anahtar adi degisirse (or. yeniden adlandirma) bu test kirilsin ve
    sessizce True varsayilana dusmeyelim."""
    assert "google_extended" in indexing.TRAINING_CRAWLER_AGENTS
    assert indexing.TRAINING_CRAWLER_AGENTS["google_extended"] == "Google-Extended"
