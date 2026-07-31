"""Promosyon kodu kullanimi (2026-07-31).

Tek kullanimlik kodlar hediye token verir. Riskli kisimlar:
  1. Es zamanli iki istek AYNI kodu iki kez odememeli.
  2. Kod "yanip" token yatmamasi (musteri kodunu kaybeder) olmamali.
  3. Kod ASLA yanita/loga sizmamali.
`main` import EDILMEZ (deploy kapisi minimal ortamda kosuyor).
"""
import asyncio
import json

import pytest

import db


class SahteYanit:
    def __init__(self, status_code=200, govde=None, text=None):
        self.status_code = status_code
        self._govde = govde
        self.text = text if text is not None else json.dumps(govde or [])

    def json(self):
        return self._govde


class SahteIstemci:
    """httpx.AsyncClient yerine gecer; cagri kayitlarini tutar."""

    def __init__(self, get=None, patch=None, post=None):
        self._get, self._patch, self._post = get, patch, post
        self.cagrilar = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.cagrilar.append(("GET", url))
        return self._get(url, kw) if callable(self._get) else self._get

    async def patch(self, url, **kw):
        self.cagrilar.append(("PATCH", url, kw.get("json")))
        return self._patch(url, kw) if callable(self._patch) else self._patch

    async def post(self, url, **kw):
        self.cagrilar.append(("POST", url, kw.get("json")))
        return self._post(url, kw) if callable(self._post) else self._post


@pytest.fixture(autouse=True)
def _ortam(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "https://ornek.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "servis-anahtari")


def _kur(monkeypatch, istemci):
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: istemci)
    return istemci


GECERLI = [{"code": "ABCD1234", "credits": 20, "batch": "lansman",
            "expires_at": None, "redeemed_by": None}]


# ── Normalizasyon ───────────────────────────────────────────────────────
def test_normalizasyon_bosluk_ve_tire_siler():
    assert db.promo_kodu_normalize(" ab cd-1234 ") == "ABCD1234"
    assert db.promo_kodu_normalize("abcd1234") == "ABCD1234"


def test_uretilen_kod_karistirilabilir_karakter_icermez():
    """Kod DM'de okunup telefonda elle yazilacak: 0/O, 1/I/L olmamali."""
    for _ in range(200):
        assert not (set(db.promo_kodu_uret()) & set("01OIL"))


def test_uretilen_kodlar_benzersiz():
    assert len({db.promo_kodu_uret() for _ in range(500)}) == 500


# ── Mutlu yol ───────────────────────────────────────────────────────────
def test_gecerli_kod_token_yatirir(monkeypatch):
    ist = _kur(monkeypatch, SahteIstemci(
        get=SahteYanit(200, GECERLI),
        patch=SahteYanit(200, GECERLI),
        post=SahteYanit(200, {}),
    ))
    r = asyncio.run(db.promo_kodu_kullan("u1", "abcd-1234"))
    assert r == {"ok": True, "credits": 20}

    rpc = [c for c in ist.cagrilar if c[0] == "POST"][0]
    govde = rpc[2]
    assert govde["p_amount"] == 20
    assert govde["p_gifted_delta"] == 20
    # HEDIYE token: `total_credits_purchased` ARTMAMALI. Artsaydi kullanici
    # check_is_premium'a gore "premium" olur ve ucretsiz-tarama tavanindan
    # tamamen muaf hale gelirdi — kod dagitmak tavani delmek anlamina gelirdi.
    assert govde.get("p_purchased_delta", 0) == 0
    assert govde["p_external_id"] == "promo:ABCD1234"   # idempotency
    assert govde["p_idempotent"] is True


def test_sahiplenme_krediden_ONCE_yapilir(monkeypatch):
    """Ters sira olsaydi es zamanli iki istek ayni kodu iki kez oderdi."""
    ist = _kur(monkeypatch, SahteIstemci(
        get=SahteYanit(200, GECERLI), patch=SahteYanit(200, GECERLI),
        post=SahteYanit(200, {})))
    asyncio.run(db.promo_kodu_kullan("u1", "ABCD1234"))
    tipler = [c[0] for c in ist.cagrilar]
    assert tipler.index("PATCH") < tipler.index("POST")


def test_sahiplenme_yalniz_kullanilmamis_kodu_hedefler(monkeypatch):
    """Kosul URL'den DUSERSE yaris korumasi sessizce yok olur."""
    ist = _kur(monkeypatch, SahteIstemci(
        get=SahteYanit(200, GECERLI), patch=SahteYanit(200, GECERLI),
        post=SahteYanit(200, {})))
    asyncio.run(db.promo_kodu_kullan("u1", "ABCD1234"))
    patch_url = [c for c in ist.cagrilar if c[0] == "PATCH"][0][1]
    assert "redeemed_by=is.null" in patch_url


