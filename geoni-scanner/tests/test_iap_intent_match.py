"""K5: IAP niyeti dogru SATIN ALMAYA baglaniyor mu?

Kusur: `consume_iap_intent` yalniz (user_id, product_id)'nin en taze pending
satirini aliyordu; webhook hangi satin almaya ait oldugunu bilmiyordu. Gecikmis
bir webhook daha SONRA olusturulmus bir niyeti tuketip bileti YANLIS hedefe
aciyor, ikinci webhook ise hedefsiz kaliyordu (target="" -> main.py'deki
oto-teslimat kosulu da dusuyor: musteri odedi, hicbir sey olmuyor).

Testler PostgREST'i sahte bir HTTP istemcisiyle taklit eder; amac SQL'i degil
SECIM MANTIGINI (hangi satir, hangi filtre) sabitlemek. Repo konvansiyonu
geregi async cagrilar `asyncio.run` ile kosulur (pytest-asyncio bagimliligi yok).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import pytest

import db
import iap


T0 = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


class _Yanit:
    def __init__(self, veri, kod=200):
        self._veri, self.status_code = veri, kod

    def json(self):
        return self._veri


class _SahteIstemci:
    """Kaydedilmis niyet satirlarini GET filtrelerine gore suzer."""

    def __init__(self, satirlar):
        self.satirlar = satirlar
        self.patchler = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        sorgu = unquote(url.split("?", 1)[1])
        assert "status=eq.pending" in sorgu
        alt = ust = None
        for parca in sorgu.split("&"):
            if parca.startswith("created_at=gte."):
                alt = datetime.fromisoformat(parca[len("created_at=gte."):])
            elif parca.startswith("created_at=lte."):
                ust = datetime.fromisoformat(parca[len("created_at=lte."):])
        uygun = [
            r for r in self.satirlar
            if r["status"] == "pending"
            and (alt is None or r["created_at"] >= alt)
            and (ust is None or r["created_at"] <= ust)
        ]
        uygun.sort(key=lambda r: r["created_at"], reverse=True)
        return _Yanit([{"id": r["id"], "target": r["target"]} for r in uygun[:1]])

    async def patch(self, url, **kw):
        kimlik = url.split("id=eq.", 1)[1]
        for r in self.satirlar:
            if r["id"] == kimlik:
                r["status"] = "consumed"
        self.patchler.append(kimlik)
        return _Yanit([], 204)

    async def post(self, url, **kw):
        return _Yanit([], 201)


@pytest.fixture
def sahte(monkeypatch):
    def kur(satirlar):
        istemci = _SahteIstemci(satirlar)
        monkeypatch.setattr(db, "SUPABASE_URL", "https://ornek.test")
        monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k")
        monkeypatch.setattr(db, "_headers", lambda: {})
        monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: istemci)
        return istemci
    return kur


def _niyet(kimlik, hedef, dakika):
    return {"id": kimlik, "target": hedef, "status": "pending",
            "created_at": T0 + timedelta(minutes=dakika)}


def _tuket(purchased_at):
    return asyncio.run(db.consume_iap_intent("u", "p", purchased_at))


def test_gecikmis_webhook_kendi_niyetini_bulur(sahte):
    """Asil kusurun birebir testi: 1. alim teslim edilmeden 2. alim yapilir.
    Gecikmis 1. webhook, KENDINDEN SONRA olusan niyeti tuketmemeli."""
    sahte([_niyet("A", "a.com", 0), _niyet("B", "b.com", 10)])
    assert _tuket(T0 + timedelta(minutes=1)) == "a.com", \
        "gecikmis webhook yanlis hedefi tuketti"
    assert _tuket(T0 + timedelta(minutes=11)) == "b.com", \
        "ikinci bilet hedefsiz kaldi"


def test_bayat_niyet_ttl_ile_dusar(sahte):
    """Magaza ekraninda vazgecilen alimin niyeti pending kalir. Cok sonraki
    ilgisiz bir alim o bayat hedefi TUKETMEMELI."""
    eski = -(db.IAP_INTENT_TTL_SECONDS // 60) - 5
    sahte([_niyet("BAYAT", "eski.com", eski)])
    assert _tuket(T0) == ""


def test_ttl_icindeki_niyet_kabul_edilir(sahte):
    sahte([_niyet("TAZE", "yeni.com", -5)])
    assert _tuket(T0) == "yeni.com"


def test_satin_almadan_sonraki_niyet_secilmez(sahte):
    """Zaman ust siniri: satin almadan SONRA yazilan niyet o alima ait olamaz."""
    sahte([_niyet("SONRAKI", "sonraki.com", 5)])
    assert _tuket(T0) == ""


def test_purchased_at_yoksa_cokmez(sahte):
    """Magaza zamani vermezse webhook yine islenebilmeli (TTL yine gecerli)."""
    sahte([_niyet("X", "x.com", 0)])
    assert _tuket(None) in ("x.com", "")


def test_purchased_at_parse_edilir():
    e = iap.parse_event({"event": {"type": "NON_RENEWING_PURCHASE", "app_user_id": "u",
                                   "product_id": "p", "id": "e1",
                                   "purchased_at_ms": 1785250000000}})
    assert e["purchased_at"] == datetime.fromtimestamp(1785250000, tz=timezone.utc)


@pytest.mark.parametrize("bozuk", [None, "abc", {}, 10 ** 20])
def test_bozuk_purchased_at_cokmez(bozuk):
    """Bozuk zaman damgasi webhook'u DUSURMEMELI — zamansiz da islenebilir."""
    e = iap.parse_event({"event": {"type": "NON_RENEWING_PURCHASE", "app_user_id": "u",
                                   "product_id": "p", "id": "e2",
                                   "purchased_at_ms": bozuk}})
    assert e is not None and e["purchased_at"] is None


