"""Creator/uzman basvurulari — ADMIN KARARI.

Kritik davranis: kabul kararini yalnizca bu fonksiyon verir. DM botu (Vercel
tarafinda) en fazla 'interviewed' yazabiliyor; kabul/red buradan gecer.
Testler ag GEREKTIRMEZ — httpx.AsyncClient sahtelenir.
"""
import asyncio

import db


class _SahteYanit:
    def __init__(self, data=None, status=200):
        self.status_code, self._data, self.text, self.headers = status, data, "", {}

    def json(self):
        return self._data


class _SahteIstemci:
    """GET -> basvuru satiri; PATCH -> kaydedilen govdeyi tutar."""

    def __init__(self, app_row, emails=None):
        self.app_row = app_row
        self.patch_govde = None
        self.patch_url = None
        self.post_cagrilari = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if "creator_applications" in url:
            return _SahteYanit([self.app_row] if self.app_row else [])
        if "profiles" in url:
            return _SahteYanit([{"referral_code": None}])
        return _SahteYanit([])

    async def patch(self, url, **kw):
        if "creator_applications" in url:
            self.patch_url, self.patch_govde = url, kw.get("json") or {}
            return _SahteYanit([{**self.app_row, **self.patch_govde}])
        return _SahteYanit([{}])

    async def post(self, url, **kw):
        self.post_cagrilari.append(url)
        return _SahteYanit({})

    async def delete(self, url, **kw):
        return _SahteYanit({})


def _kur(monkeypatch, app_row, emails=None):
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    istemci = _SahteIstemci(app_row)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: istemci)

    async def _mails(*a, **k):
        return emails or {}

    monkeypatch.setattr(db, "_fetch_all_auth_emails", _mails, raising=False)
    return istemci


UUID = "b993ae8f-1234-4abc-8def-0123456789ab"


def test_gecersiz_karar_reddedilir(monkeypatch):
    """'interviewed'/'new' gibi degerler bu uctan GECMEZ — kabul/red disi
    durumlari yalnizca mulakat akisi yazabilir."""
    for kotu in ("interviewed", "new", "accepted ", "", "AcCePtEd"):
        r = asyncio.run(db.admin_decide_creator_application(1, "admin-1", kotu))
        assert r["success"] is False and r["error"] == "invalid_decision", kotu


def test_kabul_referral_kodu_baglar(monkeypatch):
    """Hesabi olan basvuran kabul edilince davet kodu URETILIR ve satira yazilir
    — "elci" mekaniginin calistigi yer burasi."""
    ist = _kur(monkeypatch, {"id": 7, "status": "interviewed", "user_id": UUID,
                             "email": None, "referral_code": None})
    r = asyncio.run(db.admin_decide_creator_application(7, "admin-1", "accepted"))
    assert r["success"] is True
    assert r["referral_code"] == db._ref_code_for(UUID)
    assert ist.patch_govde["status"] == "accepted"
    assert ist.patch_govde["reviewed_by"] == "admin-1"
    assert ist.patch_govde["referral_code"] == db._ref_code_for(UUID)


def test_hesabi_yoksa_kabul_gecer_ama_kod_yok(monkeypatch):
    """Kayitsiz creator kabul EDILEBILIR; kod uretilemez ve bu SESSIZ GECMEZ,
    note='hesap_yok' ile bildirilir (panel 'link nerede' dedirtmesin)."""
    ist = _kur(monkeypatch, {"id": 8, "status": "new", "user_id": None,
                             "email": "yok@ornek.com", "referral_code": None},
               emails={})
    r = asyncio.run(db.admin_decide_creator_application(8, "admin-1", "accepted"))
    assert r["success"] is True and r["referral_code"] is None
    assert r["note"] == "hesap_yok"
    assert "referral_code" not in ist.patch_govde


def test_kabul_epostadan_hesap_esler(monkeypatch):
    """user_id bos ama ayni e-postali hesap varsa eslenir (buyuk/kucuk harf ve
    bosluk farki eslesmeyi bozmamali)."""
    ist = _kur(monkeypatch, {"id": 9, "status": "interviewed", "user_id": None,
                             "email": "  Creator@Ornek.COM ", "referral_code": None},
               emails={UUID: "creator@ornek.com"})
    r = asyncio.run(db.admin_decide_creator_application(9, "admin-1", "accepted"))
    assert r["user_id"] == UUID
    assert ist.patch_govde["user_id"] == UUID


def test_red_uzman_yetkisi_ACMAZ(monkeypatch):
    """make_expert=True gonderilse bile RED kararinda uzman yetkisi acilmamali."""
    cagrildi = []

    async def _sahte_expert(uid, flag, types=None):
        cagrildi.append((uid, flag))
        return True

    ist = _kur(monkeypatch, {"id": 10, "status": "new", "user_id": UUID,
                             "email": None, "referral_code": None})
    monkeypatch.setattr(db, "admin_set_is_expert", _sahte_expert, raising=False)
    r = asyncio.run(db.admin_decide_creator_application(10, "admin-1", "rejected", make_expert=True))
    assert r["success"] is True
    assert cagrildi == []
    assert ist.patch_govde["status"] == "rejected"


def test_kabul_uzman_yetkisi_secilirse_acilir(monkeypatch):
    cagrildi = []

    async def _sahte_expert(uid, flag, types=None):
        cagrildi.append((uid, flag, types))
        return True

    _kur(monkeypatch, {"id": 11, "status": "interviewed", "user_id": UUID,
                       "email": None, "referral_code": None})
    monkeypatch.setattr(db, "admin_set_is_expert", _sahte_expert, raising=False)
    asyncio.run(db.admin_decide_creator_application(
        11, "admin-1", "accepted", make_expert=True, ticket_type_ids=[1, 3]))
    assert cagrildi == [(UUID, True, [1, 3])]


