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


# ─────────────────────────────────────────────────────────────────────────────
# SAĞ KENAR PAYI (2026-08-08'de ÜRETİMDEKİ gerçek PNG üzerinde ölçüldü)
#
# `sx = W - 90 - sw` idi: 90 px'lik pay YALNIZ skora ayrılıyor, "/100" onun
# SAĞINA çiziliyordu. Canlı karttan piksel ölçümü:
#     sol kenar boşluğu 71 px  ·  sağ kenar boşluğu 7 px   → on kat asimetri
# Kırpılma yoktu ama kart X/LinkedIn/WhatsApp'ta paylaşılan yüz; kenara yapışık
# metin "bozuk" görüntüsü verir.
#
# 🪤 Pay artık metin genişliği ÖLÇÜLEREK ayrılıyor, sabit sayıyla değil — yazı
# tipi değişse de (üretim DejaVu, yerel fallback) boşluk korunur. Bu yüzden test
# de sabit piksel beklemez, SİMETRİ arar.
def test_sag_pay_metin_olculerek_ayriliyor():
    """Kaynak kapanı: sabit `W - 90 - sw` formülü geri gelmemeli.

    🪤 Yorum satırı eski formülü ALINTILIYOR (gerekçe orada yazılı), bu yüzden
    ham `re.search(KAYNAK)` yanlış alarm verir — yalnız KOD satırlarına bakılır."""
    kod = [ln for ln in KAYNAK.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(re.search(r"sx\s*=\s*W\s*-\s*90\s*-\s*sw", ln) for ln in kod)
    assert "suf_w = d.textlength(suffix, font=suf_font)" in KAYNAK
    assert "sx = W - SAG_PAY - suf_w - 6 - sw" in KAYNAK


def test_etiket_KARAKTERLE_degil_GENISLIKLE_kirpiliyor():
    """🪤 26 karakter ≠ 26 karakterlik genişlik ('WWWWW' vs 'iiiii'). Skor bloğu
    sola kayınca sabit karakter sınırı etiketi skorun üstüne bindirirdi."""
    assert not re.search(r"len\(lbl\)\s*>\s*26", KAYNAK)
    assert "kullanilabilir = max(120, sx - 70 - 24)" in KAYNAK


def _sag_bosluk(png_bytes):
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    W, _ = im.size
    px = im.load()
    bg = px[600, 200]
    fark = lambda c: sum(abs(a - b) for a, b in zip(c, bg)) > 30  # noqa: E731
    sag = 0
    for y in range(200, 420):
        for x in range(W - 1, W - 300, -1):
            if fark(px[x, y]):
                sag = max(sag, x)
                break
    return W - 1 - sag


def test_skor_blogu_KENARA_YAPISMIYOR(kart):
    """Her skor uzunluğunda sağ boşluk sol boşlukla (~70 px) kıyaslanabilir olmalı."""
    for s in (7, 73, 100):
        bosluk = _sag_bosluk(kart.render_score_card("geoni.ai", s))
        assert bosluk >= 50, f"skor {s}: sağ boşluk {bosluk}px — kenara yapışık"


def test_uzun_etiket_skorun_USTUNE_BINMIYOR(kart):
    """Uzun alan adı + 3 haneli skor: en sıkışık durum."""
    png = kart.render_score_card("cok-cok-uzun-bir-alan-adi-ornegi-burada.com.tr", 100)
    assert _sag_bosluk(png) >= 50
