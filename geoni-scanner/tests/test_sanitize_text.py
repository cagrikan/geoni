"""_sanitize_text — KELIME SINIRINDA kesme.

2026-07-26 denetimi: canli bilet #61'de musteriye teslim edilen JSON-LD'nin
knowsAbout dizisinde "...markalar yapay z" diye YARIM KELIME vardi ve bu blok
musterinin sitesinin <head>'ine yapistiriliyordu. Fonksiyon 28 yerden cagriliyor.
"""
from ticket_automation import _sanitize_text


def test_yarim_kelime_birakmaz():
    m = "Generative Engine Optimization GEO nedir ve markalar yapay zeka aramalarinda"
    for n in range(20, len(m)):
        c = _sanitize_text(m, n)
        assert len(c) <= n
        if c and len(m) > n:
            # Kesilen metnin SON kelimesi, kaynaktaki tam bir kelime olmali
            assert c.split()[-1] in m.split(), (n, repr(c))


def test_gercek_vaka_bilet61():
    """Canli hatanin birebir tekrari: eskiden '... yapay z' donuyordu."""
    m = "Generative Engine Optimization GEO nedir ve markalar yapay zeka"
    c = _sanitize_text(m, 60)
    assert not c.endswith(" z")
    assert c == "Generative Engine Optimization GEO nedir ve markalar yapay"


def test_sinira_esit_ve_kisa_metin_degismez():
    assert _sanitize_text("kisa metin", 100) == "kisa metin"
    assert _sanitize_text("tam", 3) == "tam"


def test_tek_uzun_kelime_sert_kesilir():
    """Bosluk yoksa/cok baslardaysa neredeyse bos metin dondurme — sert kes."""
    c = _sanitize_text("a" * 100, 30)
    assert len(c) == 30


def test_asili_noktalama_temizlenir():
    c = _sanitize_text("bir iki uc dort, bes alti", 18)
    assert not c.endswith((",", " ", "-", ";"))


def test_kontrol_karakteri_temizligi_korunuyor():
    """Onceki davranis (B-7 guvenlik temizligi) BOZULMAMALI."""
    c = _sanitize_text("sat\nir\tve [koseli] (parantez) <etiket> `kod`", 200)
    assert "\n" not in c and "\t" not in c
    for ch in "[]()<>`#|{}":
        assert ch not in c


def test_bos_ve_none():
    assert _sanitize_text("", 10) == ""
    assert _sanitize_text(None, 10) == ""
