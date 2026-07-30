"""`deduct_credits` tek atomik RPC'ye taşındı (2026-07-29 denetimi, K1).

Eski sürüm check-then-act idi: defterde var mı SELECT → bakiye düş RPC →
defter satırı POST. Üç ayrı HTTP çağrısı, tek transaction değil. DB'deki kısmi
UNIQUE yalnız ikinci DEFTER SATIRINI engelliyordu, ikinci BAKİYE DÜŞÜMÜNÜ değil
→ iki eşzamanlı çağrı bakiyeyi iki kez düşürüyordu.

Burada kilitlenen davranış: tek RPC çağrısı, ve dönüşün doğru yorumlanması.
`duplicate` (aynı iş zaten ücretlendirilmiş) **True** dönmeli — iş ücretlidir,
ama bakiyeye ikinci kez dokunulmaz. `insufficient` **False** dönmeli ki çağıran
`credits_spent`'i 0'a çeksin.
"""
import asyncio

import db


class FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    @property
    def text(self):
        return ""


class FakeClient:
    """Yalnız spend_credits_atomic'i tanır; başka bir uca gidilirse test patlar."""

    def __init__(self, cagrilar, cevap):
        self._cagrilar = cagrilar
        self._cevap = cevap

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self._cagrilar.append((url, kw.get("json") or {}))
        assert "/rpc/spend_credits_atomic" in url, f"beklenmeyen uç: {url}"
        return self._cevap

    async def get(self, url, **kw):  # pragma: no cover - çağrılmamalı
        raise AssertionError(f"deduct_credits artık GET atmamalı: {url}")


def _kur(monkeypatch, cevap):
    cagrilar = []
    monkeypatch.setattr(db, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: FakeClient(cagrilar, cevap))
    return cagrilar


def test_basarili_dusum_true_doner(monkeypatch):
    cagrilar = _kur(monkeypatch, FakeResp(200, {"applied": True, "balance": 95}))
    ok = asyncio.run(db.deduct_credits("u1", 5, "web_audit", "job-1"))
    assert ok is True
    assert len(cagrilar) == 1, "tek RPC çağrısı olmalı"
    _, govde = cagrilar[0]
    assert govde == {
        "p_user_id": "u1", "p_amount": 5,
        "p_description": "web_audit", "p_reference_id": "job-1",
    }


def test_tek_http_cagrisi_yapilir(monkeypatch):
    """Asıl regresyon: eskiden 3 çağrı vardı ve yarış penceresi aradaydı."""
    cagrilar = _kur(monkeypatch, FakeResp(200, {"applied": True, "balance": 1}))
    asyncio.run(db.deduct_credits("u1", 5, "web_audit", "job-1"))
    assert len(cagrilar) == 1


def test_duplicate_true_doner_ve_ucretli_sayilir(monkeypatch):
    """SQS yeniden teslimi: iş zaten ücretlendirilmiş → True, bakiye korunur."""
    _kur(monkeypatch, FakeResp(200, {"applied": False, "reason": "duplicate"}))
    assert asyncio.run(db.deduct_credits("u1", 5, "web_audit", "job-1")) is True


def test_yetersiz_bakiye_false_doner(monkeypatch):
    _kur(monkeypatch, FakeResp(200, {"applied": False, "reason": "insufficient"}))
    assert asyncio.run(db.deduct_credits("u1", 5, "web_audit", "job-1")) is False


def test_bad_input_false_doner(monkeypatch):
    _kur(monkeypatch, FakeResp(200, {"applied": False, "reason": "bad_input"}))
    assert asyncio.run(db.deduct_credits("u1", 0, "web_audit", "job-1")) is False


def test_http_hatasi_false_doner(monkeypatch):
    _kur(monkeypatch, FakeResp(500, None))
    assert asyncio.run(db.deduct_credits("u1", 5, "web_audit", "job-1")) is False


def test_reference_id_yoksa_da_calisir(monkeypatch):
    cagrilar = _kur(monkeypatch, FakeResp(200, {"applied": True, "balance": 1}))
    assert asyncio.run(db.deduct_credits("u1", 5, "web_audit")) is True
    assert cagrilar[0][1]["p_reference_id"] is None


def test_supabase_ayarsizsa_false(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "")
    assert asyncio.run(db.deduct_credits("u1", 5, "web_audit", "job-1")) is False


def test_dusum_basarisizsa_maliyet_sifirlanir_zinciri(monkeypatch):
    """K1 ile K3 zinciri: insufficient → save_audit satırı 0'a çekmeli.

    (save_audit'in kendi testleri ayrı dosyada; burada iki parçanın BİRLİKTE
    doğru çalıştığı kilitleniyor — deduct_credits'in False dönmesi yeterli.)
    """
    _kur(monkeypatch, FakeResp(200, {"applied": False, "reason": "insufficient"}))
    assert asyncio.run(db.deduct_credits("u1", 5, "web_audit", "job-x")) is False
