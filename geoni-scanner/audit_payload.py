"""
Web taramasi sonuc payload'inin TEK insa noktasi.

NEDEN AYRI MODUL (2026-08-02): bu payload iki yerde elle kopyalanmisti —
`main._build_audit_result_payload` (kullanicinin baslattigi tarama) ve
`monitor._scan_web_item` (15 gunlук otomatik izleme taramasi). Kopyalardan biri
guncellenince digeri SESSIZCE sapti ve bu gercekten yasandi:

  🪤 `platforms.google` YANLIS BULGUSU. main.py'de duzeltildi (Google SONUC
  SAYISI degil, bot-izni BOOLEAN'i okunmali) ama monitor.py'de eski satir kaldi.
  Sonuc: izleme taramasi "Gemini sitenize erisemiyor" yanlis bulgusunu ve yaninda
  "Hizmete git" butonunu GERI GETIRECEKTI — olmayan bir soruna hizmet satisi.
  Ayni sapmada monitor payload'inda `ssr`/`citability`/`page_type_gap` da hic
  yoktu: SSR cezasi skoru dusururken kullanici SEBEBINI goremiyordu.

monitor.py main'i import EDEMEZ (dairesel bagimlilik + fastapi). Bu yuzden ortak
insa buraya alindi: iki cagiran da ayni fonksiyondan gecer, sapma yapisal olarak
imkansiz hale gelir. `tests/test_audit_payload.py` bunu commit aninda korur.
"""
from datetime import datetime

from stability import build_stability


def sxo_uyumsuzlugu(crawl_result: dict, sov_payload: dict | None) -> dict | None:
    """AI'in alintiladigi sayfa tipleri ile musterinin tipleri arasindaki fark.
    Iki taraftan biri olculemezse None (ozellik sessizce yok sayilir)."""
    try:
        import source_type
        aio = ((sov_payload or {}).get("ai_overview") or {})
        alinti = [r for q in (aio.get("queries") or []) for r in (q.get("references") or [])]
        return source_type.karsilastir(crawl_result.get("pages") or [], alinti)
    except Exception:
        return None


