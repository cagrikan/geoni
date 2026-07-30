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


# ── Tarama suresi telemetrisi ────────────────────────────────────────────────
# 2026-07-26: kisi/marka/sosyal taramalarinda sure HIC kaydedilmiyordu
# (audits satiri isin sonunda yaziliyor; completed_at = result_json'daki
# created_at; ikisi de baslangici bilmiyor). "60 saniye mi 2 dakika mi"
# tartismasini veriyle cevaplayabilmek icin duration_ms telemetriye eklendi.

def test_duration_ms_telemetriye_yaziliyor():
    """Kaynakta duration_ms'in telemetry blogunda ve monotonic saatle
    olculdugu sabit — regresyonda sessizce dusmesin."""
    import inspect
    import brand_recall
    kaynak = inspect.getsource(brand_recall.check_brand_recall)
    assert "_baslangic = time.monotonic()" in kaynak, "baslangic damgasi yok"
    assert '"duration_ms"' in kaynak, "duration_ms telemetriye yazilmiyor"
    # monotonic KULLANILMALI: sistem saati geri alinirsa negatif sure cikmasin
    assert "time.time() - _baslangic" not in kaynak


def test_engines_measured_gercek_olcumu_sayar():
    """2026-07-30: metrik `m.get("recognized") is not None` ile hesaplaniyordu;
    recognized her zaman True/False oldugundan sonuc DAIMA kayit sayisiydi (4-5)
    ve saglayici kesintisinde hic dusmuyordu — sessiz bozulmayi yakalamak icin
    kurulan olcut tam da o anda kordu. Kaynak sabiti: gercek `measured` listesi."""
    import inspect
    import brand_recall
    kaynak = inspect.getsource(brand_recall.check_brand_recall)
    assert '"engines_measured": len(measured)' in kaynak, \
        "engines_measured gercek olcum listesini saymiyor"
    # Yalniz KOD satirlarina bak: kalibi aciklayan yorum (neden kirikti) kalsin,
    # ama kodda geri gelirse yakalansin.
    kod = [l.split("#", 1)[0] for l in kaynak.splitlines()]
    assert not any('m.get("recognized") is not None' in l for l in kod), \
        "kirik olcut koda geri gelmis (recognized asla None degil)"


def test_engines_unavailable_payloada_tasiniyor():
    """Kesinti uyarisini web <EngineNotice> + mobil engine_notice okur; alan
    2026-07-30'a kadar payload'a hic tasinmiyordu (90 gun / 378 tarama / 0 kayit)."""
    from result_contract import BRAND_CLIENT_KEYS
    assert "engines_unavailable" in BRAND_CLIENT_KEYS
