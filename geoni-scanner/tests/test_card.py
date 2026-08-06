"""Paylaşım kartı: renk eşiği istemcilerle AYNI, skor sınırları güvenli.

NE OLDU (2026-08-07'de ölçüldü): `card.py` skor rengini **70** eşiğiyle
çiziyordu; web (`lib/skor.js`) ve mobil (`lib/skor.ts`) ise **65**. Sonuç: 65-69
arası bir kullanıcı uygulamada YEŞİL görüyor, X/LinkedIn/WhatsApp'ta paylaştığı
kartta AMBER çıkıyordu — yani dışarıya gösterdiği görsel, kendi gördüğünden
daha kötüydü. Kart tam da "statü nesnesi" olsun diye var.

Tamamlanan 437 taramanın **45'i (%10,3)** bu aralığa düşüyordu.

Web ve mobil 2026-08-04 kör denetiminde 65'te birleştirilmişti; kart o denetimde
atlanmıştı. Bu test üç istemcinin ayrışmasını bir daha sessiz bırakmaz.
"""
import card


def test_esik_ISTEMCILERLE_ayni():
    """🪤 Bu sayı değişirse web/mobil de değişmeli. Tek başına değiştirilirse
    paylaşılan kart uygulamayla çelişir."""
    assert card.SKOR_IYI == 65
    assert card.SKOR_ORTA == 40


def test_65_YESIL_69_da_yesil():
    yesil = (63, 185, 132)
    assert card._score_color(65) == yesil
    assert card._score_color(67) == yesil
    assert card._score_color(69) == yesil
    assert card._score_color(100) == yesil


def test_64_amber_40_amber():
    amber = (245, 166, 35)
    assert card._score_color(64) == amber
    assert card._score_color(40) == amber


def test_39_kirmizi():
    assert card._score_color(39) == (240, 97, 109)
    assert card._score_color(0) == (240, 97, 109)


def test_kart_uretiliyor_ve_PNG():
    png = card.render_score_card("geoni.ai", 67)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_skor_SINIRLARI_kirpiliyor():
    """🪤 Bozuk/aşırı skor kartı patlatmamalı ve çubuğu taşırmamalı."""
    for s in (-50, 0, 100, 250, 99.6):
        png = card.render_score_card("örnek.com", s)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_uzun_etiket_kirpiliyor():
    """Uzun alan adı sağdaki skorun üstüne binmemeli — kırpma davranışı var."""
    png = card.render_score_card("cok-cok-cok-uzun-bir-alan-adi-ornegi.com.tr", 71)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_bos_etiket_patlatmaz():
    assert card.render_score_card("", 50)[:8] == b"\x89PNG\r\n\x1a\n"
    assert card.render_score_card(None, 50)[:8] == b"\x89PNG\r\n\x1a\n"


def test_ingilizce_kart_da_uretiliyor():
    assert card.render_score_card("example.com", 80, lang="en")[:8] == b"\x89PNG\r\n\x1a\n"
