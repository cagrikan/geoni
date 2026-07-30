"""starlette muafiyetinin KOSULLARINI koruyan testler.

requirements.txt'te starlette 0.41.3'e pinli ve iki CVE'den (BADHOST /
multipart DoS) muaf sayiliyor. Muafiyet SURUMDEN degil, KODUN SEKLINDEN
geliyor: (a) dosya yukleme ucu yok, (b) `request.url`den guvenlik karari
verilmiyor. Bu iki varsayim bir gun sessizce bozulursa pin, korumadigi bir
seyi koruyor gibi gorunur. Testler o ani commit aninda yakalar.
(Olcum + gerekce: 2026-07-30, memory/geoni-saglayici-kesinti-uyarisi yaninda.)
"""
import pathlib
import re

KOK = pathlib.Path(__file__).resolve().parent.parent


def _uygulama_kaynaklari():
    """Uygulama .py dosyalari — testler ve yardimci betikler haric."""
    for yol in KOK.glob("*.py"):
        yield yol, yol.read_text(encoding="utf-8")


def _kod_satirlari(metin: str):
    """Yorum ve docstring gurultusunu kabaca ele: yalniz kod tarafina bak."""
    for satir in metin.splitlines():
        govde = satir.split("#", 1)[0]
        if govde.strip():
            yield govde


def test_starlette_pinli():
    req = (KOK / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^starlette==", req, re.M), \
        "starlette pinsiz — her derleme farkli surum cekebilir, guvenlik yuzeyi kayar"


def test_dosya_yukleme_ucu_yok():
    """CVE-2025-54121 muafiyetinin kosulu: multipart form parse edilmiyor."""
    bulunan = []
    for yol, metin in _uygulama_kaynaklari():
        for satir in _kod_satirlari(metin):
            if re.search(r"\bUploadFile\b|\bFile\s*\(|\bForm\s*\(", satir):
                bulunan.append(f"{yol.name}: {satir.strip()}")
    assert not bulunan, (
        "Dosya yukleme/multipart ucu eklenmis — starlette muafiyeti DUSTU, "
        ">=0.47.2'ye cikilmali:\n" + "\n".join(bulunan)
    )


def test_request_url_uzerinden_guvenlik_karari_yok():
    """CVE-2026-48710 (BADHOST) muafiyetinin kosulu: request.url / Host
    basligi guvenlik/yonlendirme karari icin okunmuyor."""
    bulunan = []
    for yol, metin in _uygulama_kaynaklari():
        for satir in _kod_satirlari(metin):
            if re.search(r"request\.url|TrustedHostMiddleware", satir):
                bulunan.append(f"{yol.name}: {satir.strip()}")
            if re.search(r"headers\s*\.\s*get\s*\(\s*[\"']host[\"']", satir, re.I):
                bulunan.append(f"{yol.name}: {satir.strip()}")
    assert not bulunan, (
        "request.url/Host guvenlik kararina girmis — BADHOST muafiyeti DUSTU, "
        "starlette >=1.0.1 gerekir (fastapi major yukseltmesi, kurucu onayi):\n"
        + "\n".join(bulunan)
    )


# ── Deploy kapisinin ORTAMINI koruyan test ──────────────────────────────
# .github/workflows deploy kapisi testleri BILEREK minimal ortamda kosuyor:
#   pip install pytest httpx cbor2 cryptography     (fastapi/pydantic YOK)
# Bir test modulu MODUL SEVIYESINDE fastapi'yi (ya da onu ceken main.py'yi)
# import ederse pytest COLLECTION'da patlar ve TUM suite duser -> deploy bloke.
# 2026-07-30'da tam bu oldu: uc yeni test modulu `main`i import etti, deploy
# 22 saniyede dustu. Yerelde fark edilmedi cunku yerel venv'de fastapi vardi.
# Gereken yerde `pytest.importorskip("fastapi")` ile FONKSIYON ICINDE import et.
_AGIR_MODULLER = ("fastapi", "pydantic", "starlette", "main")


def test_test_modulleri_agir_bagimliligi_modul_seviyesinde_import_etmiyor():
    ihlal = []
    for yol in sorted((KOK / "tests").glob("test_*.py")):
        for no, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^(?:import|from)\s+([A-Za-z_][\w.]*)", satir)
            if m and m.group(1).split(".")[0] in _AGIR_MODULLER:
                ihlal.append(f"{yol.name}:{no}: {satir.strip()}")
    assert not ihlal, (
        "Bu import'lar deploy kapisini kirar (minimal ortamda fastapi yok):\n  "
        + "\n  ".join(ihlal)
        + "\nCozum: saf mantigi hafif bir module tasi (or. brand_progress.py) ya da"
          " testin ICINDE pytest.importorskip('fastapi') kullan."
    )
