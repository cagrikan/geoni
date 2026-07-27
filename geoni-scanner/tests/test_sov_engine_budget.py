"""SOV motorlarinin "akil yurutme butceyi yiyor" sinifi (2026-07-27 canli olcum).

IKI MOTOR AYNI HATAYA DUSMUSTU: hem gpt-5 hem gemini-2.5-flash akil yurutme/dusunme
token'larini metinle AYNI butceden harciyor. Butce yetmeyince metin 0 karakter kaliyor
ve motor sessizce olcumden dusuyor — ama faturasi kesiliyor. Cozum ikisinde de ayni:
akil yurutmeyi kis, butceyi metne birak.

--- ChatGPT (gpt-5 + web_search) ---

IKI AYRI HATA ust uste binmisti:
  1) `reasoning.effort` verilmeyince gpt-5 max_output_tokens'in TAMAMINI akil
     yurutmeye harciyor -> status=incomplete, uretilen metin 0 karakter.
     Olcum: butce 4000 -> 3392 reasoning token, 0 karakter metin, 136 saniye.
     effort="low" ile ayni prompt -> 58s, completed, 5546 karakter, 17 atif,
     reasoning 1216 token. Yani duzeltme hem calisiyor hem DAHA UCUZ.
  2) httpx transport istisnalarinin str(e)'si BOS. Log satiri
     "OpenAI web query failed: " diye cikiyordu; 60sn ReadTimeout'lar bu yuzden
     haftalarca gorunmedi. Canli veri: chatgpt SOV yanit orani %14.5
     (claude %98.4, perplexity %95.7).

--- Google (gemini-2.5-flash + google_search grounding) ---
`thinking` VARSAYILAN ACIK ve maxOutputTokens'i metinle paylasiyor. Zor promptlarda
butceyi bitirip finishReason=MAX_TOKENS'a dusuruyor, kod da motoru komple atliyordu.
Canli veri: google SOV yanit orani %42.9. Olcum (ayni prompt):
  dusunme acik, butce 1500 -> 2519 karakter,  8 atif, 276 dusunme token
  thinkingBudget=0,   1500 -> 4209 karakter, 12 atif,   0 dusunme token
  dusunme acik, butce 4000 -> 2500 karakter,  7 atif  (butce buyutmek ISE YARAMIYOR)

Bu testler her iki regresyonu da yakalar.
"""
import asyncio

import httpx
import pytest

import brand_recall as b


def test_hata_bos_istisnada_tipi_yazar():
    """httpx zaman asimlarinin str()'i bos — log en azindan TIPI gostermeli."""
    for E in (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout,
              httpx.RemoteProtocolError, httpx.ReadError):
        e = E("")
        assert str(e) == "", "varsayim degisti: httpx istisnasi artik mesaj tasiyor"
        assert b._hata(e) == E.__name__


def test_hata_mesajli_istisnada_tip_ve_mesaj():
    assert b._hata(ValueError("bozuk json")) == "ValueError: bozuk json"


def _cagri_govdesi(monkeypatch):
    """_ask_openai_web'i sahte HTTP ile calistirip gonderilen JSON govdesini doner."""
    yakalanan = {}

    class _Yanit:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "completed", "output": [
                {"type": "message", "content": [
                    {"type": "output_text", "text": "cevap", "annotations": []}]}]}

    async def sahte_post(client, url, **kw):
        yakalanan["json"] = kw.get("json")
        yakalanan["timeout"] = kw.get("timeout")
        return _Yanit()

    monkeypatch.setattr(b, "_post_retry", sahte_post)
    monkeypatch.setattr(b, "OPENAI_API_KEY", "test-anahtar")
    # provider_usage yazimi testte ag/DB'ye gitmesin
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(b, "log_provider_call", _noop)

    sonuc = asyncio.run(b._ask_openai_web("soru"))
    return yakalanan, sonuc


def test_web_cagrisi_reasoning_low_gonderir(monkeypatch):
    """effort dusurulmezse gpt-5 butceyi reasoning'e yiyip BOS metin doner."""
    govde, sonuc = _cagri_govdesi(monkeypatch)
    assert govde["json"]["reasoning"] == {"effort": "low"}, \
        "reasoning.effort dustu -> ChatGPT SOV motoru yine bos donmeye baslar"
    assert sonuc["text"] == "cevap"


def test_web_cagrisi_effort_minimal_olmamali(monkeypatch):
    """OpenAI: 'minimal' web_search ile KULLANILAMAZ (HTTP 400)."""
    govde, _ = _cagri_govdesi(monkeypatch)
    assert govde["json"]["reasoning"]["effort"] != "minimal"


def test_web_cagrisi_zaman_asimi_olculen_sureyi_karsilar(monkeypatch):
    """Olculen gercek sure effort=low ile ~58s; 60sn sinir tam kenardaydi."""
    govde, _ = _cagri_govdesi(monkeypatch)
    assert govde["timeout"] >= 120


def test_web_cagrisi_butce_metne_yer_birakir(monkeypatch):
    govde, _ = _cagri_govdesi(monkeypatch)
    assert govde["json"]["max_output_tokens"] >= 4000


# --- Google grounded (gemini-2.5-flash) ---

def _gemini_govdesi(monkeypatch, finish="STOP"):
    yakalanan = {}

    class _Yanit:
        status_code = 200

        @staticmethod
        def json():
            return {"candidates": [{
                "finishReason": finish,
                "content": {"parts": [{"text": "cevap"}]},
                "groundingMetadata": {"groundingChunks": [
                    {"web": {"title": "ornek.com", "uri": "https://ornek.com"}}]},
            }]}

    async def sahte_post(client, url, **kw):
        yakalanan["json"] = kw.get("json")
        return _Yanit()

    monkeypatch.setattr(b, "_post_retry", sahte_post)
    monkeypatch.setattr(b, "GOOGLE_API_KEY", "test-anahtar")

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(b, "log_provider_call", _noop)

    sonuc = asyncio.run(b._ask_gemini_grounded("soru"))
    return yakalanan, sonuc


def test_gemini_dusunme_kapali(monkeypatch):
    """thinkingBudget=0 dusunce butcenin TAMAMI metne gider (2519->4209 karakter)."""
    govde, sonuc = _gemini_govdesi(monkeypatch)
    gc = govde["json"]["generationConfig"]
    assert gc["thinkingConfig"] == {"thinkingBudget": 0}, \
        "dusunme geri acildi -> google SOV motoru yine MAX_TOKENS'a dusmeye baslar"
    assert sonuc["text"] == "cevap"
    assert sonuc["citations"] == ["ornek.com"]


def test_gemini_butce_metne_yeter(monkeypatch):
    govde, _ = _gemini_govdesi(monkeypatch)
    assert govde["json"]["generationConfig"]["maxOutputTokens"] >= 2500


def test_gemini_max_tokens_hala_atlanir(monkeypatch):
    """Butce yine de biterse yarim metinle olcum yapma — 'yanitlamadi' daha durust.

    Kirpilmis metin markayi ANMADAN bitmis olabilir; bunu 'anilmadi' saymak yanlis
    negatif uretir. None donunce motor o sorguda olcumden duser ve agirlik
    kalan motorlara yeniden dagitilir (durust eksiklik).
    """
    _, sonuc = _gemini_govdesi(monkeypatch, finish="MAX_TOKENS")
    assert sonuc is None
