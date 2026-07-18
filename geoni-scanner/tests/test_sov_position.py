"""
T4 offline birim testleri: pozisyon (sira) cikarimi + pozisyon agirligi +
uctan-uca SOV skoruna etkisi. LLM/ag cagrisi YOK (sahte motor + custom_queries).
"""
import asyncio

import sov


def test_position_weight_buckets():
    assert sov._position_weight(1) == 1.0
    assert sov._position_weight(2) == 1.0
    assert sov._position_weight(3) == 0.85
    assert sov._position_weight(5) == 0.85
    assert sov._position_weight(6) == 0.7
    assert sov._position_weight(12) == 0.7
    assert sov._position_weight(None) == 1.0  # olculemedi -> notr


def test_extract_position_numbered_list():
    answer = (
        "En iyi firmalar:\n"
        "1. AlphaCo cok iyi\n"
        "2. Acme guclu\n"
        "3. BetaCorp\n"
    )
    assert sov._extract_position(answer, "Acme") == 2


def test_extract_position_bulleted_ordinal():
    answer = "- AlphaCo\n- BetaCorp\n- Acme Yazilim\n"
    assert sov._extract_position(answer, "Acme") == 3


def test_extract_position_inline_list():
    answer = "Sunlari onerebilirim: AlphaCo, BetaCorp ve Acme."
    assert sov._extract_position(answer, "Acme") == 3


def test_extract_position_none_when_not_mentioned():
    answer = "1. AlphaCo\n2. BetaCorp\n"
    assert sov._extract_position(answer, "Acme") is None


def test_extract_position_none_when_only_in_intro():
    # Marka giris cumlesinde (madde icinde degil) -> top-N onerisi degil, belirsiz
    answer = "Acme gibi markalari soruyorsan iste alternatifler:\n1. AlphaCo\n2. BetaCorp\n"
    assert sov._extract_position(answer, "Acme") is None


def _run(name, responses, own_domain=""):
    async def fake_pplx(query, max_tokens=400):
        return responses.get(query)

    async def fake_llm(prompt):
        return None

    return asyncio.run(sov.check_share_of_voice(
        name, "test alani", fake_pplx, fake_llm,
        custom_queries=list(responses.keys()), own_domain=own_domain,
    ))


def test_sov_top_position_full_weight_vs_low_position():
    # Ayni bahis+atif; tek fark sira. 1. sira tam agirlik, 8. sira 0.7.
    q = "En iyi firma hangisi?"
    top = _run("Acme", {q: {"text": "1. Acme\n2. AlphaCo\n", "citations": []}})
    low_ans = "\n".join(f"{i}. Firma{i}" for i in range(1, 8)) + "\n8. Acme\n"
    low = _run("Acme", {q: {"text": low_ans, "citations": []}})
    assert top["score"] == 100.0
    assert low["score"] == 70.0  # 6+ sira -> 0.7 carpani
    assert top["diagnostics"]["avg_position"] == 1
    assert low["diagnostics"]["avg_position"] == 8
    assert low["diagnostics"]["position_measured"] is True


def test_sov_position_combines_with_citation_weight():
    # Atifsiz (0.7) + geride sira (0.85) -> carpim 0.595 -> 59.5
    q = "En iyi firma hangisi?"
    ans = "1. AlphaCo\n2. BetaCorp\n3. GammaInc\n4. Acme\n"
    res = _run("Acme", {q: {"text": ans, "citations": ["https://other.com/x"]}})
    # own not cited -> cite_w 0.7; position 4 -> pos_w 0.85
    assert res["score"] == 59.5
