"""Crawl 0-sayfa kok-neden fix kilidi (derin test 2026-07-22, 🔴 bulgu).

Eskiden crawl bot-block/JS/timeout ile 0 sayfa donunce check_indexing_status
tum httpx kontrollerini atliyor, ai_access sahte-100 + bot_protection_suspected
sahte-False kaliyordu. Bu testler dogru davranisi sabitler:
- compute_ai_access_score: olculemedi (bos dict) -> 1.0 DEGIL 0.5,
- check_indexing_status: pages bos + domain var -> kontroller KOSAR + bot_protection True,
- ne pages ne domain -> gercekten tam-bos (kontrol kosmaz).
"""
import asyncio

import indexing
from scoring import compute_ai_access_score


def test_ai_access_unmeasured_is_conservative():
    idx = {"bot_access": {"arama": {}, "egitim": {}}, "llms_txt": False}
    r = compute_ai_access_score(idx, {"sitemap_found": False})
    assert r["arama_ratio"] == 0.5   # eski: 1.0 (sahte-sisme)
    assert r["egitim_ratio"] == 0.5
    assert r["measured"] is False
    assert r["score"] == 44.0        # eski: 88.0


def test_ai_access_measured_uses_real_ratio():
    idx = {"bot_access": {"arama": {"gptbot": True, "ccbot": True}, "egitim": {"claudebot": True}},
           "llms_txt": True, "bot_protection_suspected": False}
    r = compute_ai_access_score(idx, {"sitemap_found": True})
    assert r["arama_ratio"] == 1.0
    assert r["egitim_ratio"] == 1.0
    assert r["measured"] is True


def test_indexing_zero_pages_still_checks(monkeypatch):
    calls = []

    async def fake_google(d, **k):
        calls.append("google"); return 3

    async def fake_robots(d):
        calls.append("robots")
        return {"arama": {"gptbot": True}, "egitim": {"gptbot": True},
                "robots_found": True, "bot_protection_suspected": False}

    async def fake_llms(d):
        calls.append("llms"); return True

    async def fake_brave(d, b=""):
        calls.append("brave"); return {"brave_indexed": True}

    monkeypatch.setattr(indexing, "check_google_indexed", fake_google)
    monkeypatch.setattr(indexing, "check_robots_ai_access", fake_robots)
    monkeypatch.setattr(indexing, "check_llms_txt", fake_llms)
    monkeypatch.setattr(indexing, "check_brave_index", fake_brave)
    monkeypatch.setattr(indexing, "assert_public_host", lambda h: None)

    res = asyncio.run(indexing.check_indexing_status([], domain="example.com"))
    assert res["bot_protection_suspected"] is True          # crawl_blocked isaretlendi
    assert res["bot_access"]["robots_found"] is True         # kontrol KOSTU (eski: hep False)
    assert res["google"] == 3
    assert {"google", "robots", "llms", "brave"} == set(calls)


def test_indexing_no_domain_no_pages_is_empty(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("domain yokken hicbir kontrol cagrilmamali")

    monkeypatch.setattr(indexing, "check_google_indexed", boom)
    res = asyncio.run(indexing.check_indexing_status([], domain=""))
    assert res["bot_protection_suspected"] is False
    assert res["bot_access"]["robots_found"] is False
    assert res["google"] == 0
