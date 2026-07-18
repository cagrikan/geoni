"""citation_placement deterministik hedef listesi (2.4) — LLM/ağ YOK, offline."""
import ticket_automation as T


def _audit(citation_gap=None, sources=None, competitors=None, queries=None, checked=True):
    return {"type": "web", "lang": "tr", "result_json": {"sov": {
        "checked": checked, "queries": queries or [],
        "citation_gap": citation_gap or [], "sources": sources or [],
        "competitors": competitors or []},
        "brand_recall": {"inferred_name": "Örnek", "inferred_topic": "konu"}}}


def test_none_when_no_sov():
    assert T.generate_citation_targets(None) is None
    assert T.generate_citation_targets(_audit(checked=False)) is None


def test_none_when_no_targets():
    # sov var ama own dışı kaynak/gap yok → None
    a = _audit(sources=[{"domain": "kendi.com", "mentions": 5, "own": True}])
    assert T.generate_citation_targets(a) is None


def test_own_domains_excluded():
    a = _audit(sources=[{"domain": "kendi.com", "mentions": 5, "own": True},
                        {"domain": "hedef.com", "mentions": 2, "own": False}])
    out = T.generate_citation_targets(a)
    assert "hedef.com" in out
    assert "kendi.com" not in out


def test_gap_prioritized_over_sources():
    a = _audit(citation_gap=[{"domain": "gap.com", "mentions": 1, "own": False}],
               sources=[{"domain": "src.com", "mentions": 9, "own": False}])
    out = T.generate_citation_targets(a)
    # gap satırı "rakip anıldı, siz yoksunuz" gerekçesiyle görünür
    assert "gap.com" in out and "rakip anıldı" in out


def test_semi_auto_wiring():
    assert "citation_placement" in T.SEMI_AUTO_KEYS
    # AUTO değil (yerleştirme insan işi → submitted yapılmaz)
    assert "citation_placement" not in T.AUTO_FULFILL_KEYS


def test_query_domain_map():
    sov = {"queries": [{"query": "q1", "engines": {"perplexity": {"sources": ["a.com", "b.com"]}}},
                       {"query": "q2", "engines": {"google": {"sources": ["a.com"]}}}]}
    m = T._query_domain_map(sov)
    assert m["a.com"] == ["q1", "q2"]
    assert m["b.com"] == ["q1"]
