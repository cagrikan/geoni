"""Saglayici saglik alarmi — 429+KOTA ayrimi.

2026-07-24: OpenAI kredisi bitti. Kota tukenmesi HTTP **429** ile geliyor
(`insufficient_quota`), gecici hiz siniriyla ayni kod. Eski kural "429 asla
alarm degil" diyordu -> ALARM HIC CALMADI, en agirlikli motor (openai .28)
iki gun sessizce olcumden dustu. Bu testler o ayrimi kilitler.
"""
from brand_recall import _is_provider_health_failure, MODEL_LABELS


OPENAI_KOTA = ('{"error":{"message":"You exceeded your current quota, please check '
               'your plan and billing details.","type":"insufficient_quota",'
               '"code":"insufficient_quota"}}')
OPENAI_HIZ = ('{"error":{"message":"Rate limit reached for gpt-4o in organization '
              'org-x on requests per min","type":"requests","code":"rate_limit_exceeded"}}')


def test_429_kota_ALARM_URETIR():
    """Canli vakanin birebir govdesi — bu alarm uretmeliydi, uretmiyordu."""
    assert _is_provider_health_failure(429, OPENAI_KOTA) is True


def test_429_gercek_hiz_siniri_alarm_URETMEZ():
    """Gecici 429 hala gurultu sayilir; her yogun anda mail atmasin."""
    assert _is_provider_health_failure(429, OPENAI_HIZ) is False
    assert _is_provider_health_failure(429, "") is False


def test_kimlik_ve_odeme_hatalari_alarm_uretir():
    for kod in (401, 402, 403):
        assert _is_provider_health_failure(kod, "") is True


def test_400_kredi_hatasi_alarm_uretir():
    assert _is_provider_health_failure(400, "insufficient credit balance") is True
    assert _is_provider_health_failure(400, "gecersiz parametre") is False


def test_normal_yanit_alarm_uretmez():
    assert _is_provider_health_failure(200, "ok") is False
    assert _is_provider_health_failure(500, "server error") is False


def test_govde_None_cokmez():
    assert _is_provider_health_failure(429, None) is False


def test_model_etiketleri_musteriye_uygun():
    """engines_unavailable MUSTERIYE gider — ic anahtar degil okunur ad."""
    assert MODEL_LABELS["openai"] == "ChatGPT"
    assert MODEL_LABELS["claude"] == "Claude"
    assert set(MODEL_LABELS) >= {"claude", "openai", "gemini", "perplexity", "grok"}
