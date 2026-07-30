"""Kredi düşümü — çifte-tahsilat kanıtı (denetim #9, KRİTİK).

deduct_credits Supabase REST'e httpx ile gider; burada httpx.AsyncClient
sahtelenip DB'siz test edilir.

⚠️ 2026-07-29 (K1): düşüm TEK atomik RPC'ye (`spend_credits_atomic`) taşındı.
Eskiden üç ayrı çağrı vardı (defter SELECT → bakiye RPC → defter INSERT) ve
idempotanslık uygulama seviyesindeki ön-kontrole dayanıyordu; iki eşzamanlı
çağrı o kontrolü aynı anda geçip bakiyeyi İKİ KEZ düşürebiliyordu. Artık
ikinci ücretlendirmeyi DB kısıtı durduruyor, bu yüzden testler de tek çağrı
üzerinden yazıldı. Kilitlenen davranışlar:
- aynı reference_id ikinci kez gelirse (SQS yeniden teslimi) bakiyeye
  DOKUNULMAZ ama iş ücretli sayılır → True,
- yetersiz bakiye → False (çağıran maliyeti 0'a çeker),
- yapılandırma yoksa fail-closed.
"""
import asyncio

import db


class FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = [] if json_data is None else json_data

    def json(self):
        return self._json

    @property
    def text(self):
        return ""


class FakeClient:
    def __init__(self, router):
        self._router = router
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self._router("GET", url, kw)

    async def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self._router("POST", url, kw)


def _install(monkeypatch, router):
    client = FakeClient(router)
    monkeypatch.setattr(db, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: client)
    return client


def test_deduct_idempotent_noop(monkeypatch):
    """Aynı reference_id ikinci kez: True döner, bakiyeye dokunulmaz.

    Artık bunu DB söylüyor (`reason: duplicate`), uygulama ön-kontrolü değil —
    yarış penceresi bu yüzden kapandı.
    """
    def router(method, url, kw):
        assert method == "POST" and "spend_credits_atomic" in url, f"{method} {url}"
        return FakeResp(200, {"applied": False, "reason": "duplicate"})

    client = _install(monkeypatch, router)
    ok = asyncio.run(db.deduct_credits("u1", 5, "web_audit_private", reference_id="job1"))
    assert ok is True
    # Eski desen geri gelirse bu iddia patlar: ayrı bir bakiye RPC'si olmamalı.
    assert not any("deduct_credits_if_enough" in u for _, u in client.calls)


def test_deduct_fresh_sufficient(monkeypatch):
    """Önceden düşülmemiş + yeterli bakiye: tek RPC çağrılır, True döner."""
    def router(method, url, kw):
        assert method == "POST" and "spend_credits_atomic" in url, f"{method} {url}"
        return FakeResp(200, {"applied": True, "balance": 95})

    client = _install(monkeypatch, router)
    ok = asyncio.run(db.deduct_credits("u1", 5, "web_audit_private", reference_id="job2"))
    assert ok is True
    assert len(client.calls) == 1, "düşüm tek HTTP çağrısında bitmeli (atomiklik)"


def test_deduct_insufficient(monkeypatch):
    """Bakiye yetmezse False — RPC defter satırını da geri almış olur."""
    def router(method, url, kw):
        return FakeResp(200, {"applied": False, "reason": "insufficient"})

    _install(monkeypatch, router)
    ok = asyncio.run(db.deduct_credits("u1", 5, "web_audit_private", reference_id="job3"))
    assert ok is False


def test_deduct_fails_closed_without_config(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "")
    ok = asyncio.run(db.deduct_credits("u1", 5, "x", reference_id="j"))
    assert ok is False
