"""History clearing must remove reports without resetting creator scan usage."""
import asyncio

import httpx
import pytest

import db
from api_errors import ApiHata


def _mock_client(monkeypatch, responder):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(responder)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda **kw: original(transport=transport, **kw))
    monkeypatch.setattr(db, "SUPABASE_URL", "https://db.example")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "test-key")


def test_clear_history_scrubs_only_owner_and_keeps_usage_rows(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200 if request.method == "GET" else 204, json=[] if request.method == "GET" else None)

    _mock_client(monkeypatch, respond)
    assert asyncio.run(db.clear_user_history("owner-123")) == "ok"
    assert len(calls) == 4
    assert all("user_id=eq.owner-123" in str(r.url) for r in calls)
    stale = calls[0]
    assert stale.method == "PATCH" and "status=eq.queued" in str(stale.url)
    assert "created_at=lt." in str(stale.url)
    patch = calls[2]
    assert patch.method == "PATCH" and "/rest/v1/audits" in str(patch.url)
    assert "status.not.in.%28queued%2Ccrawling%2Cindexing%2Cscoring%2Cpartial%2Cdeleted%29" in str(patch.url)
    body = __import__("json").loads(patch.content)
    assert body["status"] == "deleted"
    assert all(body[k] is None for k in ("domain", "name", "role", "company", "location", "topic", "linkedin_url", "score", "result_json"))
    assert "user_id" not in body and "created_at" not in body and "credits_spent" not in body
    assert calls[3].method == "PATCH" and "/rest/v1/watchlist" in str(calls[3].url)


def test_clear_history_rejects_active_scan_before_any_mutation(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=[{"id": "running-job"}])

    _mock_client(monkeypatch, respond)
    assert asyncio.run(db.clear_user_history("owner-123")) == "active"
    assert len(calls) == 2 and calls[1].method == "GET"
    assert "created_at" not in str(calls[1].url)


def test_clear_history_fails_closed_when_check_fails(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "PATCH":
            return httpx.Response(204)
        return httpx.Response(503)

    _mock_client(monkeypatch, respond)
    assert asyncio.run(db.clear_user_history("owner-123")) == "error"
    assert len(calls) == 2 and calls[1].method == "GET"


@pytest.mark.parametrize("kind", ["web", "brand"])
def test_deleted_report_cannot_leak_from_process_memory(monkeypatch, kind):
    pytest.importorskip("fastapi")
    import main
    job_id = "d64af7cf-911a-43e4-82d1-39058a4f070e"
    async def deleted(_):
        return True
    monkeypatch.setattr(main, "is_deleted_audit", deleted)
    store = main.jobs_store if kind == "web" else main.brand_checks_store
    store[job_id] = {"status": "complete", "result": {"private": "secret"}}
    try:
        fn = main.get_audit_status if kind == "web" else main.get_brand_check_status
        with pytest.raises(ApiHata) as exc:
            asyncio.run(fn(job_id))
        assert exc.value.status_code == 410
        assert job_id not in store
    finally:
        store.pop(job_id, None)


def test_saved_brand_scan_does_not_start_without_pending_row(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import BackgroundTasks
    from starlette.requests import Request
    import main

    async def user(_): return "owner-123"
    async def no(_): return False
    async def yes(_): return True
    async def allowed(*args, **kwargs): return True, {}
    async def pending(*args, **kwargs): return False

    monkeypatch.setattr(main, "get_user_id_from_token", user)
    monkeypatch.setattr(main, "is_user_suspended", no)
    monkeypatch.setattr(main, "check_is_premium", yes)
    monkeypatch.setattr(main, "free_scan_gate", allowed)
    monkeypatch.setattr(main, "create_pending_brand_check", pending)
    monkeypatch.setattr(main, "_is_internal_scan", lambda _: True)
    monkeypatch.setattr(main, "get_client_ip", lambda _: "127.0.0.1")
    req = Request({"type": "http", "method": "POST", "path": "/api/brand-check",
                   "headers": [(b"authorization", b"Bearer token")]})
    tasks = BackgroundTasks()
    before = set(main.brand_checks_store)
    with pytest.raises(main.ApiHata) as exc:
        asyncio.run(main.start_brand_check(main.BrandCheckRequest(name="Test Brand"), tasks, req))
    assert exc.value.status_code == 503
    assert set(main.brand_checks_store) == before
    assert tasks.tasks == []


def test_private_brand_scan_never_creates_history_row(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import BackgroundTasks
    from starlette.requests import Request
    import main

    async def user(_): return "owner-123"
    async def no(_): return False
    async def yes(_): return True
    async def pending(*args, **kwargs):
        raise AssertionError("private scan must not create an audits row")

    monkeypatch.setattr(main, "get_user_id_from_token", user)
    monkeypatch.setattr(main, "is_user_suspended", no)
    monkeypatch.setattr(main, "check_is_premium", yes)
    monkeypatch.setattr(main, "create_pending_brand_check", pending)
    monkeypatch.setattr(main, "_is_internal_scan", lambda _: True)
    monkeypatch.setattr(main, "get_client_ip", lambda _: "127.0.0.1")
    req = Request({"type": "http", "method": "POST", "path": "/api/brand-check",
                   "headers": [(b"authorization", b"Bearer token")]})
    tasks = BackgroundTasks()
    response = asyncio.run(main.start_brand_check(
        main.BrandCheckRequest(name="Test Brand", private=True), tasks, req))
    try:
        assert response.status == "queued"
        assert len(tasks.tasks) == 1
    finally:
        main.brand_checks_store.pop(response.job_id, None)
        main.brand_check_events.pop(response.job_id, None)
