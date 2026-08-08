"""Mobil istemci Turnstile'a takılmasın — ÜÇ tarama ucunda da.

CANLIDA YAŞANDI (2026-08-08, kurucu ekran görüntüsüyle bildirdi): mobil
uygulamada **Sosyal** sekmesi

    "Doğrulama başarısız. Lütfen sayfayı yenileyip tekrar deneyin."

veriyordu. Uygulamada yenilenecek bir sayfa bile yok.

## Kök neden
Mobil uygulamada **Turnstile widget'ı YOK** → istek hiçbir zaman
`turnstile_token` taşımıyor. `turnstile.py` token'sız isteklere IP başına
**2** ("grace", `_NOTOKEN_GRACE`) hak tanıyor; o tükendikten sonra 403.

`/api/audit/quick` ve `/api/brand-check` bu yüzden **zaten**
`not await _mobile_exempt(http_request)` koşulunu taşıyordu; **sosyal uç
atlanmıştı.** Sonuç: mobilde Site/Kişi/Marka çalışırken Sosyal sekmesi kırıktı.

🪤 Test `main`i IMPORT ETMEZ (fastapi zinciri; CI'ın asgari ortamı toplama
aşamasında düşerdi). Kaynak okunarak kilitlenir.
"""
import re
from pathlib import Path

KAYNAK = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
SATIRLAR = KAYNAK.split("\n")


def _turnstile_kosullari():
    """Her `await enforce_turnstile(...)` çağrısının üstündeki `if` satırı."""
    out = []
    for i, ln in enumerate(SATIRLAR):
        if "await enforce_turnstile(" not in ln:
            continue
        uc = re.search(r'"([a-z-]+)"\s*\)\s*$', ln)
        # koşul, çağrının hemen üstündeki `if` satırı
        for j in range(i - 1, max(i - 6, 0), -1):
            if SATIRLAR[j].lstrip().startswith("if "):
                out.append((uc.group(1) if uc else f"satir{i+1}", SATIRLAR[j]))
                break
    return out


def test_UC_UCTA_DA_turnstile_var():
    """Muafiyet eklerken korumayı tamamen kaldırmadığımızı da kilitler."""
    uclar = {ad for ad, _ in _turnstile_kosullari()}
    assert uclar == {"audit", "brand-check", "social-check"}, uclar


def test_HER_UCTA_mobil_muafiyeti_VAR():
    """🔴 Asıl kapan: sosyal uçta bu koşul yoktu ve mobil sekme kırıktı."""
    eksik = [ad for ad, kosul in _turnstile_kosullari() if "_mobile_exempt" not in kosul]
    assert not eksik, f"mobil muafiyeti olmayan uç(lar): {eksik}"


def test_ic_tarama_muafiyeti_KORUNDU():
    for ad, kosul in _turnstile_kosullari():
        assert "_is_internal_scan" in kosul, ad


def test_gerekce_kaynakta_YAZILI():
    """Silinirse biri 'sosyalde neden muafiyet var' deyip kaldırır."""
    assert "MOBIL MUAFIYETI ZORUNLU" in KAYNAK
    assert "_NOTOKEN_GRACE" in KAYNAK
