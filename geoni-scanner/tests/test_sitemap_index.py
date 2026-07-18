"""
O9 offline birim testleri: sitemap-index destegi + lastmod hizalamasi.
Ag yok — _parse_sitemap_xml saf; fetch_sitemap icin safe_get monkeypatch'lenir.
"""
import asyncio

import crawler


def test_parse_urlset_aligns_lastmod():
    xml = (
        '<?xml version="1.0"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://x.com/a</loc><lastmod>2026-01-02</lastmod></url>'
        '<url><loc>https://x.com/b</loc></url>'  # lastmod YOK -> kaymamali
        '<url><loc>https://x.com/c</loc><lastmod>2025-05-01</lastmod></url>'
        '</urlset>'
    )
    parsed = crawler._parse_sitemap_xml(xml)
    assert parsed["is_index"] is False
    locs = [e["loc"] for e in parsed["entries"]]
    assert locs == ["https://x.com/a", "https://x.com/b", "https://x.com/c"]
    # b'nin lastmod'u None; a/c dogru loc'a hizali (eski ayri-findall kaydiriyordu)
    lm = {e["loc"]: e["lastmod"] for e in parsed["entries"]}
    assert lm["https://x.com/a"] == "2026-01-02"
    assert lm["https://x.com/b"] is None
    assert lm["https://x.com/c"] == "2025-05-01"


def test_parse_sitemapindex_detected():
    xml = (
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<sitemap><loc>https://x.com/sitemap1.xml</loc></sitemap>'
        '<sitemap><loc>https://x.com/sitemap2.xml</loc></sitemap>'
        '</sitemapindex>'
    )
    parsed = crawler._parse_sitemap_xml(xml)
    assert parsed["is_index"] is True
    assert [e["loc"] for e in parsed["entries"]] == [
        "https://x.com/sitemap1.xml", "https://x.com/sitemap2.xml",
    ]


class _FakeResp:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


def test_fetch_sitemap_resolves_index(monkeypatch):
    """O9: fetch_sitemap bir index dosyasi gorunce alt-sitemap'leri cozer;
    eskiden alt-sitemap URL'lerini SAYFA sanip crawl ediyordu."""
    index_xml = (
        '<sitemapindex><sitemap><loc>https://x.com/sub1.xml</loc></sitemap>'
        '<sitemap><loc>https://x.com/sub2.xml</loc></sitemap></sitemapindex>'
    )
    sub1 = ('<urlset><url><loc>https://x.com/p1</loc>'
            '<lastmod>2026-02-02</lastmod></url></urlset>')
    sub2 = '<urlset><url><loc>https://x.com/p2</loc></url></urlset>'
    pages = {
        "https://x.com/sitemap.xml": index_xml,
        "https://x.com/sub1.xml": sub1,
        "https://x.com/sub2.xml": sub2,
    }

    async def fake_safe_get(client, url, timeout=10, **kw):
        return _FakeResp(pages[url])

    monkeypatch.setattr(crawler, "safe_get", fake_safe_get)
    result = asyncio.run(crawler.fetch_sitemap(None, "https://x.com/"))
    assert result["found"] is True
    assert result["urls"] == ["https://x.com/p1", "https://x.com/p2"]
    # lastmod urls ile 1:1 hizali (eksik olan "" placeholder)
    assert result["lastmods"] == ["2026-02-02", ""]
