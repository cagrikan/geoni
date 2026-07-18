"""content_package deterministik konu seçimi (2.3) — LLM/ağ YOK, offline."""
import ticket_automation as T


def _audit(sov_queries=None, opportunities=None, top_topics=None, competitors=None,
           atype="web"):
    return {"type": atype, "lang": "tr", "result_json": {
        "sov": {"checked": True, "queries": sov_queries or [],
                "competitors": competitors or []},
        "opportunities": opportunities or [],
        "top_topics": top_topics or [],
        "brand_recall": {"inferred_name": "Örnek", "inferred_topic": "konu"}}}


def test_gaps_only_mentioned_false_primary():
    a = _audit(sov_queries=[
        {"query": "hedef sorgu", "mentioned": False, "adjacent": False},
        {"query": "anıldı", "mentioned": True, "adjacent": False},
        {"query": "komşu", "mentioned": False, "adjacent": True},  # adjacent → hariç
    ])
    sel = T._select_content_topics(a)
    assert sel["gaps"] == ["hedef sorgu"]


def test_opportunities_dedup_and_str_or_dict():
    a = _audit(opportunities=[{"topic": "A"}, "B", {"topic": "A"}, "B"])
    sel = T._select_content_topics(a)
    assert sel["opportunities"] == ["A", "B"]


def test_has_material_false_when_empty():
    assert T._content_has_material(T._select_content_topics(None)) is False
    assert T._content_has_material(T._select_content_topics(_audit())) is False


def test_has_material_true_with_any_signal():
    a = _audit(top_topics=[{"topic": "güçlü"}])
    assert T._content_has_material(T._select_content_topics(a)) is True


def test_content_package_in_auto_fulfill_keys():
    assert "content_package" in T.AUTO_FULFILL_KEYS
    # content_package DOMAIN_ONLY değil (isim/@handle hedefinde de çalışır)
    from db import DOMAIN_ONLY_SERVICE_KEYS
    assert "content_package" not in DOMAIN_ONLY_SERVICE_KEYS


def test_competitors_extracted():
    a = _audit(competitors=[{"name": "DentX", "mentions": 3}, {"name": "SmileCo"}])
    sel = T._select_content_topics(a)
    assert sel["competitors"] == ["DentX", "SmileCo"]
