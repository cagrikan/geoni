"""Retention / veri saklama temizligi testleri.

_delete_attachment_files_for_tickets (storage yol kurulumu + toplu silme) ve
run_low_balance_alert (esik-alti tetikleme + gunluk debounce) httpx sahtelenip
DB/storage'siz test edilir. Proje kalibi: asyncio.run + FakeClient router
(pytest-asyncio'ya bagimli degil).
"""
import asyncio
from datetime import datetime, timezone

import db


class FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = [] if json_data is None else json_data
        self.text = ""

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
        self.calls.append(("GET", url, kw.get("json")))
        return self._router("GET", url, kw)

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw.get("json")))
        return self._router("POST", url, kw)

    async def patch(self, url, **kw):
        self.calls.append(("PATCH", url, kw.get("json")))
        return self._router("PATCH", url, kw)

    async def delete(self, url, **kw):
        self.calls.append(("DELETE", url, kw.get("json")))
        return self._router("DELETE", url, kw)

    async def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        return self._router(method, url, kw)


def _cfg(monkeypatch):
    monkeypatch.setattr(db, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "svc")


def test_delete_attachment_files_builds_full_paths(monkeypatch):
    """list -> {tid}/{name} tam yollariyla toplu DELETE; sayac dogru."""
    _cfg(monkeypatch)

    def router(method, url, kw):
        if method == "POST" and "/object/list/" in url:
            return FakeResp(200, [{"name": "a1_f.pdf"}, {"name": "b2_g.png"}])
        if method == "DELETE":
            return FakeResp(200, {})
        return FakeResp(200, [])

    client = FakeClient(router)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: client)
    deleted = asyncio.run(db._delete_attachment_files_for_tickets(client, [49]))
    assert deleted == 2
    dels = [c for c in client.calls if c[0] == "DELETE"]
    assert dels and dels[0][2]["prefixes"] == ["49/a1_f.pdf", "49/b2_g.png"]


def test_delete_attachment_files_empty_ticket_noop(monkeypatch):
    """Dosyasiz bilette DELETE cagrilmaz, 0 doner (yetim istek yok)."""
    _cfg(monkeypatch)

    def router(method, url, kw):
        if method == "POST" and "/object/list/" in url:
            return FakeResp(200, [])
        raise AssertionError(f"bos bilette DELETE olmamali: {method} {url}")

    client = FakeClient(router)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: client)
    assert asyncio.run(db._delete_attachment_files_for_tickets(client, [7])) == 0


def test_low_balance_alert_only_below_threshold(monkeypatch):
    """Esik alti saglayici uyarilir, ustundeki uyarilmaz; e-posta admin'e gider."""
    _cfg(monkeypatch)
    monkeypatch.setattr(db, "LOW_BALANCE_THRESHOLD_USD", 5.0)

    async def balances():
        return [{"provider": "openai", "remaining": 2.5, "topups": 50, "spend": 47.5},
                {"provider": "perplexity", "remaining": 40.0, "topups": 50, "spend": 10}]

    async def admins():
        return ["admin@geoni.ai"]

    sent = []

    async def send(to, providers, threshold):
        sent.append((to, [p["provider"] for p in providers]))
        return True

    monkeypatch.setattr(db, "get_provider_remaining_balances", balances)
    monkeypatch.setattr(db, "_ticket_admin_emails", admins)
    monkeypatch.setattr(db, "send_low_balance_alert_email", send)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: FakeClient(lambda m, u, kw: FakeResp(200, [])))

    alerted = asyncio.run(db.run_low_balance_alert())
    assert [a["provider"] for a in alerted] == ["openai"]
    assert sent == [("admin@geoni.ai", ["openai"])]


def test_low_balance_alert_debounced_same_day(monkeypatch):
    """Ayni saglayici bugun zaten uyarildiysa tekrar e-posta ATILMAZ (debounce)."""
    _cfg(monkeypatch)
    monkeypatch.setattr(db, "LOW_BALANCE_THRESHOLD_USD", 5.0)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def balances():
        return [{"provider": "openai", "remaining": 2.5, "topups": 50, "spend": 47.5}]

    async def admins():
        return ["admin@geoni.ai"]

    sent = []

    async def send(to, providers, threshold):
        sent.append(to)
        return True

    monkeypatch.setattr(db, "get_provider_remaining_balances", balances)
    monkeypatch.setattr(db, "_ticket_admin_emails", admins)
    monkeypatch.setattr(db, "send_low_balance_alert_email", send)

    def router(method, url, kw):
        if method == "GET" and "app_config" in url:
            return FakeResp(200, [{"value": db.json.dumps({"openai": today})}])
        return FakeResp(200, [])

    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: FakeClient(router))
    alerted = asyncio.run(db.run_low_balance_alert())
    assert alerted == []   # bugun zaten uyarildi -> yeni uyari yok
    assert sent == []      # e-posta atilmadi
