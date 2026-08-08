"""`job_id` her uc tarama ucunda KOSULSUZ atanmali — AST ile kilitli.

Ayni kusur 2026-08-08'de IKI KEZ yakalandi:

1. Sabah: atama ucretsiz-hak kapisinin ALTINDAydi. Kapiyi gecen (ucretsiz hakli)
   her marka/sosyal tarama UnboundLocalError -> 500. 4 gun canlida kaldi,
   tarayicida "Load failed" goruluyordu, kurucu kendi kullanirken buldu.
2. Aksam: birinci duzeltmede atamayi kapinin USTUNE aldim ama YINE
   `if not _is_internal_scan(...)` blogunun ICINDE biraktim. Bu sefer IC TARAMA
   yolu (izleme / lig / dogrulama) 500 dondu. Hata kullaniciya degil BIZE
   vurdugu icin sessizdi — @starbucks'i yeniden tarayarak dogrulamaya
   calistigimda ortaya cikti.

🔴 pyflakes IKISINI DE YAKALAMADI: kosullu atama sozdizimsel olarak gecerli,
   "referenced before assignment" ancak calisma zamaninda patliyor. lint kapisi
   (scripts/lint-kapisi.sh) bu sinifi goremez — bu yuzden YAPISAL test gerekli.

Kural: `job_id = ...` fonksiyon govdesinin EN UST seviyesinde olacak; hicbir
`if`/`try`/`for` icinde olmayacak. Boylece hangi dal secilirse secilsin tanimli.
"""
import ast
from pathlib import Path

KAYNAK = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
AGAC = ast.parse(KAYNAK)

UCLAR = ("start_audit", "start_brand_check", "start_social_check")


def _fonksiyonlar():
    bulunan = {}
    for d in ast.walk(AGAC):
        if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.name in UCLAR:
            bulunan[d.name] = d
    return bulunan


def _ust_seviye_atiyor_mu(fn) -> bool:
    """Yalnizca govdenin DOGRUDAN cocuklarina bak — ic bloklar sayilmaz."""
    for st in fn.body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name) and t.id == "job_id":
                    return True
    return False


def test_uc_ucun_UCU_de_bulundu():
    """Fonksiyon yeniden adlandirilirsa test sessizce gecmesin."""
    eksik = set(UCLAR) - set(_fonksiyonlar())
    assert not eksik, f"bu uc fonksiyonlari main.py'de bulunamadi: {eksik}"


def test_job_id_KOSULSUZ_atanir():
    """🔴 Asil kapan: atama bir `if` icindeyse diger dal 500 doner."""
    kotu = [ad for ad, fn in _fonksiyonlar().items() if not _ust_seviye_atiyor_mu(fn)]
    assert not kotu, (
        f"job_id ust seviyede atanmiyor: {kotu} — kosullu atama UnboundLocalError uretir"
    )


def test_ilk_kullanimdan_ONCE_atanir():
    """Ust seviyede olmasi yetmez; kullanimdan sonra gelirse yine patlar."""
    for ad, fn in _fonksiyonlar().items():
        atama = min(
            (st.lineno for st in fn.body if isinstance(st, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "job_id" for t in st.targets)),
            default=None,
        )
        assert atama is not None, ad
        kullanim = min(
            (n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Name) and n.id == "job_id" and isinstance(n.ctx, ast.Load)),
            default=None,
        )
        if kullanim is not None:
            assert atama <= kullanim, f"{ad}: job_id {kullanim}. satirda {atama} atamasindan ONCE kullaniliyor"


def test_gerekce_kaynakta_YAZILI():
    """Silinirse biri 'bu atama neden burada' deyip kapinin icine geri koyar."""
    assert "job_id KOSULUN DISINDA uretilir" in KAYNAK
