"""Musteriye gorunen hata mesajlarinin dili (2026-07-30 denetimi #9).

Eskiden HTTPException detail'leri sabit metindi: Ingilizce arayuzdeki musteri
"Yetersiz token bakiyesi", Turkce arayuzdeki "Authentication required"
goruyordu. Artik hata KODLA firlatiliyor, metin istegin diline gore seciliyor.

Buradaki asil deger surukleme (drift) korumasi: main.py yeni bir kod
firlatirsa ama sozluge eklenmezse, musteriye ham anahtar ("gecersiz_paket")
gorunur — bu testler onu CI'da yakalar.
"""
import re
import pathlib

import pytest

import api_errors
from api_errors import (BILET_HATA_KODLARI, BILET_SUNUCU_HATALARI,
                        DESTEKLENEN_DILLER, MESAJLAR, ApiHata, coz_dil, mesaj)

KOK = pathlib.Path(__file__).resolve().parent.parent


# ── Sozluk butunlugu ────────────────────────────────────────────────────
def test_tum_kodlar_tum_dillerde_var():
    for kod, girdi in MESAJLAR.items():
        eksik = set(DESTEKLENEN_DILLER) - set(girdi)
        assert not eksik, f"{kod} icin eksik dil: {eksik}"


def test_hicbir_metin_bos_degil():
    for kod, girdi in MESAJLAR.items():
        for dil, metin in girdi.items():
            assert metin.strip(), f"{kod}/{dil} bos"


def test_tr_ve_en_metinleri_farkli():
    """Kopyala-yapistir sonucu ceviri UNUTULMUS olmasin."""
    ayni = [k for k, g in MESAJLAR.items() if g["tr"] == g["en"]]
    assert not ayni, f"TR ve EN metni ayni (ceviri unutulmus?): {ayni}"


# ── main.py ile surukleme korumasi ──────────────────────────────────────
def test_main_pydeki_tum_kodlar_sozlukte():
    kaynak = (KOK / "main.py").read_text(encoding="utf-8")
    kullanilan = set(re.findall(r'ApiHata\(\s*[^,]+,\s*"([a-z0-9_]+)"', kaynak))
    assert kullanilan, "main.py'de hic ApiHata kullanimi bulunamadi (regex bozuk?)"
    eksik = kullanilan - set(MESAJLAR)
    assert not eksik, f"main.py sozlukte olmayan kod firlatiyor: {eksik}"


def test_bilet_esleme_hedefleri_sozlukte():
    eksik = set(BILET_HATA_KODLARI.values()) - set(MESAJLAR)
    assert not eksik, eksik


def test_purchase_ticket_hata_kodlari_ele_alinmis():
    """db.purchase_ticket yeni bir hata kodu uretirse ya cevirisi olmali ya da
    bilerek 'sunucu hatasi' listesinde durmali."""
    kaynak = (KOK / "db.py").read_text(encoding="utf-8")
    i = kaynak.index("async def purchase_ticket")
    sonraki = re.search(r"\n(?:async )?def ", kaynak[i + 10:])
    govde = kaynak[i:i + 10 + sonraki.start()] if sonraki else kaynak[i:]
    uretilen = set(re.findall(r'"error": "([a-z_]+)"', govde))
    ele_alinan = set(BILET_HATA_KODLARI) | BILET_SUNUCU_HATALARI
    assert uretilen <= ele_alinan, f"ele alinmayan bilet hatasi: {uretilen - ele_alinan}"


# ── Dil cozumu ──────────────────────────────────────────────────────────
def test_x_lang_accept_language_uzerinde():
    """Kullanicinin UYGULAMADAKI tercihi tarayici dilinden onceliklidir:
    TR tarayicida EN arayuz kullanan musteri EN mesaj gormeli."""
    assert coz_dil("tr-TR,tr;q=0.9", "en") == "en"
    assert coz_dil("en-US,en;q=0.9", "tr") == "tr"


