"""Sitemap kesfi de toplam sure sinirina tabi (2026-07-26).

Uretimde PHASE_TIMING logu crawl fazinin 311sn surdugu bir tarama gosterdi;
CRAWL_TOTAL_TIMEOUT 90sn olmasina ragmen. Sebep: sinir yalnizca crawl
DONGUSUNDE kontrol ediliyordu, robots+sitemap kesfi kapsam disindaydi
(1 kok + 10 alt sitemap x 10sn timeout = ~110sn, tek kontrol yapilmadan).
"""
import asyncio
import time

import crawler


class _Yanit:
    def __init__(self, text, status=200):
        self.status_code, self.text = status, text


KOK = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
""" + "".join(f"<sitemap><loc>https://x.test/s{i}.xml</loc></sitemap>" for i in range(10)) + "</sitemapindex>"

ALT = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://x.test/a</loc></url></urlset>"""


def _kur(monkeypatch, alt_gecikme=0.0):
    cagri = {"n": 0}

    async def sahte_get(client, url, timeout=10):
        if url.endswith("/sitemap.xml"):
            return _Yanit(KOK)
        cagri["n"] += 1
        if alt_gecikme:
            await asyncio.sleep(alt_gecikme)
        return _Yanit(ALT)

    monkeypatch.setattr(crawler, "safe_get", sahte_get)
    return cagri


def test_butce_bitince_alt_sitemapler_KESILIR(monkeypatch):
    """Deadline gecmisse dongu durur — 10 alt sitemap'in hepsi cekilmez."""
    cagri = _kur(monkeypatch, alt_gecikme=0.02)
    # Butce zaten dolmus: ilk alt sitemap'ten sonra kesilmeli
    r = asyncio.run(crawler.fetch_sitemap(None, "https://x.test", limit=200,
                                          deadline=time.monotonic() - 1))
    assert cagri["n"] <= 1, f"butce dolu ama {cagri['n']} alt sitemap cekildi"
    assert isinstance(r.get("urls"), list)


def test_deadline_yoksa_eski_davranis(monkeypatch):
    """Geriye uyum: deadline verilmezse hepsi cekilir (varsayilan None)."""
    cagri = _kur(monkeypatch)
    asyncio.run(crawler.fetch_sitemap(None, "https://x.test", limit=200))
    assert cagri["n"] == 10


def test_butce_varsa_ama_dolmadiysa_devam(monkeypatch):
    cagri = _kur(monkeypatch)
    asyncio.run(crawler.fetch_sitemap(None, "https://x.test", limit=200,
                                      deadline=time.monotonic() + 60))
    assert cagri["n"] == 10


def test_kesilse_bile_toplanan_URLler_KAYBOLMAZ(monkeypatch):
    """Yarim kesif bos donmemeli — elde ne varsa onunla devam edilir."""
    cagri = _kur(monkeypatch)
    r = asyncio.run(crawler.fetch_sitemap(None, "https://x.test", limit=200,
                                          deadline=time.monotonic() + 60))
    assert len(r["urls"]) == 10 and len(r["lastmods"]) == len(r["urls"])
