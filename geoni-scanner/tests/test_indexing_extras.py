"""
T5 + M6 offline birim testleri:
  - scoring.compute_index_coverage: Brave bacagi devrede/degil harmani.
  - indexing._looks_like_bot_challenge: 403/Cloudflare challenge tespiti (M6).
Ag cagrisi YOK.
"""
import indexing
import scoring


def _crawl(n=4):
    pages = [{"url": f"https://x.com/{i}", "canonical_url": f"https://x.com/{i}"} for i in range(n)]
    return {"domain": "x.com", "pages": pages}


def test_index_coverage_brave_absent_keeps_legacy_weights():
    crawl = _crawl()
    status = {"indexed_count": 4}  # brave_indexed yok -> None
    res = scoring.compute_index_coverage(crawl, status)
    # google_coverage=100, indexability=100 -> 0.5/0.5 = 100
    assert res["brave_indexed"] is None
    assert res["score"] == 100.0


def test_index_coverage_brave_indexed_blend():
    crawl = _crawl()
    status = {"indexed_count": 4, "brave_indexed": True}
    res = scoring.compute_index_coverage(crawl, status)
    # 100*0.4 + 100*0.4 + 100*0.2 = 100
    assert res["brave_indexed"] is True
    assert res["score"] == 100.0


def test_index_coverage_brave_not_indexed_lowers_score():
    crawl = _crawl()
    status = {"indexed_count": 4, "brave_indexed": False}
    res = scoring.compute_index_coverage(crawl, status)
    # 100*0.4 + 100*0.4 + 0*0.2 = 80
    assert res["score"] == 80.0


def test_bot_challenge_403():
    assert indexing._looks_like_bot_challenge(403, {}, "") is True


def test_bot_challenge_cloudflare_body_on_200():
    body = "<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"
    assert indexing._looks_like_bot_challenge(200, {"server": "cloudflare"}, body) is True


def test_bot_challenge_clean_200():
    assert indexing._looks_like_bot_challenge(200, {"server": "nginx"}, "User-agent: *") is False


def test_bot_challenge_429_only_with_cloudflare():
    assert indexing._looks_like_bot_challenge(429, {"server": "cloudflare"}, "") is True
    assert indexing._looks_like_bot_challenge(429, {"server": "nginx"}, "") is False
