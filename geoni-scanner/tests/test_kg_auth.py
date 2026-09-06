"""KG credential separation and unavailable-vs-absent behavior, no network."""
import asyncio
import importlib

import httpx
import pytest
import scoring


def test_gemini_key_is_not_a_kg_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-only-fixture")
    monkeypatch.delenv("GOOGLE_KG_API_KEY", raising=False)
    importlib.reload(scoring)
    try:
        def forbidden_client(*args, **kwargs):
            pytest.fail("KG must not call the network without its own key")
        monkeypatch.setattr(scoring.httpx, "AsyncClient", forbidden_client)
        assert asyncio.run(scoring._google_kg_presence("Google")) is None
    finally:
        monkeypatch.undo()
        importlib.reload(scoring)


@pytest.mark.parametrize("status,payload,expected", [
    (200, {"itemListElement": [{"result": {"name": "Google"}, "resultScore": 100}]}, {"present": True, "score": 100.0}),
    (200, {"itemListElement": []}, {"present": False, "score": 0.0}),
    (400, {"error": {"message": "kg-secret-fixture"}}, None),
    (403, {}, None),
    (429, {}, None),
    (500, {}, None),
])
def test_kg_uses_dedicated_header_and_preserves_unknown(monkeypatch, caplog, status, payload, expected):
    key = "kg-secret-fixture"
    monkeypatch.setattr(scoring, "GOOGLE_KG_API_KEY", key)
    real_client = httpx.AsyncClient
    def handle(request):
        assert request.headers["x-goog-api-key"] == key
        assert "key" not in request.url.params
        assert request.url.params["query"] == "Google"
        return httpx.Response(status, json=payload)
    monkeypatch.setattr(scoring.httpx, "AsyncClient", lambda: real_client(transport=httpx.MockTransport(handle)))
    assert asyncio.run(scoring._google_kg_presence("Google")) == expected
    assert key not in caplog.text


def test_kg_exception_does_not_log_credentials(monkeypatch, caplog):
    key = "kg-secret-fixture"
    monkeypatch.setattr(scoring, "GOOGLE_KG_API_KEY", key)
    real_client = httpx.AsyncClient
    def handle(request):
        raise httpx.ReadTimeout(key, request=request)
    monkeypatch.setattr(scoring.httpx, "AsyncClient", lambda: real_client(transport=httpx.MockTransport(handle)))
    assert asyncio.run(scoring._google_kg_presence("Google")) is None
    assert key not in caplog.text
