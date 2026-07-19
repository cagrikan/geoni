"""Turnstile anti-abuse — offline (fastapi/main.py YOK, minimal venv'de kosar).
Happy-path DEGIL: fail/ag-hatasi/blok/403/hard-mode/EN-mesaj/remoteip dahil."""
import asyncio
import types

import turnstile as T


def _run(coro):
    return asyncio.run(coro)


def _client(success=None, raise_exc=False, captured=None):
    """httpx.AsyncClient sahtesi: post() {success} doner ya da patlar; data'yi kaydeder."""
    class C:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, data=None):
            if captured is not None:
                captured.update(data or {})
            if raise_exc:
                raise RuntimeError("net down")
            return types.SimpleNamespace(json=lambda: {"success": success})
    return lambda *a, **k: C()


# ── verify_turnstile ──
def test_verify_no_secret_soft_allows(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET", raising=False)
    assert _run(T.verify_turnstile("anytoken")) is True


def test_verify_secret_but_no_token_false(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    assert _run(T.verify_turnstile("")) is False


def test_verify_success_true(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.setattr(T.httpx, "AsyncClient", _client(success=True))
    assert _run(T.verify_turnstile("tok")) is True


def test_verify_failure_false(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.setattr(T.httpx, "AsyncClient", _client(success=False))
    assert _run(T.verify_turnstile("tok")) is False


def test_verify_network_error_soft_allows(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.setattr(T.httpx, "AsyncClient", _client(raise_exc=True))
    assert _run(T.verify_turnstile("tok")) is True


def test_verify_remoteip_only_when_known(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    cap = {}
    monkeypatch.setattr(T.httpx, "AsyncClient", _client(success=True, captured=cap))
    _run(T.verify_turnstile("tok", "unknown"))
    assert "remoteip" not in cap
    _run(T.verify_turnstile("tok", "1.2.3.4"))
    assert cap.get("remoteip") == "1.2.3.4"


# ── check_turnstile (soft-rollout) ──
def test_check_no_token_soft_allows(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.delenv("TURNSTILE_ENFORCE", raising=False)
    assert _run(T.check_turnstile(None, "ip", "tr", "e")) == (False, None)


def test_check_valid_token_passes(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.setattr(T.httpx, "AsyncClient", _client(success=True))
    assert _run(T.check_turnstile("tok", "ip", "tr", "e")) == (False, None)


def test_check_invalid_token_blocks(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.setattr(T.httpx, "AsyncClient", _client(success=False))
    blocked, msg = _run(T.check_turnstile("bad", "ip", "tr", "e"))
    assert blocked is True and msg and "Doğrulama" in msg


def test_check_invalid_token_en_message(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.setattr(T.httpx, "AsyncClient", _client(success=False))
    _, msg = _run(T.check_turnstile("bad", "ip", "en", "e"))
    assert "Verification failed" in msg


def test_check_hard_mode_blocks_missing_token(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.setenv("TURNSTILE_ENFORCE", "1")
    blocked, msg = _run(T.check_turnstile("", "ip", "tr", "e"))
    assert blocked is True and msg


def test_check_no_secret_soft_allows_even_hard(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET", raising=False)
    monkeypatch.setenv("TURNSTILE_ENFORCE", "1")
    assert _run(T.check_turnstile("", "ip", "tr", "e")) == (False, None)


def test_check_hard_mode_valid_token_passes(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET", "s")
    monkeypatch.setenv("TURNSTILE_ENFORCE", "1")
    monkeypatch.setattr(T.httpx, "AsyncClient", _client(success=True))
    assert _run(T.check_turnstile("tok", "ip", "tr", "e")) == (False, None)
