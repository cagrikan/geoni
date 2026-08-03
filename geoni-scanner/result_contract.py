"""API yanit sozlesmesi — TEK KAYNAK (A4-3, QA 2026-07-19).

main.py brand/social sonuc payload'ini eskiden ELLE kuruyordu; bir alan (or.
resolved_identity/needs_niche) unutulunca ozellik SESSIZCE oluyordu (client
gormuyor, hicbir sey kirilmiyor). Bu modul payload'i saf bir fonksiyonda toplar
ve client'larin OKUDUGU anahtarlari (BRAND_CLIENT_KEYS) belgeler; golden-payload
testi (tests/test_result_contract.py) her commit'te "client'in bekledigi anahtar
payload'da var mi" diye assert eder — payload-drop sinifi CI'da yakalanir.

Hafif tutulur (fastapi/playwright CEKMEZ) ki minimal test venv'inde kossun.
"""

# Web SPA (BrandCheckResultsPage/SovSection) + mobil (lib/api.ts BrandResult,
# app/brand/[jobId].tsx) bu anahtarlari okur. Yeni bir client-alani eklenince
# BURAYA da eklenir; payload'dan dusen alan testte patlar.
BRAND_CLIENT_KEYS = {
    "name", "topic", "recognized", "recognition_count", "score",
    "score_breakdown", "model_results", "performing_topics", "opportunity_topics",
    "identity_mismatch", "checked", "scoring_version", "sov",
    # Saglayici kesintisi uyarisi: web (BrandCheckResultsPage -> <EngineNotice>) ve
    # mobil (app/brand/[jobId].tsx -> engine_notice) BU ALANI OKUYOR. brand_recall
    # uretiyordu ama payload'a tasinmiyordu -> 90 gunde 378 taramanin 0'inda vardi,
    # yani kesinti uyarisi HIC gosterilmedi (2026-07-30 olcumu). Bu satir olmadan
    # motor coktugunde kullanici sadece aciklamasiz bir ✗ goruyor.
    "engines_unavailable",
    # Sosyal (@handle) akisi — bu ikisinin dusmesi tam da yakalanan KRITIK bug'di:
    "resolved_identity", "needs_niche",
}

# A3-1 (QA 2026-07-19): "golge modda" motorlar — score_breakdown'da OLCULUP gosterilir
# ama agirligi 0 oldugu icin mansete KATILMAZ. Client bunu okuyup "deneysel · skora
# katilmiyor" etiketi basar.
# NOT (v5, 2026-07-19): gemini golge moddan CIKTI (backtest gecti; bkz. brand_recall
# WEIGHTS notu). Artik agirliga katiliyor -> golge listesi BOS. Ileride yeni bir deney
# motoru eklenirse buraya yazilir; etiketleme altyapisi (client shadow_engines okur) durur.
SHADOW_ENGINES = ["grok"]  # 2026-07-23: Grok (xAI) golge-modda eklendi (WEIGHTS['grok']=0)

# sov alt-sozlesmesi: SovSection (web) + mobil brand sonucu bu alanlari okur.
SOV_CLIENT_KEYS = {
    "checked", "score", "mention_count", "query_count", "engines_used",
    "queries", "competitors", "sources", "citation_gap", "own_cited_count",
}

# OPSIYONEL sov alt-anahtarlari: istemci OKUR ama payload'da OLMAYABILIR.
# Golden-payload testi bunlari "dusmus alan" saymaz -- yoklugu tasarim geregidir.
# Zorunlu listeye koymak testi YALANCI yapar: uretimde kimlik yoksa alan zaten yok.
SOV_OPTIONAL_CLIENT_KEYS = {
    # Google AI Overview (2026-08-02): SOV'un AYNI sorgulari gercek SERP'te de
    # olculur. SOV "sohbet motoru seni aniyor mu", bu "Google aramanin ustundeki
    # AI kutusunda var misin" sorusudur -- farkli yuzeyler, biri otekinin yerine
    # gecmez. SKORA KATILMAZ (WEIGHTS'e dokunulmadi); rapor bolumu olarak gosterilir.
    # Kimlik (DATAFORSEO_*) yoksa anahtar HIC yazilmaz -> istemci bolumu gizler.
    "ai_overview",
}


