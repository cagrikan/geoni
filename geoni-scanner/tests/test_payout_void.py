"""Odeme iptal yollari + telafi + fail-closed komisyon (2026-07-26 denetimi)."""
import asyncio
from datetime import datetime, timedelta, timezone

import db


class _Yanit:
    def __init__(self, data=None, status=200):
        self.status_code, self._data, self.text = status, data, ""

    def json(self):
        return self._data


class _Ist:
    """PATCH/POST govdelerini kaydeden sahte istemci."""
    def __init__(self, patch_rows=None, get_rows=None, post_status=200):
        self.patch_rows = patch_rows if patch_rows is not None else [{"id": 1}]
        self.get_rows = get_rows or []
        self.post_status = post_status
        self.patchler, self.postlar = [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        return _Yanit(self.get_rows)

    async def patch(self, url, **kw):
        self.patchler.append((url, kw.get("json")))
        return _Yanit(self.patch_rows)

    async def post(self, url, **kw):
        self.postlar.append((url, kw.get("json")))
        return _Yanit({}, status=self.post_status)


def _kur(monkeypatch, ist):
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: ist)


# ── Y1: teslim odemesi iptali ────────────────────────────────────────────────

def test_teslim_odemesi_iptal_edilir(monkeypatch):
    ist = _Ist(); _kur(monkeypatch, ist)
    assert asyncio.run(db.void_delivery_payout(5, "teslim reddedildi")) is True
    url, govde = ist.patchler[0]
    assert "kind=eq.delivery" in url and "status=neq.void" in url
    assert "ticket_id=eq.5" in url
    assert govde["status"] == "void"


def test_zaten_iptalse_False(monkeypatch):
    """Idempotent: void satiri filtre disinda -> 0 satir -> False."""
    ist = _Ist(patch_rows=[]); _kur(monkeypatch, ist)
    assert asyncio.run(db.void_delivery_payout(5)) is False


# ── Admin manuel iptal ───────────────────────────────────────────────────────

def test_admin_void_geri_ALINAMAZ(monkeypatch):
    """status=neq.void filtresi: void bir satir tekrar degistirilemez."""
    ist = _Ist(); _kur(monkeypatch, ist)
    assert asyncio.run(db.admin_void_payout(9, "admin-1", "yanlis satir")) is True
    url, govde = ist.patchler[0]
    assert "status=neq.void" in url and govde["status"] == "void"
    assert "yanlis satir" in govde["note"]


# ── K5: telafi (kredi dustu ama bilet acilmadi) ──────────────────────────────

def test_telafi_krediyi_geri_verir_VE_deftere_yazar(monkeypatch):
    """Eskiden yalniz bakiye geri veriliyordu; defterde karsiliksiz
    '-cost ticket_purchase' satiri kaliyordu."""
    ist = _Ist(); _kur(monkeypatch, ist)
    asyncio.run(db._telafi_et("u1", 1200, {"key": "wikidata_entity"}, "exception"))
    rpc = [g for u, g in ist.postlar if "deduct_credits_if_enough" in u]
    ledger = [g for u, g in ist.postlar if "credit_transactions" in u]
    assert rpc and rpc[0]["p_amount"] == -1200     # NEGATIF = geri verme
    assert ledger and ledger[0]["amount"] == 1200  # POZITIF telafi satiri
    assert ledger[0]["type"] == "ticket_refund"


def test_telafi_rpc_basarisizsa_ledger_YAZILMAZ(monkeypatch):
    """Bakiye geri verilemediyse sahte bir iade satiri yazmak defteri BOZAR."""
    ist = _Ist(post_status=500); _kur(monkeypatch, ist)
    asyncio.run(db._telafi_et("u1", 100, {"key": "x"}, "exception"))
    assert not [g for u, g in ist.postlar if "credit_transactions" in u]


# ── O7: komisyon fail-closed ─────────────────────────────────────────────────

