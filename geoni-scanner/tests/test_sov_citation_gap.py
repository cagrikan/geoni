"""
T3 offline birim testleri: citation_gap metrigi + atifli-bahis skor agirligi.

LLM/ag cagrisi YOK — sahte motor fonksiyonlari ({"text","citations"} dondurur)
ve custom_queries ile calisir (generate_category_queries atlanir; rakip
cikarimi da ask_llm=None ile bos doner). Amac: sov.py'nin skor+gap mantigini
belirlenimci dogrulamak.
"""
import asyncio

import sov


def _run(name, queries, responses, own_domain=""):
    """responses: {query -> {"text","citations"}} sahte perplexity motoru."""
    async def fake_pplx(query, max_tokens=400):
        return responses.get(query)

    async def fake_llm(prompt):
        return None  # rakip cikarimini bos birak (offline)

    return asyncio.run(sov.check_share_of_voice(
        name, "test alani", fake_pplx, fake_llm,
        custom_queries=queries, own_domain=own_domain,
    ))


def test_citation_gap_rakip_anan_domainleri_listeler():
    # q1: marka anilir + kendi sitesi atif alir -> gap degil, brand_domain
    # q2: marka anilmaz + rakip kaynaklar atif alir -> citation_gap
    res = _run(
        "Acme",
        ["Acme icin en iyi firma hangisi?", "Bu alanda kimleri onerirsin genel?"],
        {
            "Acme icin en iyi firma hangisi?": {
                "text": "Acme bu alanda one cikan bir firmadir.",
                "citations": ["https://acme.com/hakkinda"],
            },
            "Bu alanda kimleri onerirsin genel?": {
                "text": "Rakip1 ve Rakip2 gibi secenekler var.",
                "citations": ["https://rival.com/liste", "https://blog.example.com/en-iyiler"],
            },
        },
        own_domain="acme.com",
    )
    gap_domains = {g["domain"] for g in res["citation_gap"]}
    assert gap_domains == {"rival.com", "blog.example.com"}
    # Kendi domaini ve marka-yaniti domainleri gap'te olmamali
    assert "acme.com" not in gap_domains
    assert res["diagnostics"]["citation_gap_domains"] == 2
    assert set(res["diagnostics"]["citation_gap_examples"]) == gap_domains
    # 2 sorgu yanitli, 1'i marka-anan + kendi-atifli (agirlik 1.0) -> 1/2 = 50
    assert res["score"] == 50.0


def test_atifsiz_bahis_0_7_agirlik():
    # Marka anilir ama KENDI sitesi atif almaz (baska kaynak atif alir) ->
    # atifsiz bahis 0.7. Tek sorgu yanitli -> skor 70.
    res = _run(
        "Acme",
        ["Acme onerir misin bu alanda?"],
        {
            "Acme onerir misin bu alanda?": {
                "text": "Evet, Acme iyi bir secenek.",
                "citations": ["https://baska-site.com/yazi"],
            },
        },
        own_domain="acme.com",
    )
    assert res["score"] == 70.0
    assert res["diagnostics"]["citation_weighting"] is True
    # Marka anilan yanitin kaynagi gap sayilmaz
    assert res["citation_gap"] == []


def test_atif_yoksa_notr_1_0():
    # Hicbir motor atif dondurmez -> agirlik notr (1.0), herkes 0.7'ye cekilmez.
    # Marka anilir, atif yok -> skor 100 (cezasiz).
    res = _run(
        "Acme",
        ["Acme onerir misin bu alanda?"],
        {
            "Acme onerir misin bu alanda?": {
                "text": "Evet, Acme iyi bir secenek.",
                "citations": [],
            },
        },
        own_domain="acme.com",
    )
    assert res["score"] == 100.0
    assert res["diagnostics"]["citation_weighting"] is False
    assert res["citation_gap"] == []
