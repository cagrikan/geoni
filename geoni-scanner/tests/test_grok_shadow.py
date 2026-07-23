"""_ask_grok (Grok / xAI SHADOW recall motoru) sozlesme testi.

Grok golge-modda eklendi: brand_recall WEIGHTS['grok']=0, canli manseti
DEGISTIRMEZ; yalniz offline backtest icin per-engine skoru toplanir. Bu test,
xAI /chat/completions OpenAI-uyumlu yanit seklinden metnin dogru cikarildigini
ve (anahtar yok / HTTP hata) durumlarinda sessizce None dondugunu — motorun
'olculemedi' gibi ele alinmasi — commit aninda dogrular. GERCEK ANAHTAR GEREKMEZ.
"""
import asyncio
import brand_recall as b


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


async def _noop(*a, **k):
    return None


def test_grok_parses_openai_compatible_response(monkeypatch):
    """200 + OpenAI-uyumlu govde -> choices[0].message.content (strip'li) doner;
    dogru URL/Authorization/json_object modu gonderilir."""
    monkeypatch.setattr(b, "GROK_API_KEY", "test-key")
    monkeypatch.setattr(b, "log_provider_call", _noop)

    captured = {}

    async def _fake_post_retry(client, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        captured["body"] = kwargs.get("json", {})
        return _FakeResp(200, {"choices": [{"message": {"content": '  {"taniyor": true}  '}}]})

    monkeypatch.setattr(b, "_post_retry", _fake_post_retry)

    out = asyncio.run(b._ask_grok("selam", json_mode=True))
    assert out == '{"taniyor": true}'
    assert captured["url"] == "https://api.x.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["model"] == b.GROK_MODEL


def test_grok_no_key_returns_none(monkeypatch):
    """Anahtar yoksa hic cagri yapmadan None -> motor sessizce olculmez."""
    monkeypatch.setattr(b, "GROK_API_KEY", "")
    assert asyncio.run(b._ask_grok("selam")) is None


def test_grok_http_error_returns_none(monkeypatch):
    """5xx/4xx -> None (saglik alarmi tetiklenir, cagiran 'olculemedi' sayar)."""
    monkeypatch.setattr(b, "GROK_API_KEY", "test-key")
    monkeypatch.setattr(b, "_provider_health_alert", _noop)

    async def _fake_post_retry(client, url, **kwargs):
        return _FakeResp(500, text="upstream boom")

    monkeypatch.setattr(b, "_post_retry", _fake_post_retry)
    assert asyncio.run(b._ask_grok("selam")) is None
