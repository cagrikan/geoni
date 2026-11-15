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
    patch = calls[1]
    assert patch.method == "PATCH" and "/rest/v1/audits" in str(patch.url)
    assert "status.not.in.%28queued%2Ccrawling%2Cindexing%2Cscoring%2Cpartial%2Cdeleted%29" in str(patch.url)
    body = __import__("json").loads(patch.content)
    assert body["status"] == "deleted"
    assert all(body[k] is None for k in ("domain", "name", "role", "company", "location", "topic", "linkedin_url", "score", "result_json"))
    assert "user_id" not in body and "created_at" not in body and "credits_spent" not in body
    assert calls[2].method == "PATCH" and "created_at=lt." in str(calls[2].url)
    assert calls[3].method == "PATCH" and "/rest/v1/watchlist" in str(calls[3].url)


def test_clear_history_rejects_active_scan_before_any_mutation(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=[{"id": "running-job"}])

    _mock_client(monkeypatch, respond)
    assert asyncio.run(db.clear_user_history("owner-123")) == "active"
    assert len(calls) == 1 and calls[0].method == "GET"


def test_clear_history_fails_closed_when_check_fails(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503)

    _mock_client(monkeypatch, respond)
    assert asyncio.run(db.clear_user_history("owner-123")) == "error"
    assert len(calls) == 1 and calls[0].method == "GET"


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
