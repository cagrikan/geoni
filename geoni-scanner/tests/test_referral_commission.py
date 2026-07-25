"""Influencer davet komisyonu — KADEMELI ve SURELI (kurucu karari 2026-07-25).
1. yil %10, 2. yil %5, 3. yil %5, sonra biter. Nakit YALNIZ kabul edilmis
creator/elci ortagina; sıradan kullanicinin karsiligi token odulu.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import db


class _Yanit:
    def __init__(self, data=None, status=200):
        self.status_code, self._data, self.text = status, data, ""

    def json(self):
        return self._data


class _Ist:
    def __init__(self, referred_by="davet-eden-1", kayit=None, kabul=True,
                 tx=True, post_status=201):
        self.referred_by, self.kabul, self.tx = referred_by, kabul, tx
        self.kayit = kayit or datetime.now(timezone.utc)
        self.post_status, self.yazilan = post_status, None

    async def get(self, url, **kw):
        if "profiles" in url:
            return _Yanit([{"referred_by": self.referred_by,
                            "created_at": self.kayit.isoformat()}])
        if "creator_applications" in url:
            return _Yanit([{"id": 1}] if self.kabul else [])
        if "credit_transactions" in url:
            return _Yanit([{"id": "tx-uuid-1"}] if self.tx else [])
        return _Yanit([])

    async def post(self, url, **kw):
        self.yazilan = kw.get("json")
        return _Yanit({}, status=self.post_status)


def _cagir(ist, tutar=79.99, para="USD"):
    return asyncio.run(db._record_referral_commission(ist, "alici-1", "polar_x1", tutar, para))


def _kur(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)


def test_kademeler(monkeypatch):
    """Ay -> oran esiklerini kilitler; 12/24/36 SINIRLARI dahil."""
    simdi = datetime(2026, 7, 25, tzinfo=timezone.utc)
    for gun, beklenen in [(0, 0.10), (300, 0.10), (400, 0.05), (700, 0.05),
                          (1000, 0.05), (1200, 0.0)]:
        oran = db._referral_commission_rate(simdi - timedelta(days=gun), simdi)
        assert oran == beklenen, (gun, oran, beklenen)


def test_ilk_yil_yuzde_on(monkeypatch):
    _kur(monkeypatch)
    ist = _Ist(kayit=datetime.now(timezone.utc) - timedelta(days=30))
    assert _cagir(ist) is True
    assert ist.yazilan["rate"] == 0.10
    assert ist.yazilan["amount"] == 8.0          # 79.99 * 0.10
    assert ist.yazilan["basis_amount"] == 79.99  # matrah: FIILEN odenen
    assert ist.yazilan["kind"] == "referral"
    assert ist.yazilan["expert_id"] == "davet-eden-1"
    assert ist.yazilan["transaction_id"] == "tx-uuid-1"


def test_ikinci_yil_yuzde_bes(monkeypatch):
    _kur(monkeypatch)
    ist = _Ist(kayit=datetime.now(timezone.utc) - timedelta(days=500))
    assert _cagir(ist) is True
    assert ist.yazilan["rate"] == 0.05 and ist.yazilan["amount"] == 4.0


def test_uc_yil_dolunca_biter(monkeypatch):
    """SURELI olmasinin anlami: 3 yil sonra yukumluluk BITER."""
    _kur(monkeypatch)
    ist = _Ist(kayit=datetime.now(timezone.utc) - timedelta(days=1200))
    assert _cagir(ist) is False and ist.yazilan is None


def test_kabul_edilmemis_davet_edene_NAKIT_YOK(monkeypatch):
    """Nakit yalniz kabul edilmis ortaga. Herkese verilseydi (a) her davet
    kalici yukumluluk olurdu (b) kendi kendini davet eden halkalar nakit basardi."""
    _kur(monkeypatch)
    ist = _Ist(kabul=False)
    assert _cagir(ist) is False and ist.yazilan is None


def test_davet_eden_yoksa_komisyon_yok(monkeypatch):
    _kur(monkeypatch)
    ist = _Ist(referred_by=None)
    assert _cagir(ist) is False and ist.yazilan is None


def test_usd_disi_para_atlanir(monkeypatch):
    _kur(monkeypatch)
    assert _cagir(_Ist(), para="TRY") is False


def test_sifir_tutar_komisyon_uretmez(monkeypatch):
    _kur(monkeypatch)
    assert _cagir(_Ist(), tutar=0) is False


def test_ayni_islem_icin_ikinci_komisyon_yok(monkeypatch):
    """Partial unique index 409 doner (mukerrer webhook) -> ikinci borc yok."""
    _kur(monkeypatch)
    ist = _Ist(post_status=409)
    assert _cagir(ist) is False


def test_zaman_damgasi_iki_bicimde_de_cozulur():
    """Supabase iki bicim de donuyor; ':00'suz saat dilimi eskiden patliyordu."""
    a = db._parse_ts("2026-07-25 19:03:04.640749+00")
    b = db._parse_ts("2026-07-25T19:03:04.640749+00:00")
    c = db._parse_ts("2026-07-25T19:03:04Z")
    assert a and b and c and a == b
    assert db._parse_ts(None) is None and db._parse_ts("cop") is None
