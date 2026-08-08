"""Ücretsiz-hak kapısı `job_id`'yi TANIMLANMADAN kullanmasın.

CANLIDA YAŞANDI (2026-08-04 → 2026-08-08): `start_brand_check` ve
`start_social_check` içinde ücretsiz-hak kapısı

    _bekleyen_hak[job_id] = (...)

satırını, `job_id = str(uuid.uuid4())` atamasından **ÖNCE** çalıştırıyordu →
`UnboundLocalError: local variable 'job_id' referenced before assignment`.

Sonuç: **ücretsiz hakla yapılan HER marka/kişi ve sosyal tarama 500 döndü.**
Kullanıcı tarafında bu "Load failed" olarak görünüyor — hata mesajı bile yok.
Dört gün canlı kaldı; Sentry ilk kez 2026-08-08 07:25 UTC'de yakaladı, çünkü
o güne kadar bu yoldan geçen olmamıştı (`private` ya da premium taramalar
etkilenmiyordu — kapı yalnız ücretsiz hakta çalışıyor).

`start_audit` (web taraması) DOĞRUYDU: orada `job_id` kapıdan önce üretiliyor.
Yani hata "tek yerde unutuldu" değil, **aynı desenin iki kopyasında** vardı.

🪤 Bu test `main`i IMPORT ETMEZ (fastapi zinciri; CI'ın asgari ortamı toplama
aşamasında düşerdi). Kaynak okunarak, YORUM SATIRLARI ATLANARAK kilitlenir —
gerekçe yorumları örnek kodu birebir içeriyor ve naif arama yanlış alarm verir.
"""
import re
from pathlib import Path

SATIRLAR = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8").split("\n")


def _kullanimlar():
    """(fonksiyon, kullanim_satiri, son_atama_satiri) — yorumlar hariç."""
    fn = atama = None
    out = []
    for i, ln in enumerate(SATIRLAR, 1):
        if ln.startswith("async def ") or ln.startswith("def "):
            fn = ln.split("(")[0].split()[-1]
            atama = None
        if ln.lstrip().startswith("#"):
            continue
        if re.search(r"^\s*job_id\s*=\s*str\(uuid", ln):
            atama = i
        if "_bekleyen_hak[job_id]" in ln:
            out.append((fn, i, atama))
    return out


def test_her_kullanimda_job_id_ONCEDEN_tanimli():
    """🔴 Asıl kapan: atama kullanımdan SONRAYSA uç 500 döner."""
    hatali = [(fn, k, a) for fn, k, a in _kullanimlar() if not a or a >= k]
    assert not hatali, f"job_id tanımlanmadan kullanılıyor: {hatali}"


def test_UC_yolun_UCUNDE_de_kapi_var():
    """Web, marka/kişi ve sosyal — üçü de ücretsiz hak kapısından geçer.
    Biri listeden düşerse ya kapı kaldırılmıştır ya da isim değişmiştir."""
    fnler = {fn for fn, _, _ in _kullanimlar()}
    assert {"start_audit", "start_brand_check", "start_social_check"} <= fnler, fnler


def test_gerekce_kaynakta_YAZILI():
    """Silinirse biri 'bu atama neden burada' deyip aşağı taşır ve hata döner."""
    kaynak = "\n".join(SATIRLAR)
    assert "job_id BURADA uretilir" in kaynak
    assert "UnboundLocalError" in kaynak
