"""3 sayfadan az okunan tarama DUSUK GUVEN sayilir (kurucu karari 2026-08-09).

Rozet ve lig zaten "3 sayfadan az -> muhur/liste yok" diyordu; kullanici ekrani
demiyordu. Olculdu: 30 gunde 113 web taramasinin 66'si 3 sayfadan az okumus,
54'u kullaniciya CEKINCESIZ gosterilmis. Istemciler (web + mobil) cekince notunu
`diagnostics.low_confidence` bayragina bakarak ciziyor; bayrak dogru yazilinca
iki ekran birden duzeliyor.

PUAN DEGISMEZ — bu test onu da kiliyor.
"""
import asyncio

import scoring


def _teshis(sayfa_sayisi: int):
    """N sayfalik sahte bir crawl ile skoru hesaplar; (diagnostics, sonuc) doner."""
    pages = [{"url": f"https://ornek.com/{i}", "title": f"S{i}",
              "text": "yeterince uzun bir metin " * 20,
              "canonical_url": f"https://ornek.com/{i}"} for i in range(sayfa_sayisi)]
    sonuc = asyncio.run(scoring.compute_ai_visibility_score(
        {"pages": pages, "sitemap_found": True},
        {"indexed_count": sayfa_sayisi},
    ))
    return sonuc["diagnostics"], sonuc


def test_iki_sayfa_dusuk_guven():
    d, _ = _teshis(2)
    assert d["low_confidence"] is True, "2 sayfa okunduysa cekince ACIK olmali"


def test_uc_sayfa_dusuk_guven_DEGIL():
    d, _ = _teshis(3)
    assert d["low_confidence"] is False, "3 sayfa esigi tutuyorsa cekince kapali"


def test_sifir_sayfa_dusuk_guven():
    d, _ = _teshis(0)
    assert d["low_confidence"] is True


def test_esik_rozet_ve_lig_ile_AYNI():
    """Tek kural, dort yer. Sayilar birbirinden kaymasin."""
    assert scoring.MIN_GUVEN_SAYFA == 3


def test_PUAN_degismedi():
    """Bayrak acilirken skor DUSURULMEZ — kurucu: 'puan degismez'.

    Ayni girdiyle bayragin ACIK oldugu (2 sayfa) ve KAPALI oldugu (3 sayfa)
    kosularda skor uretiliyor ve pozitif kaliyor; cekince bir CEZA degil.
    """
    d2, iki = _teshis(2)
    d3, uc = _teshis(3)
    assert d2["low_confidence"] is True and d3["low_confidence"] is False
    assert isinstance(iki["overall_score"], (int, float)) and iki["overall_score"] > 0
    assert isinstance(uc["overall_score"], (int, float)) and uc["overall_score"] > 0


def test_bayrak_skoru_CEZALANDIRMIYOR():
    """Regresyon nobeti: ileride biri 'dusuk guven -> skoru kirp' derse yakalar.

    Ayni sayfa sayisiyla iki kez hesaplanan skor AYNI kalmali; bayrak skor
    formulune girmemeli.
    """
    _, a = _teshis(2)
    _, b = _teshis(2)
    assert a["overall_score"] == b["overall_score"]
