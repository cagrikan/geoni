"""
O3/O4/O5 offline birim testleri (ag yok; LLM sahte):
  - O3: rakip cikariminda kendi markasi (varyant-toleransli) elenir.
  - O4: AI platformlari/motorlari varyantlariyla (Google Gemini, ChatGPT (OpenAI),
        Microsoft Bing) rakip listesine girmez.
  - O5: mention sayilari LLM'e degil, yanitlara deterministik olarak sayilir.
"""
import asyncio

import sov


def test_denied_competitor_variants():
    # O4: varyantlar (tam eslesme degil) da elenir
    assert sov._is_denied_competitor("Google Gemini")
    assert sov._is_denied_competitor("ChatGPT (OpenAI)")
    assert sov._is_denied_competitor("Microsoft Bing")
    assert sov._is_denied_competitor("perplexity")
    # Gercek marka elenmemeli (yanlis pozitif yok)
    assert not sov._is_denied_competitor("Semrush")
    assert not sov._is_denied_competitor("AI Solutions")  # generik 'ai' takilmamali


def test_own_brand_variant_excluded():
    # O3: 'Acme Yazilim A.S.' markasinin 'Acme Yazilim' varyanti kendi markasidir
    assert sov._is_own_brand("Acme Yazılım", "Acme Yazılım A.Ş.")
    assert sov._is_own_brand("Acme Yazılım A.Ş.", "Acme")
    assert not sov._is_own_brand("Beta Danışmanlık", "Acme Yazılım A.Ş.")


def test_extract_competitors_deterministic_count_and_exclusions():
    answers = [
        "Bu alanda Semrush ve Ahrefs öne çıkıyor.",   # Semrush x1, Ahrefs x1
        "Semrush çok güçlü bir araçtır.",              # Semrush x1 (toplam 2)
        "Google Gemini ve ChatGPT birer AI aracıdır.",  # denylist
    ]

    # LLM: mention sayilarini SISIRIYOR + kendi marka + denylist varyantlari doner
    async def fake_llm(prompt):
        return (
            '{"competitors": ['
            '{"name": "Semrush", "mentions": 9},'         # sisirilmis -> det=2
            '{"name": "Ahrefs", "mentions": 7},'          # sisirilmis -> det=1
            '{"name": "Google Gemini", "mentions": 4},'   # O4 elenir
            '{"name": "ChatGPT (OpenAI)", "mentions": 3},' # O4 elenir
            '{"name": "Acme Yazılım", "mentions": 5}'      # O3 kendi marka elenir
            ']}'
        )

    out = asyncio.run(sov._extract_competitors(answers, "Acme Yazılım A.Ş.", fake_llm))
    names = {c["name"]: c["mentions"] for c in out}
    assert set(names) == {"Semrush", "Ahrefs"}   # denylist + kendi marka elendi
    assert names["Semrush"] == 2                  # O5: deterministik (LLM'in 9'u degil)
    assert names["Ahrefs"] == 1                   # O5: deterministik (LLM'in 7'si degil)
    # Sirali: en cok anilana gore
    assert out[0]["name"] == "Semrush"


def test_fallback_queries_use_location_o6():
    # O6: lokasyon verilince ilk sablon sorgu yerel olur
    tr = sov._fallback_queries("avukatlık", "tr", "Ankara")
    assert "Ankara" in tr[0]
    en = sov._fallback_queries("law services", "en", "Ankara")
    assert "Ankara" in en[0]
    # Lokasyon yoksa eski davranis (Türkiye geneli)
    assert "Türkiye" in sov._fallback_queries("avukatlık", "tr")[0]

