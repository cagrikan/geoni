"""Paylaşım kartı: renk eşiği istemcilerle AYNI, skor sınırları güvenli.

NE OLDU (2026-08-07'de ölçüldü): `card.py` skor rengini **70** eşiğiyle
çiziyordu; web (`lib/skor.js`) ve mobil (`lib/skor.ts`) ise **65**. Sonuç: 65-69
arası bir kullanıcı uygulamada YEŞİL görüyor, X/LinkedIn/WhatsApp'ta paylaştığı
kartta AMBER çıkıyordu — dışarıya gösterdiği görsel kendi gördüğünden daha
kötüydü. Kart tam da "statü nesnesi" olsun diye var.

Tamamlanan 437 taramanın **45'i (%10,3)** bu aralığa düşüyordu.

🪤 CI ORTAMI: deploy kapısı yalnız `pytest httpx cbor2 cryptography` kuruyor —
**Pillow YOK**. `card` modülünü import eden bir test CI'da toplama aşamasında
`ModuleNotFoundError: PIL` ile düşer (yaşandı, dağıtım kırıldı). Bu yüzden:
  * eşik testi `card.py`'yi DOSYA olarak okur → her ortamda koşar,
  * çizim testleri `importorskip` ile korunur → Pillow varsa koşar.
Eşik testi kritik olan; o asla atlanmamalı.
"""
import re
from pathlib import Path

import pytest

KAYNAK = (Path(__file__).resolve().parent.parent / "card.py").read_text(encoding="utf-8")


def _sabit(ad: str) -> int:
    m = re.search(rf"^{ad}\s*=\s*(\d+)", KAYNAK, re.M)
    assert m, f"{ad} sabiti card.py'de yok"
    return int(m.group(1))


def test_esik_ISTEMCILERLE_ayni():
    """🪤 Bu sayı değişirse web (lib/skor.js) ve mobil (lib/skor.ts) de
    değişmeli. Tek başına değiştirilirse paylaşılan kart uygulamayla çelişir.
    Import gerektirmez — CI'ın asgari ortamında da koşar."""
    assert _sabit("SKOR_IYI") == 65
    assert _sabit("SKOR_ORTA") == 40


def test_esikler_koda_GOMULU_kalmamis():
    """Eski hâlde `if score >= 70:` gövdeye gömülüydü. Sabite bağlanmazsa
    ileride yine sessizce ayrışır."""
    assert "score >= SKOR_IYI" in KAYNAK
    assert "score >= SKOR_ORTA" in KAYNAK
    assert not re.search(r"score >= 70\b", KAYNAK)


# ── Çizim testleri: Pillow gerekiyor (CI'da yok) ─────────────────────────────

@pytest.fixture(scope="module")
def kart():
    pytest.importorskip("PIL", reason="Pillow CI deploy kapisinda kurulu degil")
    import card
    return card


def test_65_YESIL_69_da_yesil(kart):
    yesil = (63, 185, 132)
    assert kart._score_color(65) == yesil
    assert kart._score_color(67) == yesil
    assert kart._score_color(69) == yesil
    assert kart._score_color(100) == yesil


def test_64_amber_40_amber(kart):
    amber = (245, 166, 35)
    assert kart._score_color(64) == amber
    assert kart._score_color(40) == amber


def test_39_kirmizi(kart):
    assert kart._score_color(39) == (240, 97, 109)
    assert kart._score_color(0) == (240, 97, 109)


def test_kart_uretiliyor_ve_PNG(kart):
    png = kart.render_score_card("geoni.ai", 67)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_skor_SINIRLARI_kirpiliyor(kart):
    """🪤 Bozuk/aşırı skor kartı patlatmamalı ve çubuğu taşırmamalı."""
    for s in (-50, 0, 100, 250, 99.6):
        assert kart.render_score_card("örnek.com", s)[:8] == b"\x89PNG\r\n\x1a\n"


def test_uzun_ve_bos_etiket_patlatmaz(kart):
    assert kart.render_score_card("cok-cok-uzun-bir-alan-adi-ornegi.com.tr", 71)[:8] == b"\x89PNG\r\n\x1a\n"
    assert kart.render_score_card("", 50)[:8] == b"\x89PNG\r\n\x1a\n"
    assert kart.render_score_card(None, 50)[:8] == b"\x89PNG\r\n\x1a\n"


def test_ingilizce_kart_da_uretiliyor(kart):
    assert kart.render_score_card("example.com", 80, lang="en")[:8] == b"\x89PNG\r\n\x1a\n"
