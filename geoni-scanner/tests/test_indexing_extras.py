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


def test_index_coverage_brave_absent_is_indexability():
    crawl = _crawl()
    status = {"indexed_count": 4}  # brave_indexed yok -> None
    res = scoring.compute_index_coverage(crawl, status)
    # Fable #6: google_coverage skordan cikti (188/188 olu). brave None -> score = indexability = 100
    assert res["brave_indexed"] is None
    assert res["score"] == 100.0
    # google_coverage teshis icin raporda hala var (skora girmiyor)
    assert res["google_coverage"] == 100.0


def test_index_coverage_brave_indexed_blend():
    crawl = _crawl()
    status = {"indexed_count": 4, "brave_indexed": True}
    res = scoring.compute_index_coverage(crawl, status)
    # indexability*0.75 + brave*0.25 = 100*0.75 + 100*0.25 = 100
    assert res["brave_indexed"] is True
    assert res["score"] == 100.0


def test_index_coverage_brave_not_indexed_lowers_score():
    crawl = _crawl()
    status = {"indexed_count": 4, "brave_indexed": False}
    res = scoring.compute_index_coverage(crawl, status)
    # Fable #6: indexability*0.75 + 0*0.25 = 75 (google artik skora girmiyor)
    assert res["score"] == 75.0


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
