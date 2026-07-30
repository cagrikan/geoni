"""Para yollarında test boşluğunu kapatır (2026-07-29 denetimi, K8).

`record_purchase`, `record_refund` ve `get_admin_sales_stats` için sıfır Python
testi vardı. Mekanizma DB tarafında (apply_credit_change + UNIQUE) sağlam, ama
Python katmanı testsiz olduğu için biri ileride bu deseni bozarsa hiçbir şey
kırmızıya dönmüyordu — burada kilitlenen tam olarak o desen.
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
    def __init__(self, yonlendirici, kayit):
        self._y = yonlendirici
        self._kayit = kayit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self._kayit.append(("POST", url, kw.get("json") or {}))
        return self._y("POST", url, kw)

    async def get(self, url, **kw):
        self._kayit.append(("GET", url, kw))
        return self._y("GET", url, kw)


def _kur(monkeypatch, yonlendirici):
    kayit = []
    monkeypatch.setattr(db, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: FakeClient(yonlendirici, kayit))
    return kayit


# --- record_purchase -------------------------------------------------------

def test_satin_alma_atomik_rpc_kullanir(monkeypatch):
    """Bakiyeyi oku-değiştir-yaz ile GÜNCELLEMEMELİ — tek atomik RPC."""
    kayit = _kur(monkeypatch, lambda m, u, kw: FakeResp(200, {"applied": True, "balance": 100}))
    ok = asyncio.run(db.record_purchase("u1", 100, 9.99, "USD", "rc_abc", channel="android"))
    assert ok is True
    urls = [u for _, u, _ in kayit]
    assert any("rpc/apply_credit_change" in u for u in urls)
    assert not any(u.endswith("/profiles") for u in urls), "bakiye doğrudan PATCH'lenmemeli"


def test_mukerrer_webhook_ikinci_kez_kredilemez(monkeypatch):
    """Aynı external_id ikinci teslim: çift kredi YOK, komisyon da YOK.

    Dönüş **True** olmalı — webhook onaylanır ki RevenueCat tekrar tekrar
    denemesin. Asıl kilitlenen şey dönüş değeri değil, `duplicate` yolunda
    komisyon kaydının ÇAĞRILMAMASI: mükerrer teslim yeni bir alım değil, bu
    ayrım kaybolursa aynı satıştan iki kez komisyon ödenir.
    """
    kayit = _kur(monkeypatch, lambda m, u, kw: FakeResp(200, {"applied": False, "reason": "duplicate"}))
    assert asyncio.run(db.record_purchase("u1", 100, 9.99, "USD", "rc_abc")) is True
    assert len(kayit) == 1, "yalnız RPC çağrılmalı — komisyon yolu çalışmamalı"
    assert "apply_credit_change" in kayit[0][1]


def test_ilk_teslimde_komisyon_yolu_calisir(monkeypatch):
    """Karşıt test: applied=True olduğunda komisyon kaydı DENENMELİ.

    Bu olmadan üstteki test, komisyonun hiç çalışmamasıyla da geçerdi.
    """
    kayit = _kur(monkeypatch, lambda m, u, kw: FakeResp(200, {"applied": True, "balance": 100}))
    assert asyncio.run(db.record_purchase("u1", 100, 9.99, "USD", "rc_yeni")) is True
    assert len(kayit) > 1, "komisyon yolu en az bir ek çağrı yapmalı"


def test_kredi_sifirsa_islem_yapilmaz(monkeypatch):
    kayit = _kur(monkeypatch, lambda m, u, kw: FakeResp(200, {"applied": True}))
    assert asyncio.run(db.record_purchase("u1", 0, 9.99, "USD", "rc_x")) is False
    assert kayit == [], "hiç HTTP çağrısı yapılmamalı"


def test_kanal_ve_tutar_rpcye_gecer(monkeypatch):
    """Kanal etiketi ciro raporunun kırılım anahtarı — RPC'ye ulaşmalı."""
    kayit = _kur(monkeypatch, lambda m, u, kw: FakeResp(200, {"applied": True}))
    asyncio.run(db.record_purchase("u1", 50, 739.99, "TRY", "rc_1", channel="android_sandbox"))
    govde = next(g for _, u, g in kayit if "apply_credit_change" in u)
    assert govde["p_channel"] == "android_sandbox"
    assert govde["p_amount_paid"] == 739.99
    assert govde["p_currency"] == "TRY"
    assert govde["p_external_id"] == "rc_1"


def test_ayarsizsa_fail_closed(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "")
    assert asyncio.run(db.record_purchase("u1", 100, 9.99, "USD", "rc_a")) is False


# --- get_admin_sales_stats -------------------------------------------------

def _satislar(rows):
    def yonlendirici(method, url, kw):
        if method == "GET" and "credit_transactions" in url:
            return FakeResp(200, rows)
        return FakeResp(200, [])
    return yonlendirici


def test_sandbox_ciroya_girmez_ve_sayilir(monkeypatch):
    rows = [
        {"user_id": "u1", "amount": 100, "channel": "android", "amount_paid": 100, "currency_paid": "TRY", "created_at": "2026-07-29T10:00:00Z"},
        {"user_id": "u1", "amount": 100, "channel": "android_sandbox", "amount_paid": 739.99, "currency_paid": "TRY", "created_at": "2026-07-29T11:00:00Z"},
        {"user_id": "u2", "amount": 100, "channel": "ios_sandbox", "amount_paid": 499.99, "currency_paid": "TRY", "created_at": "2026-07-29T12:00:00Z"},
    ]
    _kur(monkeypatch, _satislar(rows))
    r = asyncio.run(db.get_admin_sales_stats(days=7))
    assert r["revenue_total"] == 100, "sandbox tutarları ciroya girmemeli"
    assert r["sandbox_excluded"] == 2, "dışlananların SAYISI dönmeli (sessizce yutulmamalı)"
    assert "android_sandbox" not in r["revenue_by_channel"]
    assert r["revenue_by_channel"]["android"] == 100


def test_recent_sandbox_icermez(monkeypatch):
    rows = [
        {"user_id": "u1", "amount": 1, "channel": "ios_sandbox", "amount_paid": 1, "currency_paid": "TRY", "created_at": "2026-07-29T10:00:00Z"},
    ]
    _kur(monkeypatch, _satislar(rows))
    r = asyncio.run(db.get_admin_sales_stats(days=7))
    assert r["recent"] == []
    assert r["sandbox_excluded"] == 1


def test_kanali_bos_satir_web_sayilir(monkeypatch):
    rows = [{"user_id": "u1", "amount": 1, "channel": None, "amount_paid": 25, "currency_paid": "TRY", "created_at": "2026-07-29T10:00:00Z"}]
    _kur(monkeypatch, _satislar(rows))
    r = asyncio.run(db.get_admin_sales_stats(days=7))
    assert r["revenue_by_channel"]["web"] == 25
    assert r["sandbox_excluded"] == 0


def test_gunluk_seri_istenen_gun_sayisinda(monkeypatch):
    _kur(monkeypatch, _satislar([]))
    r = asyncio.run(db.get_admin_sales_stats(days=7))
    assert len(r["daily"]) == 7
    assert r["revenue_total"] == 0
