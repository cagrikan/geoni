"""Kredi düşümü — çifte-tahsilat kanıtı (denetim #9, KRİTİK).

deduct_credits Supabase REST'e httpx ile gider; burada httpx.AsyncClient
sahtelenip DB'siz test edilir. Kilitlenen davranışlar:
- reference_id (job_id) için 'spend' zaten varsa TEKRAR DÜŞME (SQS yeniden
  teslimi = çifte tahsilat açığının kanıtı),
- atomik RPC boş dönerse (yetersiz bakiye) False,
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
    """Aynı reference_id için önceden 'spend' varsa: no-op True, RPC ÇAĞRILMAZ."""
    def router(method, url, kw):
        if method == "GET" and "credit_transactions" in url:
            return FakeResp(200, [{"id": "existing-spend"}])
        raise AssertionError(f"idempotent no-op'ta RPC/insert çağrılmamalı: {method} {url}")

    client = _install(monkeypatch, router)
    ok = asyncio.run(db.deduct_credits("u1", 5, "web_audit_private", reference_id="job1"))
    assert ok is True
    assert not any("deduct_credits_if_enough" in u for _, u in client.calls)


def test_deduct_fresh_sufficient(monkeypatch):
    """Önceden düşülmemiş + yeterli bakiye: RPC çağrılır, True döner."""
    def router(method, url, kw):
        if method == "GET" and "credit_transactions" in url:
            return FakeResp(200, [])
        if method == "POST" and "deduct_credits_if_enough" in url:
            return FakeResp(200, [True])
        return FakeResp(201, [{"id": "txn"}])

    client = _install(monkeypatch, router)
    ok = asyncio.run(db.deduct_credits("u1", 5, "web_audit_private", reference_id="job2"))
    assert ok is True
    assert any("deduct_credits_if_enough" in u for _, u in client.calls)


def test_deduct_insufficient(monkeypatch):
    """Atomik RPC boş liste dönerse (yetersiz bakiye) False."""
    def router(method, url, kw):
        if method == "GET" and "credit_transactions" in url:
            return FakeResp(200, [])
        if method == "POST" and "deduct_credits_if_enough" in url:
            return FakeResp(200, [])
        return FakeResp(201, [])

    _install(monkeypatch, router)
    ok = asyncio.run(db.deduct_credits("u1", 5, "web_audit_private", reference_id="job3"))
    assert ok is False


def test_deduct_fails_closed_without_config(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "")
    ok = asyncio.run(db.deduct_credits("u1", 5, "x", reference_id="j"))
    assert ok is False