# ── Reddetme yollari ────────────────────────────────────────────────────
def test_olmayan_kod(monkeypatch):
    _kur(monkeypatch, SahteIstemci(get=SahteYanit(200, [])))
    assert asyncio.run(db.promo_kodu_kullan("u1", "YOKBOYLE1"))["hata"] == "gecersiz_promo_kodu"


def test_zaten_kullanilmis_kod(monkeypatch):
    kullanilmis = [{**GECERLI[0], "redeemed_by": "baskasi"}]
    _kur(monkeypatch, SahteIstemci(get=SahteYanit(200, kullanilmis)))
    assert asyncio.run(db.promo_kodu_kullan("u1", "ABCD1234"))["hata"] == "promo_kodu_kullanilmis"


def test_suresi_gecmis_kod(monkeypatch):
    eski = [{**GECERLI[0], "expires_at": "2020-01-01T00:00:00+00:00"}]
    _kur(monkeypatch, SahteIstemci(get=SahteYanit(200, eski)))
    assert asyncio.run(db.promo_kodu_kullan("u1", "ABCD1234"))["hata"] == "promo_kodu_suresi_gecmis"


def test_yarisi_kaybeden_istek_odenmez(monkeypatch):
    """Iki es zamanli istek: kosullu UPDATE ikincisine BOS satir doner."""
    ist = _kur(monkeypatch, SahteIstemci(
        get=SahteYanit(200, GECERLI),
        patch=SahteYanit(200, []),          # baskasi kapmis
        post=SahteYanit(200, {})))
    r = asyncio.run(db.promo_kodu_kullan("u1", "ABCD1234"))
    assert r["hata"] == "promo_kodu_kullanilmis"
    assert not [c for c in ist.cagrilar if c[0] == "POST"]   # kredi YATMADI


def test_ayni_partiden_ikinci_kod_reddedilir(monkeypatch):
    """(batch, redeemed_by) benzersiz indeksi -> 23505."""
    _kur(monkeypatch, SahteIstemci(
        get=SahteYanit(200, GECERLI),
        patch=SahteYanit(409, None, text='duplicate key value ... 23505')))
    r = asyncio.run(db.promo_kodu_kullan("u1", "ABCD1234"))
    assert r["hata"] == "promo_partisi_zaten_kullanildi"


def test_cok_kisa_kod_ag_istegi_yapmaz(monkeypatch):
    def patlat(*a, **k):
        raise AssertionError("gecersiz kod icin ag istegi yapildi")
    monkeypatch.setattr(db.httpx, "AsyncClient", patlat)
    assert asyncio.run(db.promo_kodu_kullan("u1", "AB"))["hata"] == "gecersiz_promo_kodu"


# ── Telafi ──────────────────────────────────────────────────────────────
def test_kredi_yatmazsa_sahiplenme_geri_alinir(monkeypatch):
    """Kod yanip token yatmazsa musteri kodunu KAYBEDERDI."""
    ist = _kur(monkeypatch, SahteIstemci(
        get=SahteYanit(200, GECERLI),
        patch=SahteYanit(200, GECERLI),
        post=SahteYanit(500, {})))          # kredi RPC'si dustu
    r = asyncio.run(db.promo_kodu_kullan("u1", "ABCD1234"))
    assert r["hata"] == "promo_kullanilamadi"
    geri = [c for c in ist.cagrilar if c[0] == "PATCH"][-1]
    assert geri[2] == {"redeemed_by": None, "redeemed_at": None}


# ── Toplu uretim sinirlari ──────────────────────────────────────────────
@pytest.mark.parametrize("batch,credits,adet", [
    ("", 10, 5),          # parti adi bos
    ("x", 0, 5),          # token 0
    ("x", 1001, 5),       # token tavani
    ("x", 10, 0),         # adet 0
    ("x", 10, 1001),      # adet tavani
])
def test_toplu_uretim_gecersiz_girdiyi_reddeder(monkeypatch, batch, credits, adet):
    def patlat(*a, **k):
        raise AssertionError("gecersiz girdi icin ag istegi yapildi")
    monkeypatch.setattr(db.httpx, "AsyncClient", patlat)
    assert asyncio.run(db.promo_toplu_uret(batch, credits, adet)) == []


def test_toplu_uretim_benzersiz_kod_uretir(monkeypatch):
    yakalanan = {}

    def post(url, kw):
        yakalanan["satirlar"] = kw.get("json")
        return SahteYanit(201, kw.get("json"))

    _kur(monkeypatch, SahteIstemci(post=post))
    kodlar = asyncio.run(db.promo_toplu_uret("lansman", 20, 25))
    assert len(kodlar) == 25 and len(set(kodlar)) == 25
    assert all(x["batch"] == "lansman" and x["credits"] == 20
               for x in yakalanan["satirlar"])
