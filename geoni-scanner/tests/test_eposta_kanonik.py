"""Y1 (güvenlik denetimi 2026-08-12): Gmail nokta/`+` varyantları TEK kova.

NE OLDU: e-posta normalizasyonu yalnız `strip().lower()` yapıyordu. Gmail
noktaları ve `+etiket`i yok sayar — `u.s.e.r@gmail.com`, `user+1@gmail.com`,
`user+2@gmail.com` hepsi AYNI posta kutusu ama rate-limit'te AYRI kova
sayılıyordu; tek Gmail hesabı varyant başına sıfırdan sayaçla sınırsız istek
üretebiliyordu (kara liste de tek noktayla aşılıyordu — CLAUDE.md'deki açık
bot-savunması maddesi).

KURALLAR (kanonik_eposta):
  - `+etiket` HER sağlayıcıda atılır (alt-adres uzantısı).
  - Noktalar YALNIZ gmail.com/googlemail.com yerel kısmında silinir — başka
    sağlayıcıda `a.b` ile `ab` FARKLI kutular olabilir.
  - googlemail.com -> gmail.com.
  - Yalnız KARŞILAŞTIRMA anahtarı: DB kayıtlarına ve gönderim adresine dokunmaz.

`ratelimit` fastapi çekmez; testler her ortamda koşar.
"""
import pytest

import ratelimit
from ratelimit import (InMemoryRateLimiter, RateLimitExceeded,
                       enforce_audit_rate_limits, kanonik_eposta)


# ── Birim: kanonikleştirme kuralları ────────────────────────────────────────

def test_gmail_noktalari_silinir():
    assert kanonik_eposta("u.s.e.r@gmail.com") == "user@gmail.com"


def test_arti_etiketi_her_saglayicida_atilir():
    assert kanonik_eposta("user+spam@gmail.com") == "user@gmail.com"
    assert kanonik_eposta("user+1@outlook.com") == "user@outlook.com"


def test_googlemail_gmail_ayni_kutu():
    assert kanonik_eposta("U.ser+x@googlemail.com") == "user@gmail.com"


def test_gmail_disinda_noktalar_KORUNUR():
    """🪤 Başka sağlayıcıda nokta farklı kutu olabilir; silmek iki meşru
    kullanıcıyı aynı kovaya kilitlerdi."""
    assert kanonik_eposta("a.b@outlook.com") == "a.b@outlook.com"
    assert kanonik_eposta("a.b@firma.com.tr") == "a.b@firma.com.tr"


def test_bozuk_girdi_patlamaz():
    # brand-check ucu e-posta yerine user_id geçiyor (T3) — '@' yoksa aynen döner.
    assert kanonik_eposta("  USER-ID-123  ") == "user-id-123"
    assert kanonik_eposta("") == ""
    assert kanonik_eposta("+x@gmail.com") == "@gmail.com"  # boş yerel: yine tek kova


# ── Davranışsal: varyantlar rate-limit'te tek kovada sayılır ────────────────

def _taze_limiter(monkeypatch):
    monkeypatch.setattr(ratelimit, "RATE_LIMIT_STORE", InMemoryRateLimiter())
    monkeypatch.setattr(ratelimit, "_redis_limiter", None)


def test_gmail_varyantlari_TEK_kovada_sayilir(monkeypatch):
    """🔴 Asıl dava: EMAIL_LIMIT varyantla aşılamaz. skip_ip=True (IP kovası
    ayrı konu), domain her çağrıda farklı (o kova dolmasın)."""
    _taze_limiter(monkeypatch)
    varyantlar = ["user@gmail.com", "u.ser@gmail.com", "us.er+1@gmail.com",
                  "user+2@gmail.com", "u.s.e.r+xyz@googlemail.com"]
    assert ratelimit.EMAIL_LIMIT == 5, "limit değişti — senaryoyu güncelle"
    for i, e in enumerate(varyantlar):
        enforce_audit_rate_limits("1.2.3.4", e, f"site{i}.com", skip_ip=True)
    with pytest.raises(RateLimitExceeded):
        enforce_audit_rate_limits("1.2.3.4", "user+son@gmail.com",
                                  "site-son.com", skip_ip=True)


def test_farkli_gmail_hesaplari_birbirini_ENGELLEMEZ(monkeypatch):
    """Kanonikleştirme aşırıya kaçmasın: gerçekten farklı kutular ayrı kova."""
    _taze_limiter(monkeypatch)
    for i in range(ratelimit.EMAIL_LIMIT):
        enforce_audit_rate_limits("1.2.3.4", "ayse@gmail.com", f"a{i}.com",
                                  skip_ip=True)
    # ayse dolu ama fatma etkilenmez
    enforce_audit_rate_limits("1.2.3.4", "fatma@gmail.com", "b.com", skip_ip=True)


def test_outlook_noktali_hesaplar_ayri_kova(monkeypatch):
    _taze_limiter(monkeypatch)
    for i in range(ratelimit.EMAIL_LIMIT):
        enforce_audit_rate_limits("1.2.3.4", "a.b@outlook.com", f"c{i}.com",
                                  skip_ip=True)
    enforce_audit_rate_limits("1.2.3.4", "ab@outlook.com", "d.com", skip_ip=True)
