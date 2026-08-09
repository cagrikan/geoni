"""Kismi olcum cekincesi KART ve E-POSTA yuzeylerinde de var (kurucu 2026-08-09).

Ayni butunluk kurali once dort yerde farkli davraniyordu: rozet ve lig 3 sayfa
altini eliyordu, kullanici ekrani/kart/e-posta elemiyordu. Ekran 275edec ile
duzeldi; bu test kalan iki yuzeyi kilitler.

Kurucu karari: kart URETILSIN ama NOTLU (kesmek paylasimi oldururdu),
e-postaya cekince cumlesi EKLENSIN.
"""
import card
import mailer


def _kart(kismi: bool) -> bytes:
    return card.render_score_card("ornek.com", 71, "web", "tr", kismi)


def test_esik_rozet_lig_ve_skorla_AYNI():
    assert card.MIN_TAM_OLCUM_SAYFA == 3


def test_kart_kismi_isaretle_FARKLI_ciziliyor():
    """Ibare gercekten piksele donuyor mu — ayni girdi, iki farkli PNG."""
    assert _kart(True) != _kart(False)


def test_kart_kismi_olsa_da_URETILIYOR():
    """Kurucu 'kart uretilsin ama notlu' dedi; kesilmedigi kilitleniyor."""
    png = _kart(True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000


def test_eposta_cekince_YAZIYOR():
    html = mailer._build_report_html("ornek.com", {"score": 71, "domain": "ornek.com", "breakdown": {},
         "diagnostics": {"low_confidence": True}}, lang="tr")
    assert "Bu ölçüm kısmi" in html


def test_eposta_saglam_taramada_SUSUYOR():
    """Gurultu yapmiyoruz: guven dusuk degilse cekince cikmaz."""
    html = mailer._build_report_html("ornek.com", {"score": 71, "domain": "ornek.com", "breakdown": {},
         "diagnostics": {"low_confidence": False}}, lang="tr")
    assert "Bu ölçüm kısmi" not in html


def test_eposta_ingilizce_de_YAZIYOR():
    html = mailer._build_report_html("ornek.com", {"score": 71, "domain": "ornek.com", "breakdown": {},
         "diagnostics": {"low_confidence": True}}, lang="en")
    assert "partial measurement" in html.lower()
