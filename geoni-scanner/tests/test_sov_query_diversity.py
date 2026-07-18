"""
T9 offline birim testleri: sorgu niyet cesitliligi.
  - _is_recommendation_query yeni alternatif/karsilastirma cue'larini kabul eder.
  - generate_category_queries, uretici modelin dondurdugu alternatif/karsilastirma
    sorularini oneri-filtresinde ELEMEZ (LLM sahte, ag yok).
"""
import asyncio

import sov


def test_recommendation_cues_accept_alternatives_tr():
    assert sov._is_recommendation_query("Semrush alternatifleri neler?")
    assert sov._is_recommendation_query("Ahrefs yerine ne kullanmali?")
    assert sov._is_recommendation_query("A ile B'yi karşılaştır")


def test_recommendation_cues_accept_comparison_en():
    assert sov._is_recommendation_query("What are the best alternatives to Notion?")
    assert sov._is_recommendation_query("Compare Asana vs Trello")


def test_method_question_still_rejected():
    # Yontem sorusu hala elenmeli (yanlis pozitif uretmedik)
    assert not sov._is_recommendation_query("SEO nasıl yapılır adım adım?")


def test_generate_queries_keeps_comparison_intent():
    payload = (
        '{"komsu_alan": "icerik pazarlama", "queries": ['
        '{"soru": "En iyi SEO araci hangisi?", "alan": "birincil"},'
        '{"soru": "Semrush alternatifleri nelerdir?", "alan": "birincil"},'
        '{"soru": "Ahrefs ile Semrush karşılaştırması nasıl?", "alan": "birincil"},'
        '{"soru": "İçerik pazarlama için hangi firmaları önerirsin?", "alan": "komsu"},'
        '{"soru": "İçerik ajansı alternatifleri kimler?", "alan": "komsu"}'
        ']}'
    )

    async def fake_llm(prompt):
        return payload

    out = asyncio.run(sov.generate_category_queries("Acme", "SEO araci", fake_llm))
    qs = [q["query"] for q in out]
    # Alternatif/karsilastirma sorulari filtreden gecti (elenmedi)
    assert any("alternatif" in q.lower() for q in qs)
    assert any("karşılaştır" in q.lower() for q in qs)
    # Birincil sorgu tavanina uyar
    assert sum(1 for q in out if not q.get("adjacent")) <= sov.SOV_QUERY_COUNT