def test_bozuk_kayit_tarihinde_komisyon_ODENMEZ(monkeypatch):
    """Eskiden `or simdi` ile ay=0 -> EN YUKSEK oran (%10) odeniyordu."""
    ist = _Ist(get_rows=[{"referred_by": "davet-eden", "created_at": "bozuk-tarih"}])
    _kur(monkeypatch, ist)
    r = asyncio.run(db._record_referral_commission(ist, "alici", "ext1", 79.99, "USD"))
    assert r is False
    assert not [g for u, g in ist.postlar if "expert_payouts" in u]


def test_kademe_fonksiyonu_bozulmadi():
    simdi = datetime(2026, 7, 26, tzinfo=timezone.utc)
    assert db._referral_commission_rate(simdi - timedelta(days=10), simdi) == 0.10
    assert db._referral_commission_rate(simdi - timedelta(days=500), simdi) == 0.05
    assert db._referral_commission_rate(simdi - timedelta(days=1200), simdi) == 0.0


# ── K5 ikinci yari: tekrar-deneme (idempotency) korumasi ────────────────────

class _AlimIstemci:
    """purchase_ticket icin: GET -> mevcut bilet, POST -> insert yaniti."""
    def __init__(self, mevcut=None, insert_status=201):
        self.mevcut = mevcut or []
        self.insert_status = insert_status
        self.rpc_cagrilari, self.insertler = [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if "tickets?request_id" in url:
            return _Yanit(self.mevcut)
        return _Yanit([])

    async def post(self, url, **kw):
        if "deduct_credits_if_enough" in url:
            self.rpc_cagrilari.append(kw.get("json"))
            return _Yanit([{"credit_balance": 100}])
        if "/tickets" in url:
            self.insertler.append(kw.get("json"))
            if self.insert_status != 201:
                return _Yanit(None, status=self.insert_status)
            return _Yanit([{"id": 77}], status=201)
        return _Yanit({}, status=201)


def test_ayni_request_id_IKINCI_KEZ_KONTOR_DUSMEZ(monkeypatch):
    """Kullanici hata alip tekrar denedigincde eskiden bir daha dusuyordu."""
    ist = _AlimIstemci(mevcut=[{"id": 42, "ticket_type_id": 3}])
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: ist)

    async def _tip(_):
        return {"id": 3, "key": "wikidata_entity", "name": "W", "token_cost": 1200, "is_active": True}

    async def _eksik(*a, **k):
        return []

    monkeypatch.setattr(db, "_get_ticket_type", _tip, raising=False)
    monkeypatch.setattr(db, "missing_service_prerequisites", _eksik, raising=False)

    r = asyncio.run(db.purchase_ticket("u1", 3, None, "ornek.com", request_id="req-1"))
    assert r["success"] is True and r["ticket_id"] == 42 and r.get("idempotent") is True
    # EN ONEMLI ASSERT: hic kontor dusulmemis olmali
    assert ist.rpc_cagrilari == [], f"idempotent yolda kontor dusuldu: {ist.rpc_cagrilari}"
    assert ist.insertler == [], "idempotent yolda yeni bilet acildi"


def test_request_id_yoksa_eski_davranis(monkeypatch):
    """Geriye uyum: kimlik gondermeyen eski istemciler calismaya devam eder."""
    ist = _AlimIstemci()
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: ist)

    async def _tip(_):
        return {"id": 3, "key": "wikidata_entity", "name": "W", "token_cost": 1200, "is_active": True}

    async def _eksik(*a, **k):
        return []

    monkeypatch.setattr(db, "_get_ticket_type", _tip, raising=False)
    monkeypatch.setattr(db, "missing_service_prerequisites", _eksik, raising=False)

    r = asyncio.run(db.purchase_ticket("u1", 3, None, "ornek.com"))
    assert r["success"] is True and r["ticket_id"] == 77
    assert ist.rpc_cagrilari and ist.rpc_cagrilari[0]["p_amount"] == 1200
    assert ist.insertler and ist.insertler[0]["request_id"] is None