def test_create_intent_artik_supersede_etmiyor():
    """Supersede geri gelirse gecikmis webhook kendi niyetini yine bulamaz
    (satir pending olmaktan cikip aramadan duser)."""
    import inspect
    kaynak = inspect.getsource(db.create_iap_intent)
    # Kelimenin kendisi docstring'de geciyor (neden kaldirildigini anlatiyor),
    # bu yuzden KOD kalibina bakilir: supersede tek PATCH cagrisiydi.
    assert '"status": "superseded"' not in kaynak, \
        "supersede yazimi geri gelmis — K5 yarisi yeniden acilir"
    assert "client.patch" not in kaynak, \
        "create_iap_intent yine PATCH atiyor — supersede geri gelmis olabilir"


def test_zaman_damgasi_url_guvenli():
    """`isoformat()` "+00:00" uretir; PostgREST sorgu dizesinde "+" BOSLUGA
    cozulur ve zaman filtresi sessizce bozulur. Filtrelerde Z bicimi sart."""
    from datetime import datetime as _dt
    d = db._pgrest_ts(_dt(2026, 7, 30, 12, 0, tzinfo=timezone.utc))
    assert d == "2026-07-30T12:00:00Z"
    assert "+" not in d
    # Naive girdi UTC sayilir — cagiran yerlerde tzinfo unutulursa da bozulmasin.
    assert db._pgrest_ts(_dt(2026, 7, 30, 12, 0)) == "2026-07-30T12:00:00Z"


def test_niyet_filtreleri_arti_icermez(sahte):
    """Uretilen PostgREST URL'sinde "+" olmamali (bozuk filtre erken yakalansin)."""
    istemci = sahte([_niyet("X", "x.com", 0)])
    gorulen = {}

    async def _get(url, **kw):
        gorulen["url"] = url
        return _Yanit([])

    istemci.get = _get
    _tuket(T0)
    assert "+" not in gorulen["url"], f"URL'de + var: {gorulen['url']}"