def test_hesapsiz_kabulde_uzman_yetkisi_denenmez(monkeypatch):
    """user_id yoksa is_expert yazacak profil de yok — bos cagri yapilmamali."""
    cagrildi = []

    async def _sahte_expert(uid, flag, types=None):
        cagrildi.append(uid)
        return True

    _kur(monkeypatch, {"id": 12, "status": "new", "user_id": None,
                       "email": None, "referral_code": None}, emails={})
    monkeypatch.setattr(db, "admin_set_is_expert", _sahte_expert, raising=False)
    asyncio.run(db.admin_decide_creator_application(12, "admin-1", "accepted", make_expert=True))
    assert cagrildi == []


def test_olmayan_basvuru(monkeypatch):
    _kur(monkeypatch, None)
    r = asyncio.run(db.admin_decide_creator_application(999, "admin-1", "accepted"))
    assert r["success"] is False and r["error"] == "not_found"


def test_mevcut_kod_yeniden_uretilmez(monkeypatch):
    """Zaten kodu olan basvuru tekrar kabul edilirse kod DEGISMEZ — daginmis
    davet linkleri olu linke donmemeli."""
    ist = _kur(monkeypatch, {"id": 13, "status": "interviewed", "user_id": UUID,
                             "email": None, "referral_code": "eskikod"})
    r = asyncio.run(db.admin_decide_creator_application(13, "admin-1", "accepted"))
    assert r["referral_code"] == "eskikod"
    assert ist.patch_govde["referral_code"] == "eskikod"


# ── Uzman teslim odemesi (SABIT UCRET) ──────────────────────────────────────
# Kurucu karari 2026-07-25: yuzde DEGIL, onaylanan teslim basina sabit ucret
# (ticket_types.expert_payout_usd). Yuzdenin matrahi belirsizdi.

class _OdemeIstemci:
    def __init__(self, ticket, tip, post_status=201):
        self.ticket, self.tip, self.post_status = ticket, tip, post_status
        self.yazilan = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if "/tickets?" in url:
            return _SahteYanit([self.ticket] if self.ticket else [])
        if "ticket_types" in url:
            return _SahteYanit([self.tip] if self.tip else [])
        return _SahteYanit([])

    async def post(self, url, **kw):
        self.yazilan = kw.get("json")
        return _SahteYanit({}, status=self.post_status)


BILET = {"id": 5, "user_id": "musteri-1", "assigned_expert_id": "uzman-1",
         "ticket_type_id": 3, "token_cost": 1200}
TIP = {"key": "wikidata_entity", "name": "Wikidata", "expert_payout_usd": "35.00",
       "token_cost": 1200, "money_price": "107.99"}


def _odeme(monkeypatch, ticket=BILET, tip=TIP, post_status=201):
    monkeypatch.setattr(db, "SUPABASE_URL", "http://x", raising=False)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "k", raising=False)
    ist = _OdemeIstemci(ticket, tip, post_status)
    return ist, asyncio.run(db._record_delivery_payout(ist, 5))


def test_odeme_sabit_ucret_yazilir(monkeypatch):
    ist, ok = _odeme(monkeypatch)
    assert ok is True
    y = ist.yazilan
    assert y["amount"] == 35.0                 # SABIT ucret — yuzde degil
    assert y["basis_amount"] == 107.99         # baglam: hizmetin GERCEK fiyati
    assert y["expert_id"] == "uzman-1" and y["customer_id"] == "musteri-1"
    assert y["kind"] == "delivery" and y["status"] == "pending"
    assert y["period_month"].endswith("-01")   # ay basina yuvarli donem


def test_odeme_token_kurundan_TUREMEZ(monkeypatch):
    """basis_amount hizmetin gercek fiyati olmali; token x referans kur DEGIL.
    Turetseydik 1200 x 0.08 = $96 cikardi ve musterinin gordugu $107.99 ile
    celisirdi."""
    ist, _ = _odeme(monkeypatch)
    assert ist.yazilan["basis_amount"] != round(1200 * db.TOKEN_REFERENCE_USD, 2)


def test_fiyat_yoksa_token_referansina_duser(monkeypatch):
    ist, ok = _odeme(monkeypatch, tip={**TIP, "money_price": None})
    assert ok is True
    assert ist.yazilan["basis_amount"] == round(1200 * db.TOKEN_REFERENCE_USD, 2)


def test_atanmis_uzman_yoksa_odeme_yok(monkeypatch):
    ist, ok = _odeme(monkeypatch, ticket={**BILET, "assigned_expert_id": None})
    assert ok is False and ist.yazilan is None


def test_ucreti_tanimsiz_hizmet_odenmez(monkeypatch):
    """llms_robots/schema_setup bilerek NULL: otomasyon dusunce $5'lik is
    atmak anlamsiz. NULL = ucretli uzmana atanmaz/odenmez."""
    ist, ok = _odeme(monkeypatch, tip={**TIP, "expert_payout_usd": None})
    assert ok is False and ist.yazilan is None


def test_tekrar_onayda_IKINCI_BORC_YOK(monkeypatch):
    """Red bileti 'assigned'a geri donduruyor, yani tekrar onaylanabiliyor.
    Kismi benzersiz indeks 409 doner -> yeni borc acilmaz, bu hata degil."""
    ist, ok = _odeme(monkeypatch, post_status=409)
    assert ok is False


def test_olmayan_bilet(monkeypatch):
    ist, ok = _odeme(monkeypatch, ticket=None)
    assert ok is False and ist.yazilan is None
