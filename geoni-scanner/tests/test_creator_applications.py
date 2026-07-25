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