# ---------- WEB taramasi sozlesmesi (2026-08-02) ----------
# NEDEN SONRADAN EKLENDI: marka akisinda bu sozlesme vardi ve gercek bir bug'i
# yakaladi (resolved_identity/needs_niche sessizce dusmustu). WEB akisinda hic
# yoktu -> 2026-08-02 denetiminde "uretiliyor ama hicbir istemci okumuyor"
# (citability) ve "istemcide var, payload'da yok" (monitor'de ssr/page_type_gap)
# vakalarinin ikisi de ancak elle bakarak bulundu.
#
# Liste OLCULEREK cikarildi (tahminle degil): web `ResultsPage.jsx:161-168` +
# `FixSuggestions:96-140` + `SovSection`, mobil `lib/api.ts:227-250 AuditResult`.
# Yeni alan eklerken: istemci okuyorsa BURAYA da ekle, yoksa test yalan soyler.
WEB_CLIENT_KEYS = {
    "domain", "score", "score_breakdown", "total_pages", "indexed_pages",
    "platforms", "llms_txt", "top_topics", "opportunities", "brand_recall",
    "sov", "sov_pending", "stability", "created_at",
    # 2026-08-02'de eklendi; web `FixSuggestions` bunlari okuyup "neden" cumlesi
    # kuruyor (ResultsPage.jsx:122,131). Mobil'de HENUZ YOK — acik is.
    "ssr", "page_type_gap",
    # 2026-08-03: golge modda uretiliyordu ve HICBIR istemci okumuyordu. Artik
    # rapor bulgusu olarak gosteriliyor (SKORA hala girmiyor — dayandigi
    # arastirmayi bagimsiz dogrulamadik, "shadow": true bayragi bunu soyler).
    "citability",
}

# Istemcinin OKUMADIGI ama payload'da tasinan alanlar. Burada olmak bir SUC
# degil (rapor/otomasyon/oz-gelisim okuyabilir) — ama listede durmasi "kimse
# okumuyorsa neden uretiyoruz" sorusunu gorunur kilar.
WEB_INTERNAL_KEYS = {
    "lang", "scoring_version", "weights_used", "diagnostics", "sitemap_found",
    "site_assets", "bot_access", "pages", "model_results",
}


def build_brand_payload(result: dict, name, topic, stability, created_at: str, lang: str = "tr") -> dict:
    """brand_recall.check_brand_recall sonucundan client payload'i uretir.
    `result` brand_recall'in dondurdugu ham dict; `stability` ve `created_at`
    cagiran tarafindan hesaplanip verilir (bu fonksiyon saf + senkron kalsin).
    `lang` result_json'a gomulur ki 24h idempotent cache (A2-1) DILE gore
    eslessin — yoksa EN sonuc TR istegine servis edilir (yanlis skor)."""
    return {
        "name": name,
        "topic": topic,
        "lang": lang,
        # Kimlik uyusmazliginda arayuz aciklamali ekrani gosterebilsin.
        "identity_mismatch": result.get("identity_mismatch", False),
        "match_score": result.get("match_score"),
        "recognized": result.get("recognized", False),
        "recognition_count": result.get("recognition_count", 0),
        "score": result.get("score", 0),
        "score_legacy": result.get("score_legacy"),
        # Grup B faz-1 golge skor (B6+B7) — manset DEGIL. self_improve shadow_compare
        # sinyali (v4->golge faz-2 gecis karari) bunu result_json'dan okur; eskiden
        # payload'a tasinmadigi icin sinyal HIC uretilmiyordu (olu).
        "score_shadow": result.get("score_shadow"),
        "scoring_version": result.get("scoring_version"),
        "score_breakdown": result.get("score_breakdown", {}),
        "model_results": result.get("model_results", {}),
        "google_result_count": result.get("google_result_count", 0),
        "performing_topics": result.get("performing_topics", []),
        "opportunity_topics": result.get("opportunity_topics", []),
        "checked": result.get("checked", False),
        # Olculemeyen motorlarin OKUNUR adlari (sebep YAZILMAZ — ic bilgi sizmasin;
        # sebep yalnizca loga/admin mailine gider, bkz. _provider_health_alert).
        "engines_unavailable": result.get("engines_unavailable", []),
        "raw_list": result.get("raw_list"),
        "sov": result.get("sov"),
        # Sosyal (@handle) taramada @handle -> gorunen ad (resolved_identity) +
        # nis-eksik bayragi (needs_niche). Payload'a tasinmayinca sosyal akis olur.
        "resolved_identity": result.get("resolved_identity"),
        "needs_niche": result.get("needs_niche", False),
        # A3-1: golge-mod motorlar (score_breakdown'da var ama skora katkisi 0).
        "shadow_engines": SHADOW_ENGINES,
        # A4-6: tarama telemetrisi (internal gozlemlenebilirlik; client okumaz).
        "telemetry": result.get("telemetry"),
        "stability": stability,
        "created_at": created_at,
    }
