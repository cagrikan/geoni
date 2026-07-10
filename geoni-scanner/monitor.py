"""
GEONI Scanner - Izleme v2 (haftalik otomatik yeniden tarama)

Kullanicinin izleme listesine (watchlist) ekledigi hedefler haftada bir
otomatik olarak yeniden taranir:
  - type=web    -> tam site denetimi (crawl + indexing + recall + skor v3)
  - type=person/brand -> marka bilinirligi kontrolu (recall + SOV)

Kurallar:
  - Izleme taramalari UCRETSIZDIR (deduct=False) — kontor dusmez,
    credits_spent=0 yazilir. Elde tutma (retention) ozelligidir.
  - Kayitlar audits tablosuna normal tarama gibi yazilir; dashboard'daki
    trend/sparkline ayni gecmisten beslenmeye devam eder.
  - Skor son otomatik skora gore ±5 puan degistiyse kullaniciya
    mail@geoni.ai'dan bildirim gider.
  - Donem basina az is (varsayilan 3 kayit/saat) — Tavily/LLM kotalarini
    korumak icin. Kuyruk uzunsa sonraki saatlerde siraya devam edilir.

main.py startup'ta monitor_loop() gorevini baslatir. Bu modul main'i
IMPORT ETMEZ (dairesel bagimlilik yok).
"""

import asyncio
import logging
import uuid
from datetime import datetime

from crawler import crawl_domain
from indexing import check_indexing_status
from scoring import compute_ai_visibility_score
from brand_recall import check_brand_recall, infer_brand_identity
from topics import generate_topics_and_opportunities
from db import (
    list_due_watchlist_items, update_watchlist_after_scan, get_auth_email,
    save_audit, save_brand_check,
)
from mailer import send_monitor_email

logger = logging.getLogger(__name__)

MONITOR_CYCLE_SECONDS = 3600   # saatte bir kontrol
MONITOR_BATCH_SIZE = 3         # donem basina en cok 3 hedef (kota korumasi)
MONITOR_PAGE_LIMIT = 30        # izleme taramasi hafif crawl
SCORE_CHANGE_THRESHOLD = 5     # bildirim esigi (puan)


async def _scan_web_item(item: dict) -> int | None:
    """Web hedefi icin tam denetim; audits'e kaydeder, yeni skoru dondurur."""
    domain = (item.get("target") or {}).get("domain") or item.get("label")
    if not domain:
        return None

    crawl_result = await crawl_domain(domain, MONITOR_PAGE_LIMIT)
    indexing_status = await check_indexing_status(crawl_result["pages"])
    page_titles = [p.get("title", "") for p in crawl_result.get("pages", []) if p.get("title")]
    identity = await infer_brand_identity(domain, page_titles)
    brand_recall_result = await check_brand_recall(
        identity["name"], identity["topic"],
        custom_queries=item.get("custom_queries"),
    )
    score_result = await compute_ai_visibility_score(crawl_result, indexing_status, brand_recall_result)
    topics = await generate_topics_and_opportunities(domain, crawl_result["pages"])

    # NOT: main.run_audit_job'daki result_payload ile ayni sekil olmali —
    # dashboard bu kaydi tiklaninca normal rapor olarak acar.
    result_payload = {
        "domain": domain,
        "score": score_result["overall_score"],
        "score_breakdown": score_result["breakdown"],
        "scoring_version": score_result.get("scoring_version"),
        "weights_used": score_result.get("weights_used"),
        "diagnostics": score_result.get("diagnostics"),
        "total_pages": crawl_result["total_pages"],
        "indexed_pages": indexing_status["indexed_count"],
        "platforms": {
            "chatgpt": indexing_status.get("openai", False),
            "anthropic": indexing_status.get("anthropic", False),
            "perplexity": indexing_status.get("perplexity", False),
            "google": indexing_status.get("google", 0),
        },
        "bot_access": indexing_status.get("bot_access", {}),
        "llms_txt": indexing_status.get("llms_txt", False),
        "top_topics": topics["performing_topics"],
        "opportunities": topics["opportunity_topics"],
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
        },
        "sov": brand_recall_result.get("sov"),
        "auto_monitor": True,  # rapor basliginda "otomatik izleme taramasi" gosterilebilir
        "created_at": datetime.now().isoformat(),
    }

    job_id = str(uuid.uuid4())
    await save_audit(job_id, {"domain": domain, "email": ""}, result_payload,
                     item.get("user_id"), deduct=False)
    return score_result["overall_score"]


async def _scan_brand_item(item: dict) -> int | None:
    """Kisi/marka hedefi icin bilinirlik kontrolu; audits'e kaydeder."""
    target = item.get("target") or {}
    name = target.get("name") or item.get("label")
    if not name:
        return None

    result = await check_brand_recall(
        name=name,
        topic=target.get("topic") or "",
        entity_type=item.get("type") or "person",
        custom_queries=item.get("custom_queries"),
    )
    if not result.get("checked"):
        return None

    result_payload = {
        "name": name,
        "topic": target.get("topic") or "",
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
        "checked": True,
        "raw_list": result.get("raw_list"),
        "sov": result.get("sov"),
        "auto_monitor": True,
        "created_at": datetime.now().isoformat(),
    }

    job_id = str(uuid.uuid4())
    await save_brand_check(
        job_id,
        {"type": item.get("type") or "person", "name": name, "topic": target.get("topic") or ""},
        result_payload,
        item.get("user_id"),
        deduct=False,
    )
    return result.get("score")


async def _process_item(item: dict):
    label = item.get("label", "?")
    try:
        if item.get("type") == "web":
            new_score = await _scan_web_item(item)
        else:
            new_score = await _scan_brand_item(item)
    except Exception as e:
        logger.warning(f"monitor: '{label}' taramasi basarisiz: {e}")
        # Basarisiz denemede de zamani guncelle — bozuk hedef her saat
        # kuyrugun basini tikamasin, sirasi haftaya yeniden gelsin.
        await update_watchlist_after_scan(item.get("id"), None)
        return

    old_score = item.get("last_score")
    await update_watchlist_after_scan(item.get("id"), new_score)
    logger.info(f"monitor: '{label}' otomatik tarandi, skor={new_score} (onceki={old_score})")

    if (
        new_score is not None and old_score is not None
        and abs(new_score - old_score) >= SCORE_CHANGE_THRESHOLD
    ):
        email = await get_auth_email(item.get("user_id"))
        if email:
            await send_monitor_email(email, label, int(old_score), int(new_score))


async def monitor_loop():
    """Saatlik dongu: sirasi gelen izleme kayitlarini kucuk partiler halinde tarar."""
    await asyncio.sleep(120)  # acilista servis otursun
    logger.info("monitor: izleme dongusu basladi")
    while True:
        try:
            items = await list_due_watchlist_items(limit=MONITOR_BATCH_SIZE)
            if items:
                logger.info(f"monitor: {len(items)} hedef sirada")
            for item in items:
                await _process_item(item)
        except Exception as e:
            logger.warning(f"monitor: dongu hatasi: {e}")
        await asyncio.sleep(MONITOR_CYCLE_SECONDS)