async def build_audit_result_payload(*, domain: str, lang: str | None, crawl_result: dict,
                                     indexing_status: dict, brand_recall_result: dict,
                                     score_result: dict, topics: dict, identity: dict,
                                     auto_monitor: bool = False,
                                     ic_dogrulama: bool = False) -> dict:
    """Musteriye donen web tarama sonucu.

    Hem ARA (partial, SOV beklenirken) hem FINAL sonuc hem de izleme taramasi
    AYNI fonksiyonla kurulur -> payload'lar asla birbirinden sapmaz.
    `auto_monitor=True` yalnizca rapor basliginda "otomatik izleme taramasi"
    rozetini acar; alanlarin geri kalani birebir aynidir.
    """
    payload = {
        "domain": domain,
        "lang": lang or "tr",  # BUG-5: schema inLanguage doğru dil (hep "tr" değil)
        "score": score_result["overall_score"],
        "score_breakdown": score_result["breakdown"],
        "scoring_version": score_result.get("scoring_version"),
        "weights_used": score_result.get("weights_used"),
        "diagnostics": score_result.get("diagnostics"),
        "total_pages": crawl_result["total_pages"],
        "sitemap_found": crawl_result.get("sitemap_found"),  # B-4: llms/robots sitemap kanıtı
        "site_assets": crawl_result.get("site_assets"),  # P1: schema logo/sameAs
        # SSR korlugu (2026-08-02): AI botlari JS calistirmaz, bizim tarayicimiz
        # calistirir. Icerik yalnizca JS sonrasi olusuyorsa bot bos kabuk goruyor.
        # ai_access skoruna zaten kesinti olarak girdi; burada rapor METNI icin
        # tasiniyor ("AI botlari bu sayfanin %X'ini goremiyor").
        "ssr": crawl_result.get("ssr"),
        # Alintilanabilirlik/yapi: GOLGE MOD (shadow: true). Skora GIRMEZ;
        # istemci "deneysel · skora katilmiyor" etiketiyle gosterir.
        "citability": crawl_result.get("citability"),
        # SXO uyumsuzlugu (GOLGE MOD): AI'in alintiladigi sayfa TIPLERI ile
        # musterinin sahip oldugu tipleri karsilastirir. "Sema ekle" gibi genel
        # oneri yerine EKSIK SAYFA TIPI verir: "AI %53 karsilastirma listesi
        # alintiliyor, senin bu tipte tek sayfan yok."
        "page_type_gap": sxo_uyumsuzlugu(crawl_result, brand_recall_result.get("sov")),
        "indexed_pages": indexing_status["indexed_count"],
        "platforms": {
            # Not: bu alanlar artik ARAMA/ALINTILANMA botlarina (OAI-SearchBot,
            # ChatGPT-User, Claude-SearchBot, PerplexityBot...) gore hesaplaniyor,
            # yalnizca egitim crawler'larina (GPTBot vb.) gore degil (Madde 2.5).
            "chatgpt": indexing_status.get("openai", False),
            "anthropic": indexing_status.get("anthropic", False),
            "perplexity": indexing_status.get("perplexity", False),
            # YANLIS BULGU DUZELTMESI (2026-08-02): burada eskiden
            # indexing_status["google"] vardi -- o Google SONUC SAYISI (int),
            # digerleri ise bot-izni boolean'i. Arayuz hepsini boolean sanip
            # `!platforms.google` yaziyordu; 0 sonuc = "Gemini sitenize
            # erisemiyor" diye okunuyordu. geoni.ai'nin kendi robots.txt'inde
            # `Google-Extended: Allow: /` OLMASINA RAGMEN rapor "Gemini Bot
            # Izni: Hayir" basti ve olmayan bir soruna HIZMET onerdi.
            "google": indexing_status.get("google_bot_allowed", True),
        },
        "bot_access": indexing_status.get("bot_access", {}),
        "llms_txt": indexing_status.get("llms_txt", False),
        "top_topics": topics["performing_topics"],
        "opportunities": topics["opportunity_topics"],
        # llms.txt otomasyonu (bilet sistemi) icin sayfa ozeti - sadece
        # baslik+aciklamasi olan sayfalar, ilk 20 - ham crawl_result'un
        # tamami degil, uzun taramalarda result_json'u sismesin diye.
        "pages": [
            {"url": p.get("url"), "title": p.get("title"), "meta_description": p.get("meta_description")}
            for p in crawl_result.get("pages", []) if p.get("title")
        ][:20],
        "brand_recall": {
            "checked": brand_recall_result.get("checked", False),
            "recognized": brand_recall_result.get("recognized", False),
            "score": brand_recall_result.get("score"),
            "score_legacy": brand_recall_result.get("score_legacy"),
            "scoring_version": brand_recall_result.get("scoring_version"),
            "inferred_name": identity["name"],
            "inferred_topic": identity["topic"],
            # B3 (derin test 2026-07-22): zengin teshis audits'e yaziliyordu ATILIYORDU
            # -> "neden bu skor" sorusu kayitlardan cevaplanamiyordu. model_results
            # (motor bazli dogruluk/celiski/uydurma), score_breakdown ve telemetry
            # (sessiz bozulmayi SQL'le izlemek icin) artik result_json'a gomulur.
            # (retention zaten superseded/eski result_json'lari sadelestiriyor.)
            "model_results": brand_recall_result.get("model_results"),
            "score_breakdown": brand_recall_result.get("score_breakdown"),
            "telemetry": brand_recall_result.get("telemetry"),
        },
        # F1: 4-motor model_results TOP-LEVEL'da da durur. self_improve bazi
        # sinyalleri (self_improve.py:440,456 — motor kalitesi/eskalasyon)
        # YALNIZCA top-level'dan okur; ic-ice tek kopya birakilirsa o sinyaller
        # web taramalarinda sessizce bos kalir. Marka payload'i (result_contract)
        # da top-level yazar — iki akis ayni sekilde okunabilsin.
        "model_results": brand_recall_result.get("model_results") or {},
        # v3: Share of Voice — markayi bilmeyen kullanicinin kategori
        # sorgularinda gorunurluk + ayni yanitlardan cikarilan rakipler.
        "sov": brand_recall_result.get("sov"),
        # Progressive result (Fable 2026-07-24): SOV henuz gelmediyse True.
        # Ara (partial) yazimda True, final yazimda alan hic yoktur/False.
        "sov_pending": bool(brand_recall_result.get("sov_pending")),
        # Skor istikrari: yumusatilmis skor + degisim kaynagi (oynaklik notu)
        "stability": await build_stability("web", domain,
                                           score_result["overall_score"],
                                           score_result["breakdown"],
                                           score_result.get("weights_used")),
        "created_at": datetime.now().isoformat(),
    }
    if auto_monitor:
        payload["auto_monitor"] = True  # rapor basliginda "otomatik izleme taramasi"
    if ic_dogrulama:
        # 🚩 Ic dogrulama taramasi: SOV atlandigi icin skoru musteri taramalariyla
        # KIYASLANAMAZ. Damga sart — db.get_ai_friendly_list bu satirlari ligden
        # eler, yoksa eksik skorlu bir kayit herkese acik siralamaya sizar.
        payload["internal"] = True
    return payload
