"""Golden-payload sozlesme testi (A4-3, QA 2026-07-19).

Bu döngünün TEK kritik bug'i resolved_identity/needs_niche'in payload'dan
dusmesiydi — mantik hatasi degil, SOZLESME KAYMASI: hicbir sey kirilmadan
ozellik sessizce oldu. Bu test o sinifi commit aninda yakalar: client'in
okudugu her anahtar (BRAND_CLIENT_KEYS) build_brand_payload ciktisinda VAR mi?
"""
from result_contract import (
    BRAND_CLIENT_KEYS, SOV_CLIENT_KEYS, SOV_OPTIONAL_CLIENT_KEYS, build_brand_payload,
)


def _sample_result():
    """brand_recall.check_brand_recall'in dondurdugu ham dict'in temsili ornegi
    (canli @garyvee taramasinin sekli)."""
    return {
        "recognized": True, "recognition_count": 4, "score": 81,
        "score_legacy": 77, "scoring_version": "v4-sov",
        "identity_mismatch": False, "match_score": None,
        "score_breakdown": {"claude": 76.5, "chatgpt": 72.6, "gemini": 83.3},
        "model_results": {"claude": {"recognized": True}},
        "google_result_count": 12,
        "performing_topics": [], "opportunity_topics": [],
        "checked": True, "raw_list": None,
        "resolved_identity": {"name": "Gary Vaynerchuk", "platform": "youtube"},
        "needs_niche": False,
        "sov": {"checked": True, "score": 82.8, "mention_count": 4, "query_count": 5,
                "engines_used": ["perplexity", "claude"], "queries": [],
                "competitors": [{"name": "@nogood.io", "mentions": 2}],
                "sources": [], "citation_gap": [{"domain": "clutch.co", "mentions": 1}],
                "own_cited_count": 0},
    }


def test_brand_payload_has_all_client_keys():
    payload = build_brand_payload(_sample_result(), "@garyvee",
                                  "entrepreneurship", {"trend": []}, "2026-07-19T00:00:00")
    missing = BRAND_CLIENT_KEYS - set(payload.keys())
    assert not missing, f"payload'dan dusen client anahtarlari: {missing}"


def test_social_fields_survive_payload():
    """Bu turun regresyonunun birebir testi: resolved_identity/needs_niche gecmeli."""
    payload = build_brand_payload(_sample_result(), "@garyvee", "x", {}, "t")
    assert payload["resolved_identity"] == {"name": "Gary Vaynerchuk", "platform": "youtube"}
    assert payload["needs_niche"] is False


def test_sov_subcontract_keys_present():
    payload = build_brand_payload(_sample_result(), "@garyvee", "x", {}, "t")
    sov = payload.get("sov") or {}
    missing = SOV_CLIENT_KEYS - set(sov.keys())
    assert not missing, f"sov'dan dusen client anahtarlari: {missing}"


def test_opsiyonel_sov_anahtarlari_zorunluyla_cakismaz():
    """
    Opsiyonel alan zorunlu listeye SIZMAMALI: sizarsa golden test uretimde
    hicbir zaman dolmayan bir alani "dusmus" sayip surekli kirmizi yanar ya da
    (fixture'a eklenirse) yalan soyler. Iki kume ayrik kalmali.
    """
    assert not (SOV_CLIENT_KEYS & SOV_OPTIONAL_CLIENT_KEYS)


def test_ai_overview_alani_tasiniyor_ama_zorunlu_degil():
    """
    ai_overview kimlik varsa payload'a GECMELI (sessizce dusen alan = olu ozellik,
    resolved_identity bug'inin aynisi), kimlik yoksa YOKLUGU normal olmali.
    """
    r = _sample_result()
    r["sov"]["ai_overview"] = {"aio_present_count": 3, "aio_presence_rate": 1.0,
                               "brand_mention_count": 0, "brand_mention_rate": 0.0,
                               "queries": [], "top_cited_domains": []}
    with_aio = build_brand_payload(r, "@garyvee", "x", {}, "t")
    assert with_aio["sov"]["ai_overview"]["aio_present_count"] == 3

    without = build_brand_payload(_sample_result(), "@garyvee", "x", {}, "t")
    assert "ai_overview" not in (without.get("sov") or {})
    # ...ve yoklugu golden testi KIRMAMALI
    assert not (SOV_CLIENT_KEYS - set((without.get("sov") or {}).keys()))


def test_missing_source_field_defaults_not_crash():
    """brand_recall bir alani hic dondurmezse payload cokmeden guvenli default verir."""
    payload = build_brand_payload({}, "x", None, None, "t")
    assert payload["recognized"] is False
    assert payload["score"] == 0
    assert payload["needs_niche"] is False
    assert BRAND_CLIENT_KEYS - set(payload.keys()) == set()


def test_engines_unavailable_musteriye_tasiniyor():
    """Saglayici kesintisi uyarisi (web <EngineNotice> + mobil engine_notice) bu
    alani okur. brand_recall uretiyordu ama payload'a tasinmiyordu: 2026-07-30
    olcumunde 90 gunluk 378 taramanin 0'inda vardi -> uyari HIC gosterilmedi.
    Bu test o sessiz dususu commit aninda yakalar."""
    ham = _sample_result()
    ham["engines_unavailable"] = ["ChatGPT", "Gemini"]
    payload = build_brand_payload(ham, "@garyvee", "x", {}, "t")
    assert payload["engines_unavailable"] == ["ChatGPT", "Gemini"]
    # Alan hic uretilmediginde de client kirilmasin: bos liste, None degil.
    ham.pop("engines_unavailable")
    assert build_brand_payload(ham, "@garyvee", "x", {}, "t")["engines_unavailable"] == []
