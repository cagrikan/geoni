"""Hedef (ad/alan adi) eslesmesi buyuk-kucuk harf ve bosluk duyarsiz mi?

2026-07-30 olcumu: `get_previous_audits` `eq.` ile BIREBIR esliyordu. Uretimde
tek kisi 6 FARKLI yazimla 37 tarama uretmisti ("Sabri Çağrı Çakır",
"Sabri çağrı çakır", "SAbri Çağrı Çakır", sondaki bosluklu hali...). Sonuc:
skor gecmisi parcalaniyor (kullanici "onceki taramam nerede" diyor) ve
score_stability yanlis/eksik referansa gore hesaplaniyor -- 07-27..07-30
arasi metrigin 17.9'da "plato" yapmasinin bir bileseni buydu.
"""
import asyncio

import db


class _Yanit:
    def __init__(self, veri, kod=200):
        self._veri, self.status_code = veri, kod

    def json(self):
        return self._veri


class _Istemci:
    def __init__(self, satirlar):
        self.satirlar = satirlar
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, params=None, timeout=None):
        self.params = params
        return _Yanit(self.satirlar)


def _kur(monkeypatch, satirlar):
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    monkeypatch.setattr(db, "_headers", lambda: {}, raising=False)
    ist = _Istemci(satirlar)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: ist)
    return ist


def test_anahtar_yazim_farklarini_siler():
    """Uretimde GORULEN 6 varyantin hepsi ayni anahtara dusmeli."""
    a = db.hedef_anahtari("Sabri Çağrı Çakır")
    for varyant in ("sabri çağrı çakır", "Sabri çağrı çakır", "SAbri Çağrı Çakır",
                    "Sabri çağrı Çakır ", "Sabri Çağrı Çakır ", " Sabri  Çağrı  Çakır "):
        assert db.hedef_anahtari(varyant) == a, varyant


def test_BILINEN_SINIR_turkce_noktasiz_I():
    """DURUST SINIR: casefold() Turkce yerelini bilmez — "ÇAĞRI" (noktasiz I)
    "çağri" olur, "Çağrı" ise "çağrı". Yani TAMAMI BUYUK harfle yazilmis Turkce
    ad, normal yazimla eslesmez.

    Neden boyle birakildi: alternatif I/ı/İ/i'yi tek harfe indirmekti; o da
    "Kıran" ile "Kiran"i AYNI kisi yapardi — yani FARKLI insanlarin gecmisini
    birlestirirdi. Bolme hatasi (gecmis kopar) birlestirme hatasindan (baskasinin
    skoru sana yazilir) daha ucuz. Uretimde gorulen 6 varyantin hicbiri
    tamami-buyuk-harf degildi.

    Bu test kusuru DOGRULAMIYOR, DAVRANISI SABITLIYOR: biri I/ı katlamasi
    eklerse burasi kirilir ve karar bilincli olarak yeniden verilir."""
    assert db.hedef_anahtari("ÇAĞRI") != db.hedef_anahtari("Çağrı")
    assert db.hedef_anahtari("Kıran") != db.hedef_anahtari("Kiran")


def test_farkli_kisiler_KARISMAZ():
    assert db.hedef_anahtari("Ali Veli") != db.hedef_anahtari("Ali Velı")
    assert db.hedef_anahtari("Geoni") != db.hedef_anahtari("Geoni AI")


def test_onceki_tarama_farkli_yazimda_da_BULUNUR(monkeypatch):
    """Asil kusurun testi: gecmis yazim farkiyla kopmamali.

    IKI SEYI birden sabitler — sahte istemci DB filtresini taklit etmedigi icin
    yalniz donen satirlara bakmak YETMEZ (eq.'ye geri donulse de gecerdi):
      1) uretilen PostgREST filtresi harf-duyarsiz (`ilike.`) olmali,
      2) Python tarafi satirlari dogru eslestirmeli."""
    ist = _kur(monkeypatch, [
        {"name": "Sabri çağrı çakır ", "score": 44, "result_json": {"score_breakdown": {"a": 1}}},
        {"name": "SAbri Çağrı Çakır", "score": 39, "result_json": {"score_breakdown": {"a": 2}}},
    ])
    onceki = asyncio.run(db.get_previous_audits("person", "Sabri Çağrı Çakır", limit=2))
    assert [p["score"] for p in onceki] == [44, 39]
    assert ist.params["name"].startswith("ilike."), \
        f"harf-duyarli eslesmeye geri donulmus: {ist.params['name']}"


def test_ilike_genis_eslesirse_PYTHON_ELER(monkeypatch):
    """ilike beklenmedik sekilde genis eslesirse ilgisiz kayit GECMEMELI."""
    _kur(monkeypatch, [
        {"name": "Baska Biri", "score": 90, "result_json": {}},
        {"name": "sabri çağrı çakır", "score": 44, "result_json": {}},
    ])
    onceki = asyncio.run(db.get_previous_audits("person", "Sabri Çağrı Çakır", limit=2))
    assert [p["score"] for p in onceki] == [44], "ilgisiz hedef gecmise karisti"


def test_joker_karakter_HERKESLE_eslesemez(monkeypatch):
    """'%' adiyla tarama yapan biri TUM kayitlarla eslesmemeli."""
    ist = _kur(monkeypatch, [{"name": "Baska Biri", "score": 90, "result_json": {}}])
    onceki = asyncio.run(db.get_previous_audits("person", "%", limit=2))
    assert onceki == []
    assert "\\%" in ist.params["name"], "joker karakter kacirilmamis"


def test_alan_adi_domain_kolonundan_bakar(monkeypatch):
    ist = _kur(monkeypatch, [{"domain": "geoni.ai", "score": 59, "result_json": {}}])
    onceki = asyncio.run(db.get_previous_audits("web", "GEONI.ai", limit=2))
    assert [p["score"] for p in onceki] == [59]
    assert "domain" in ist.params and "name" not in ist.params


def test_bos_hedef_sorgu_ACMAZ(monkeypatch):
    ist = _kur(monkeypatch, [])
    assert asyncio.run(db.get_previous_audits("person", "   ", limit=2)) == []
    assert ist.params is None, "bos hedefle DB'ye gidildi"
