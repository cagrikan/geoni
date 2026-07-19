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
    # Sosyal (@handle) akisi — bu ikisinin dusmesi tam da yakalanan KRITIK bug'di:
    "resolved_identity", "needs_niche",
}

# A3-1 (QA 2026-07-19): "golge modda" motorlar — score_breakdown'da OLCULUP gosterilir
# ama agirligi 0 oldugu icin mansete KATILMAZ (gemini entegrasyonu stabilize olana dek).
# Client bunu okuyup "deneysel · skora katilmiyor" etiketi basar; kullanici gemini
# alt-skorunu (or. 83.3) katki saniyordu (QA ORTA bulgusu).
SHADOW_ENGINES = ["gemini"]

# sov alt-sozlesmesi: SovSection (web) + mobil brand sonucu bu alanlari okur.
SOV_CLIENT_KEYS = {
    "checked", "score", "mention_count", "query_count", "engines_used",
    "queries", "competitors", "sources", "citation_gap", "own_cited_count",
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
        "scoring_version": result.get("scoring_version"),
        "score_breakdown": result.get("score_breakdown", {}),
        "model_results": result.get("model_results", {}),
        "google_result_count": result.get("google_result_count", 0),
        "performing_topics": result.get("performing_topics", []),
        "opportunity_topics": result.get("opportunity_topics", []),
        "checked": result.get("checked", False),
        "raw_list": result.get("raw_list"),
        "sov": result.get("sov"),
        # Sosyal (@handle) taramada @handle -> gorunen ad (resolved_identity) +
        # nis-eksik bayragi (needs_niche). Payload'a tasinmayinca sosyal akis olur.
        "resolved_identity": result.get("resolved_identity"),
        "needs_niche": result.get("needs_niche", False),
        # A3-1: golge-mod motorlar (score_breakdown'da var ama skora katkisi 0).
        "shadow_engines": SHADOW_ENGINES,
        "stability": stability,
        "created_at": created_at,
    }
