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


def test_to_usd_converts_try(monkeypatch):
    """TRY -> USD kur ile cevrilir; USD oldugu gibi kalir."""
    monkeypatch.setattr(db, "USD_TRY_RATE", 40.0)
    assert db._to_usd(400.0, "TRY") == 10.0
    assert db._to_usd(10.0, "USD") == 10.0


def test_get_usd_try_rate_fetches_and_caches(monkeypatch):
    """Kur ucretsiz kaynaktan cekilir (rates.TRY)."""
    db._FX_CACHE["rate"] = None
    db._FX_CACHE["at"] = None

    def router(method, url, kw):
        if "open.er-api.com" in url:
            return FakeResp(200, {"rates": {"TRY": 41.5}})
        return FakeResp(200, {})

    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: FakeClient(router))
    assert asyncio.run(db._get_usd_try_rate()) == 41.5


def test_get_usd_try_rate_fallback_on_error(monkeypatch):
    """FX API hata verirse env varsayilanina duser (alarm patlamaz)."""
    db._FX_CACHE["rate"] = None
    db._FX_CACHE["at"] = None
    monkeypatch.setattr(db, "USD_TRY_RATE", 38.0)

    def router(method, url, kw):
        return FakeResp(500, {})

    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: FakeClient(router))
    assert asyncio.run(db._get_usd_try_rate()) == 38.0


def test_low_balance_alert_uses_usd_for_try_provider(monkeypatch):
    """TRY saglayici esik karsilastirmasinda USD karsiligiyla degerlendirilir:
    ~$8 (₺330) SAGLIKLI uyarilmaz; ~$2.5 (₺100) DUSUK uyarilir."""
    _cfg(monkeypatch)
    monkeypatch.setattr(db, "LOW_BALANCE_THRESHOLD_USD", 5.0)

    async def balances():
        return [
            {"provider": "gemini", "currency": "TRY", "remaining": 330.0,
             "remaining_usd": 8.25, "topups": 500, "spend": 170},
            {"provider": "geminilow", "currency": "TRY", "remaining": 100.0,
             "remaining_usd": 2.5, "topups": 100, "spend": 0},
        ]

    async def admins():
        return ["a@x"]

    sent = []

    async def send(to, providers, threshold):
        sent.append([p["provider"] for p in providers])
        return True

    monkeypatch.setattr(db, "get_provider_remaining_balances", balances)
    monkeypatch.setattr(db, "_ticket_admin_emails", admins)
    monkeypatch.setattr(db, "send_low_balance_alert_email", send)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda *a, **k: FakeClient(lambda m, u, kw: FakeResp(200, [])))

    alerted = asyncio.run(db.run_low_balance_alert())
    assert [a["provider"] for a in alerted] == ["geminilow"]
    assert sent == [["geminilow"]]
