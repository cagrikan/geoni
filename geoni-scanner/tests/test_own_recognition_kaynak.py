"""own_recognition sinyalinin KAYNAK ZINCIRI sozlesmesi.

2026-08-01'de sinyal 9 gun uretildikten sonra bir anda kayboldu. Kok neden:
`apply_audit_retention` RPC'si ayni (kullanici+tur+hedef) icin en yeni kayit
disindaki HER raporun `result_json`'unu YASINA BAKMADAN NULL'liyor (rn > 1).
Sinyal tam da o alandan okundugu icin, geoni.ai elle bir kez daha taranir
taranmaz haftalik self-scan kaydi bosaliyor ve sinyal SESSIZCE oluyordu.

Bu test, kalici cozumu commit aninda dogrular:
  (1) self_scan tarama aninda app_config'e kalici anlik goruntu birakir,
  (2) dongu ONCE o anlik goruntuyu okur (retention'a bagimli degil),
  (3) anlik goruntu yoksa audits'e duser — top-level, o da yoksa ic-ice.
GERCEK SUPABASE GEREKMEZ.
"""
import asyncio
import json

import self_improve as si


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


class _FakeClient:
    """URL'e gore cevap veren sahte httpx.AsyncClient. `routes` = (parca -> govde)."""

    def __init__(self, routes):
        self.routes = routes
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        for parca, govde in self.routes:
            if parca in url:
                return _Resp(200, govde)
        return _Resp(200, [])

    async def post(self, url, **kw):
        self.posts.append((url, kw.get("json")))
        return _Resp(201, [])

    async def delete(self, url, **kw):
        return _Resp(204, [])


_MR = {"claude": {"recognized": True, "score": 66.1},
       "gemini": {"recognized": False, "score": 4.1}}


def _kur(monkeypatch, routes):
    monkeypatch.setattr(si, "SUPABASE_URL", "https://ornek.supabase.co")
    monkeypatch.setattr(si, "SUPABASE_SERVICE_KEY", "test-key")
    fake = _FakeClient(routes)
    monkeypatch.setattr(si.httpx, "AsyncClient", lambda *a, **k: fake)
    return fake


def _own_recognition(fake):
    """Yazilan sinyaller icinden own_recognition satirlarini cikar."""
    out = []
    for url, govde in fake.posts:
        if "improvement_signals" in url and isinstance(govde, list):
            out += [s for s in govde if s.get("kind") == "own_recognition"]
    return out


def test_snapshot_birincil_kaynak(monkeypatch):
    """app_config anlik goruntusu varsa audits'e HIC bakilmadan o kullanilir."""
    snap = [{"value": json.dumps({"at": "2026-08-03T07:00:00+00:00",
                                  "score": 71, "model_results": _MR})}]
    fake = _kur(monkeypatch, [("app_config", snap)])

    d = asyncio.run(si.run_improvement_cycle(days=7))
    assert d.get("ok") is not False

    sig = _own_recognition(fake)
    assert {s["subject"] for s in sig} == {"claude", "gemini"}
    claude = next(s for s in sig if s["subject"] == "claude")
    assert claude["metric"] == 1
    assert claude["detail"]["source"] == "snapshot"
    # as_of: sinyalin BAYAT olup olmadigi disaridan gorulebilmeli (self_scan haftalik).
    assert claude["detail"]["as_of"] == "2026-08-03T07:00:00+00:00"


def test_snapshot_yoksa_ic_ice_audit_kaynagina_duser(monkeypatch):
    """Anlik goruntu ve top-level self-scan kaydi yoksa, normal kullanici
    taramasinin brand_recall.model_results'i okunur — 'sessizce bos' kalmaz."""
    nested = [{"created_at": "2026-08-01T07:56:29+00:00",
               "result_json": {"brand_recall": {"model_results": _MR}}}]
    # app_config bos; auto_monitor sorgusu bos; son care sorgusu dolu.
    fake = _kur(monkeypatch, [("app_config", []),
                              ("auto_monitor", []),
                              ("result_json=not.is.null", nested)])

    asyncio.run(si.run_improvement_cycle(days=7))
    sig = _own_recognition(fake)
    assert sig, "ic-ice kaynaktan da sinyal uretilmeliydi"
    assert all(s["detail"]["source"] == "audit_nested" for s in sig)


def test_golge_motor_own_recognition_a_girmez(monkeypatch):
    """grok golge motordur; kendi taninma trendini kirletmemeli (regresyon)."""
    mr = dict(_MR, grok={"recognized": True, "score": 90})
    snap = [{"value": json.dumps({"at": "2026-08-03T07:00:00+00:00", "model_results": mr})}]
    fake = _kur(monkeypatch, [("app_config", snap)])

    asyncio.run(si.run_improvement_cycle(days=7))
    assert "grok" not in {s["subject"] for s in _own_recognition(fake)}


def test_self_scan_kalici_kopya_birakir(monkeypatch):
    """self_scan, taramadan SONRA anlik goruntuyu app_config'e yazar —
    retention kaydi bosaltmadan once. Yazilan govde model_results tasir."""
    audit = [{"created_at": "2026-08-03T07:00:00+00:00",
              "result_json": {"score": 71, "auto_monitor": True, "model_results": _MR}}]
    fake = _kur(monkeypatch, [("audits", audit)])

    assert asyncio.run(si._snapshot_self_recognition()) is True
    yazilan = [g for u, g in fake.posts if "app_config" in u]
    assert yazilan, "app_config'e yazilmadi"
    govde = json.loads(yazilan[0]["value"])
    assert govde["model_results"] == _MR
    assert govde["at"] == "2026-08-03T07:00:00+00:00"


def test_snapshot_bos_model_results_yazmaz(monkeypatch):
    """Bos/eksik sonuc KALICI kopyayi EZMEZ — yoksa bir basarisiz tarama
    saglam anlik goruntuyu silip sinyali yine oldururdu."""
    audit = [{"created_at": "2026-08-03T07:00:00+00:00",
              "result_json": {"auto_monitor": True, "model_results": {}}}]
    fake = _kur(monkeypatch, [("audits", audit)])

    assert asyncio.run(si._snapshot_self_recognition()) is False
    assert not [g for u, g in fake.posts if "app_config" in u]