def test_accept_language_ayristirilir():
    assert coz_dil("en-US,en;q=0.9,tr;q=0.8") == "en"
    assert coz_dil("tr") == "tr"


def test_bilinmeyen_dil_turkceye_duser():
    assert coz_dil("de-DE,de;q=0.9") == "tr"
    assert coz_dil("", "fr") == "tr"
    assert coz_dil() == "tr"


def test_bos_x_lang_accept_languagei_engellemez():
    assert coz_dil("en-US", "") == "en"


# ── mesaj() ─────────────────────────────────────────────────────────────
def test_mesaj_dile_gore_secer():
    assert mesaj("gecersiz_paket", "tr") == MESAJLAR["gecersiz_paket"]["tr"]
    assert mesaj("gecersiz_paket", "en") == MESAJLAR["gecersiz_paket"]["en"]


def test_bilinmeyen_kod_kendini_doner():
    """Bos metin donmek hatayi GIZLERDI; kod gorunur kalsin."""
    assert mesaj("boyle_bir_kod_yok", "tr") == "boyle_bir_kod_yok"


def test_desteklenmeyen_dil_varsayilana_duser():
    assert mesaj("gecersiz_paket", "de") == MESAJLAR["gecersiz_paket"]["tr"]


# ── Handler entegrasyonu ────────────────────────────────────────────────
@pytest.fixture
def istemci():
    """main.py'nin handler'inin AYNISI: kod -> dile gore metin + X-Error-Code.

    fastapi GEREKIR. Deploy kapisi (.github/workflows) testleri BILEREK minimal
    ortamda kosuyor (pytest/httpx/cbor2/cryptography) -> bu dosyadaki entegrasyon
    testleri orada ATLANIR, yukaridaki surukleme korumalari KOSAR. Entegrasyon
    yerelde tam suite ile dogrulanir.
    """
    fastapi = pytest.importorskip("fastapi")
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from starlette.requests import Request

    FastAPI = fastapi.FastAPI
    app = FastAPI()

    @app.exception_handler(ApiHata)
    async def _h(request: Request, exc: ApiHata):
        dil = coz_dil(request.headers.get("accept-language", ""),
                      request.headers.get("x-lang", ""))
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.metin(dil)},
                            headers={"X-Error-Code": exc.kod})

    @app.get("/patla")
    async def patla():
        raise ApiHata(402, "yetersiz_bakiye")

    return TestClient(app, raise_server_exceptions=False)


def test_handler_dile_gore_metin_doner(istemci):
    r = istemci.get("/patla", headers={"X-Lang": "en"})
    assert r.status_code == 402
    assert r.json()["detail"] == MESAJLAR["yetersiz_bakiye"]["en"]

    r = istemci.get("/patla", headers={"X-Lang": "tr"})
    assert r.json()["detail"] == MESAJLAR["yetersiz_bakiye"]["tr"]


def test_handler_baslik_yoksa_turkce(istemci):
    r = istemci.get("/patla")
    assert r.json()["detail"] == MESAJLAR["yetersiz_bakiye"]["tr"]


def test_handler_kararli_kodu_baslikta_verir(istemci):
    """Istemci metne degil KODA gore dallanabilsin."""
    assert istemci.get("/patla").headers["X-Error-Code"] == "yetersiz_bakiye"


def test_detail_duz_metin_kalir(istemci):
    """Web istemcisi `data.detail` degerini dogrudan basiyor; sozluk donsek
    ekranda '[object Object]' gorunurdu."""
    assert isinstance(istemci.get("/patla").json()["detail"], str)


# ── Bilgi sizmasi ───────────────────────────────────────────────────────
def test_ic_hata_metni_istemciye_sizmiyor():
    """Eskiden `detail=f\"Audit failed: {job['error']}\"` ham istisna metnini
    istemciye tasiyordu."""
    kaynak = (KOK / "main.py").read_text(encoding="utf-8")
    assert 'detail=f"Audit failed' not in kaynak
    assert 'detail=f"Brand check failed' not in kaynak
