"""
free_scan.free_scan_gate + record_free_scan karar mantigi (max guvenlik:
cihaz + hesap). Bagimliliklar (db + devicecheck) mock'lanir; saf mantik test.
pytest-asyncio'ya bagimli olmamak icin asyncio.run() ile cagrilir.
"""
import asyncio

import pytest

import free_scan


def _install(monkeypatch, *, premium=False, device_count=0, account_used=0):
    """Katmanlari sahte async fonksiyonlarla degistirir; cagri kayitlarini döner."""
    rec = {"inc": [], "setdev": []}

    async def _premium(uid):
        return premium

    async def _acct(uid):
        return account_used

    async def _inc(uid):
        rec["inc"].append(uid)
        return account_used + 1

    async def _qdev(token):
        return device_count

    async def _setdev(token, count):
        rec["setdev"].append((token, count))
        return True

    monkeypatch.setattr(free_scan, "check_is_premium", _premium)
    monkeypatch.setattr(free_scan, "get_free_scans_used", _acct)
    monkeypatch.setattr(free_scan, "increment_free_scans", _inc)
    monkeypatch.setattr(free_scan, "query_device_count", _qdev)
    monkeypatch.setattr(free_scan, "set_device_count", _setdev)
    monkeypatch.setattr(free_scan, "FREE_SCAN_LIMIT", 2)
    return rec


def test_premium_bypasses_cap(monkeypatch):
    rec = _install(monkeypatch, premium=True, device_count=5, account_used=9)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert allowed and info["reason"] == "premium"
    asyncio.run(free_scan.record_free_scan("u1", "devtok", info))
    assert rec["inc"] == [] and rec["setdev"] == []  # premium → record no-op


def test_anon_new_device_allowed_and_increments(monkeypatch):
    rec = _install(monkeypatch, device_count=0)
    allowed, info = asyncio.run(free_scan.free_scan_gate(None, "devtok"))  # anonim
    assert allowed
    asyncio.run(free_scan.record_free_scan(None, "devtok", info))
    assert rec["inc"] == []                       # anonim → hesap sayaci artmaz
    assert rec["setdev"] == [("devtok", 1)]        # cihaz 0→1


def test_device_at_limit_blocks(monkeypatch):
    _install(monkeypatch, device_count=2)          # cihaz tavan
    allowed, info = asyncio.run(free_scan.free_scan_gate(None, "devtok"))
    assert not allowed and info["reason"] == "free_limit_reached"
    assert info["device_over"] and not info["account_over"]


def test_account_at_limit_blocks_even_new_device(monkeypatch):
    _install(monkeypatch, device_count=0, account_used=2)  # cok-cihaz istismarini engeller
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert not allowed and info["account_over"]


def test_device_unknown_safe_side_account_decides(monkeypatch):
    # DeviceCheck env yok → query None → cihaz katmani atlanir, hesap karar verir
    rec = _install(monkeypatch, device_count=None, account_used=1)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", None))
    assert allowed
    asyncio.run(free_scan.record_free_scan("u1", None, info))
    assert rec["inc"] == ["u1"]                     # hesap +1
    assert rec["setdev"] == []                      # cihaz set edilmez (count None)


def test_logged_in_both_increment(monkeypatch):
    rec = _install(monkeypatch, device_count=1, account_used=0)
    allowed, info = asyncio.run(free_scan.free_scan_gate("u1", "devtok"))
    assert allowed
    asyncio.run(free_scan.record_free_scan("u1", "devtok", info))
    assert rec["inc"] == ["u1"]                     # hesap +1
    assert rec["setdev"] == [("devtok", 2)]         # cihaz 1→2
