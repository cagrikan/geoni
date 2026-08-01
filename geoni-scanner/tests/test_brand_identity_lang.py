"""infer_brand_identity KULLANICININ dilinde ad+alan uretmeli.

Kok neden (2026-08-01): 07-31'de sov.infer_topic dile duyarli yapildi ama
Ingilizce tarama HALA "Field: AI Görünürlük Optimizasyonu" gosteriyordu.
Sebep: ekrandaki alan (result_json brand_recall.inferred_topic) sov.infer_topic'ten
DEGIL, infer_brand_identity'den geliyor — o fonksiyonun prompt'u Turkce sabitti
ve `lang` bile almiyordu. Canli kanit: audit 742f6334 (lang=en) ->
inferred_topic="AI Görünürlük Optimizasyonu".

Ag yok — sahte _ask_claude ile yalniz PROMPT'un dili ve cagiranlarin dili
gecirdigi dogrulanir.
"""
import asyncio
import inspect

import brand_recall

SAYFALAR = [
    {"title": "Acme Analytics", "h1": "AI visibility", "meta_description": "Track how LLMs see your brand"},
    {"title": "Acme blog", "h1": "Guides", "meta_description": "How LLMs cite sources"},
]


def _yakala(monkeypatch, yanit="MARKA: Acme\nALAN: ai visibility tracking"):
    """Prompt'u yakalayan sahte Claude cagrisi: kayit sozlugu doner."""
    kayit = {}

    async def sahte(prompt, temperature=0, max_tokens=200):
        kayit["prompt"] = prompt
        return yanit

    monkeypatch.setattr(brand_recall, "_ask_claude", sahte)
    monkeypatch.setattr(brand_recall, "ANTHROPIC_API_KEY", "test-key")
    return kayit


def test_lang_parametresi_var():
    imza = inspect.signature(brand_recall.infer_brand_identity)
    assert "lang" in imza.parameters
    assert imza.parameters["lang"].default == "tr"


def test_en_dilinde_prompt_ingilizce(monkeypatch):
    kayit = _yakala(monkeypatch)
    sonuc = asyncio.run(brand_recall.infer_brand_identity("acme.com", SAYFALAR, "en"))
    assert sonuc == {"name": "Acme", "topic": "ai visibility tracking"}
    p = kayit["prompt"]
    assert "IN ENGLISH" in p
    # Turkce yonergenin izi kalmamali - yoksa model yine Turkce alan dondurur.
    assert "faaliyet alanı" not in p
    assert "Sadece şu formatta" not in p


def test_tr_dilinde_prompt_turkce_kalir(monkeypatch):
    kayit = _yakala(monkeypatch)
    asyncio.run(brand_recall.infer_brand_identity("acme.com", SAYFALAR, "tr"))
    p = kayit["prompt"]
    assert "faaliyet alanı" in p
    assert "IN ENGLISH" not in p


def test_varsayilan_turkce(monkeypatch):
    kayit = _yakala(monkeypatch)
    asyncio.run(brand_recall.infer_brand_identity("acme.com", SAYFALAR))
    assert "faaliyet alanı" in kayit["prompt"]


def test_ad_alan_anahtarlari_iki_dilde_de_ayni(monkeypatch):
    # Cikti regex'i MARKA:/ALAN: ariyor; Ingilizce prompt'ta anahtarlar
    # cevrilirse ad/alan sessizce fallback'e duser (domain adi alan olur).
    for dil in ("tr", "en"):
        kayit = _yakala(monkeypatch)
        asyncio.run(brand_recall.infer_brand_identity("acme.com", SAYFALAR, dil))
        assert "MARKA:" in kayit["prompt"], dil
        assert "ALAN:" in kayit["prompt"], dil


def test_ad_tahmini_yasagi_iki_dilde_de_duruyor(monkeypatch):
    # "Kategoriyi marka ADINDAN tahmin etme" yonergesi (daktilo vakasi) dil
    # degistirirken DUSMEMELI.
    for dil, imza in (("tr", "marka ADINDAN tahmin etme"), ("en", "not guess the field from the brand NAME")):
        kayit = _yakala(monkeypatch)
        asyncio.run(brand_recall.infer_brand_identity("acme.com", SAYFALAR, dil))
        assert imza in kayit["prompt"], dil


def test_cagiranlar_dili_geciriyor():
    # Duzeltme uretimde devreye girsin diye HER cagiran dili gecirmeli.
    ana = open("main.py", encoding="utf-8").read()
    assert 'infer_brand_identity(request.domain, crawl_result.get("pages", []), request.lang)' in ana
    izleme = open("monitor.py", encoding="utf-8").read()
    assert 'infer_brand_identity(domain, crawl_result.get("pages", []), item_lang)' in izleme
