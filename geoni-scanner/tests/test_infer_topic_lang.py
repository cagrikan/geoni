"""infer_topic KULLANICININ dilinde alan uretmeli.

Kok neden (2026-07-31): prompt Turkce SABITLENMISTI ve fonksiyon `lang` bile
almiyordu. Ingilizce arayuzde sonuc ekrani "Field: AI Görünürlük Optimizasyonu"
gibi Turkce bir deger gosteriyordu; ayni alan kategori sorgularina da
besleniyordu. Ag yok — sahte ask_llm ile yalniz PROMPT'un dili dogrulanir.
"""
import asyncio
import inspect

import sov

SONUCLAR = [
    {"title": "Acme Analytics", "snippet": "AI visibility optimization for brands"},
    {"title": "Acme blog", "snippet": "How LLMs cite sources"},
]


def _yakala():
    """Prompt'u yakalayan sahte LLM: (kayit, ask_llm) doner."""
    kayit = {}

    async def ask_llm(prompt):
        kayit["prompt"] = prompt
        return '{"alan": "ai visibility"}'

    return kayit, ask_llm


def test_lang_parametresi_var():
    # Cagiran taraf dili gecirebilmeli; varsayilan Turkce kalmali (geriye uyum).
    imza = inspect.signature(sov.infer_topic)
    assert "lang" in imza.parameters
    assert imza.parameters["lang"].default == "tr"


def test_en_dilinde_prompt_ingilizce():
    kayit, ask_llm = _yakala()
    alan = asyncio.run(sov.infer_topic("Acme Analytics", SONUCLAR, ask_llm, lang="en"))
    assert alan == "ai visibility"
    p = kayit["prompt"]
    assert "IN ENGLISH" in p
    # Turkce yonergenin izi kalmamali - yoksa model yine Turkce alan dondurur.
    assert "Turkce soyle" not in p
    assert "faaliyet alanini" not in p


def test_tr_dilinde_prompt_turkce_kalir():
    kayit, ask_llm = _yakala()
    asyncio.run(sov.infer_topic("Acme Analytics", SONUCLAR, ask_llm, lang="tr"))
    p = kayit["prompt"]
    assert "Turkce soyle" in p
    assert "IN ENGLISH" not in p


def test_varsayilan_turkce():
    kayit, ask_llm = _yakala()
    asyncio.run(sov.infer_topic("Acme Analytics", SONUCLAR, ask_llm))
    assert "Turkce soyle" in kayit["prompt"]


def test_enjeksiyon_uyarisi_iki_dilde_de_duruyor():
    # Guvenlik yonergesi dil degistirirken DUSMEMELI (prompt injection savunmasi).
    for dil, imza in (("en", "UNTRUSTED EXTERNAL DATA"), ("tr", "GUVENILMEYEN DIS VERIDIR")):
        kayit, ask_llm = _yakala()
        asyncio.run(sov.infer_topic("Acme Analytics", SONUCLAR, ask_llm, lang=dil))
        assert imza in kayit["prompt"], dil


def test_cagiran_taraf_dili_geciriyor():
    # brand_recall icindeki tek cagri dili GECIRMELI; yoksa duzeltme oluyor gibi
    # gorunur ama uretimde hic devreye girmez.
    kaynak = open("brand_recall.py", encoding="utf-8").read()
    assert "infer_topic(name, _sanitize_web_results(web_results), _ask_aux, lang)" in kaynak
