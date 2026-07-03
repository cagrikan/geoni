"""
GEONI Scanner - Scoring Engine
Computes the AI Visibility Score (0-100) from crawl + indexing + brand-recall data.

Formula (Technical Doc 5.1, "v2" — Teknik Duzeltme ve Eklenti Plani Faz 2):

  6 boyutlu (brand recall mevcutsa):
    Score = (IndexCoverage * 0.25) + (Authority * 0.20) + (Freshness * 0.15)
          + (Schema * 0.10) + (Engagement * 0.10) + (BrandRecall * 0.20)

  5 boyutlu fallback (brand recall hesaplanamazsa):
    Score = (IndexCoverage * 0.30) + (Authority * 0.25) + (Freshness * 0.20)
          + (Schema * 0.15) + (Engagement * 0.10)

Madde 2.4 duzeltmesi: Bing `site:` sorgusu bot korumasi nedeniyle sahada
guvenilmez bulundugu icin TAMAMEN KALDIRILDI. Otorite artik uc dayanakli:
  1) Open PageRank API (ucretsiz, domain authority)
  2) Tavily sonuclarinda markanin kac farkli domain'de gectigi (brand_recall
     asamasinda zaten cekilen veriden turetilir, ek maliyet yok)
  3) Wikipedia/Wikidata varlik kontrolu (tek HTTP istegi)
Etkilesim boyutu da ayni Tavily verisinden (sonuc cesitliligi + haber/medya
domain orani) turetilir; Bing/Google sayaclarina bagimliligi kaldirildi.

Madde 2.3 duzeltmesi: "Sema Butunlugu" artik gercek JSON-LD (schema.org)
verisini olcer (crawler.py'nin cikardigi schema_types alani uzerinden).
Eski meta-etiket kontrolu (canonical+description+title) "Temel Meta Sagligi"
adiyla ayri ve durust bir alt gosterge olarak korunur; agirlikli skora
KARISMAZ.
"""

import logging
import re
from datetime import datetime
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

SCORING_VERSION = "v2"

OPENPAGERANK_API_KEY = __import__("os").environ.get("OPENPAGERANK_API_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GeoniBot/1.0; +https://geoni.ai/bot)"
}

# Sayfa icerigi haber/medya kaynagi mi anlamak icin kaba bir domain listesi.
# Etkilesim boyutunun ikincil sinyali olarak kullanilir (Madde 2.4).
NEWS_MEDIA_DOMAINS = {
    "hurriyet.com.tr", "milliyet.com.tr", "sabah.com.tr", "sozcu.com.tr",
    "ntv.com.tr", "cnnturk.com", "haberturk.com", "bloomberght.com",
    "aa.com.tr", "trthaber.com", "webrazzi.com", "forbes.com",
    "techcrunch.com", "reuters.com", "bbc.com", "cnbc.com", "bloomberg.com",
}

# Ana sayfada beklenen kritik schema.org turleri
CRITICAL_HOME_TYPES = {"Organization", "Person", "WebSite", "LocalBusiness"}
# Icerik sayfalarinda beklenen kritik schema.org turleri
CRITICAL_CONTENT_TYPES = {"Article", "FAQPage", "Product", "BlogPosting", "NewsArticle"}


def compute_index_coverage(crawl_result: dict, indexing_status: dict) -> float:
    total_pages = max(len(crawl_result.get("pages", [])), 1)
    indexed = indexing_status.get("indexed_count", 0)
    return min(100.0, (indexed / total_pages) * 100)


# ── Otorite: uc dayanakli yapi (Madde 2.4) ─────────────────────────────────

async def _open_pagerank_score(domain: str) -> float | None:
    """Open PageRank API'sinden 0-10 araligindaki domain skorunu 0-100'e normalize eder."""
    if not OPENPAGERANK_API_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://openpagerank.com/api/v1.0/getPageRank",
                params={"domains[]": domain},
                headers={"API-OPR": OPENPAGERANK_API_KEY},
                timeout=10,
            )
            if resp.status_code == 200:
                items = resp.json().get("response", [])
                if items and items[0].get("status_code") == 200:
                    rank_decimal = float(items[0].get("page_rank_decimal", 0) or 0)
                    return min(100.0, rank_decimal * 10)
    except Exception as e:
        logger.info(f"Open PageRank check failed for {domain}: {e}")
    return None


def _tavily_mentions_score(web_results: list) -> float | None:
    """Tavily sonuclarinda markanin kac farkli domain'de gectigine dayali skor. Ek API maliyeti yoktur."""
    if not web_results:
        return None
    distinct_domains = {urlparse(r.get("url", "")).netloc for r in web_results if r.get("url")}
    distinct_domains.discard("")
    if not distinct_domains:
        return None
    return min(100.0, len(distinct_domains) * 15.0)  # ~7 farkli domain = 100


async def _wikipedia_presence_score(name: str) -> float:
    """Wikipedia'da (TR ardindan EN) sayfa varligini kontrol eder — guclu bir otorite sinyali."""
    if not name or not name.strip():
        return 0.0
    for lang in ("tr", "en"):
        try:
            url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{name.strip().replace(' ', '_')}"
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(url, timeout=8, headers=HEADERS)
                if resp.status_code == 200:
                    data = resp.json()
                    if not data.get("type", "").endswith("disambiguation"):
                        return 100.0
        except Exception as e:
            logger.info(f"Wikipedia check failed ({lang}) for '{name}': {e}")
    return 0.0


async def estimate_authority_score(domain: str, brand_name: str = "", web_results: list | None = None) -> dict:
    """
    Uc dayanakli otorite skoru: Open PageRank + Tavily domain cesitliligi +
    Wikipedia/Wikidata varligi. Kullanilamayan bacaklar atlanir (agirliksiz
    ortalama, mevcut baaklar uzerinden). Hicbiri kullanilamazsa muhafazakar
    bir varsayilan (30.0) donulur — eski davranisla tutarli.
    """
    legs: dict[str, float] = {}

    opr = await _open_pagerank_score(domain)
    if opr is not None:
        legs["open_pagerank"] = opr

    tavily_leg = _tavily_mentions_score(web_results or [])
    if tavily_leg is not None:
        legs["tavily_mentions"] = tavily_leg

    wiki = await _wikipedia_presence_score(brand_name or domain)
    legs["wikipedia"] = wiki  # her zaman hesaplanabilir (0 ya da 100)

    if legs:
        score = sum(legs.values()) / len(legs)
    else:
        score = 30.0  # muhafazakar fallback

    return {"score": score, "legs": legs}


def compute_freshness_score(pages: list[dict]) -> float:
    """
    Estimate freshness based on presence of recent dates in page metadata
    (title/description often contain year or 'updated' markers for blogs).
    Falls back to a neutral score if no signal is found.
    """
    if not pages:
        return 50.0

    current_year = datetime.now().year
    recent_signals = 0

    for page in pages:
        text_blob = " ".join(
            str(page.get(field, "")) for field in ("title", "meta_description")
        )
        if str(current_year) in text_blob or str(current_year - 1) in text_blob:
            recent_signals += 1

    ratio = recent_signals / len(pages)
    return min(100.0, 40 + ratio * 60)  # baseline 40, up to 100 with strong signal


# ── Sema: gercek JSON-LD (Madde 2.3) ────────────────────────────────────────

def compute_meta_health_score(pages: list[dict]) -> float:
    """
    Eski 'schema' hesabi — aslinda temel HTML meta etiketlerini (canonical +
    description + title) olcuyordu. schema.org ile ilgisi yoktu; bu yuzden
    'Temel Meta Sagligi' adiyla ayri ve durust bir gosterge olarak korunur.
    """
    if not pages:
        return 0.0
    scored = 0
    for page in pages:
        has_canonical = bool(page.get("canonical_url"))
        has_description = bool(page.get("meta_description"))
        has_title = bool(page.get("title"))
        scored += sum([has_canonical, has_description, has_title]) / 3
    return min(100.0, (scored / len(pages)) * 100)


def compute_schema_score(pages: list[dict], domain: str = "") -> dict:
    """
    Gercek schema.org (JSON-LD) butunluk skoru:
      - Varlik orani: kac sayfada en az bir JSON-LD blogu var
      - Tur cesitliligi: sitede kac farkli @type kullanilmis
      - Kritik tur bonusu: ana sayfada Organization/Person/WebSite,
        icerik sayfalarinda Article/FAQPage/Product/... var mi

    Playwright zaten her sayfayi render ettigi icin bu ek bir crawl
    maliyeti getirmez (bkz. crawler.py extract_page_metadata).
    """
    if not pages:
        return {"score": 0.0, "presence_ratio": 0.0, "distinct_types": [], "critical_home": False, "critical_content": False}

    pages_with_schema = [p for p in pages if p.get("schema_types")]
    presence_ratio = len(pages_with_schema) / len(pages)

    all_types: set[str] = set()
    for p in pages_with_schema:
        all_types.update(p.get("schema_types", []))
    type_diversity_score = min(100.0, len(all_types) * 20.0)

    def _is_homepage(page_url: str) -> bool:
        if not domain:
            return False
        parsed = urlparse(page_url)
        path = parsed.path.strip("/")
        return domain in parsed.netloc and path == ""

    critical_home = any(
        _is_homepage(p.get("url", "")) and set(p.get("schema_types", [])) & CRITICAL_HOME_TYPES
        for p in pages_with_schema
    )
    # Ana sayfa tespit edilemediyse (ör. crawl kok URL'i donmediyse) herhangi
    # bir sayfada kritik "kimlik" turu olmasi da kismi puan alsin.
    if not critical_home:
        critical_home = any(set(p.get("schema_types", [])) & CRITICAL_HOME_TYPES for p in pages_with_schema)

    critical_content = any(
        set(p.get("schema_types", [])) & CRITICAL_CONTENT_TYPES for p in pages_with_schema
    )

    critical_bonus = (30.0 if critical_home else 0.0) + (20.0 if critical_content else 0.0)

    score = min(100.0, presence_ratio * 100 * 0.5 + type_diversity_score * 0.3 + critical_bonus * 0.2)

    return {
        "score": score,
        "presence_ratio": round(presence_ratio, 2),
        "distinct_types": sorted(all_types),
        "critical_home": critical_home,
        "critical_content": critical_content,
    }


# ── Etkilesim: Tavily tabanli (Madde 2.4) ──────────────────────────────────

def compute_engagement_score(web_results: list | None) -> float:
    """
    Etkilesim proxy'si artik Bing/Google sonuc sayisina degil, Tavily
    sonuc cesitliligine (farkli domain sayisi) ve haber/medya domain
    oranina dayaniyor. Bing kaldirildigi icin (Madde 2.4) bu boyut artik
    brand_recall asamasinda zaten cekilen veriden turetiliyor.
    """
    if not web_results:
        return 20.0  # notr-dusuk baseline, veri yoksa fallback

    distinct_domains = {urlparse(r.get("url", "")).netloc for r in web_results if r.get("url")}
    distinct_domains.discard("")
    diversity_score = min(100.0, len(distinct_domains) * 15.0)

    news_hits = sum(
        1 for r in web_results
        if any(nd in (r.get("url") or "") for nd in NEWS_MEDIA_DOMAINS)
    )
    news_ratio = news_hits / max(len(web_results), 1)

    return min(100.0, diversity_score * 0.7 + news_ratio * 100 * 0.3)


async def compute_ai_visibility_score(crawl_result: dict, indexing_status: dict, brand_recall_result: dict | None = None) -> dict:
    """
    Compute the full AI Visibility Score (0-100) and component breakdown.

    brand_recall_result verilirse (main.py artik domain taramalarinda marka
    bilinirligini de kontrol ediyor — bkz. infer_brand_identity +
    check_brand_recall), skor 6 boyutlu formule gecer ve BrandRecall %20
    agirlikla dahil olur. Verilmezse veya `checked=False` ise 5 boyutlu
    fallback formule dusulur.
    """
    domain = crawl_result.get("domain", "")
    pages = crawl_result.get("pages", [])

    web_results = (brand_recall_result or {}).get("web_results") or []
    brand_name = (brand_recall_result or {}).get("topic") or domain

    index_coverage = compute_index_coverage(crawl_result, indexing_status)
    authority = await estimate_authority_score(domain, brand_name=brand_name, web_results=web_results)
    authority_score = authority["score"]
    freshness_score = compute_freshness_score(pages)
    schema = compute_schema_score(pages, domain=domain)
    schema_score = schema["score"]
    meta_health_score = compute_meta_health_score(pages)
    engagement_score = compute_engagement_score(web_results)

    brand_recall_checked = bool(brand_recall_result and brand_recall_result.get("checked"))
    brand_recall_score = brand_recall_result.get("score") if brand_recall_checked else None

    if brand_recall_checked and brand_recall_score is not None:
        score = (
            (index_coverage * 0.25)
            + (authority_score * 0.20)
            + (freshness_score * 0.15)
            + (schema_score * 0.10)
            + (engagement_score * 0.10)
            + (brand_recall_score * 0.20)
        )
        weights_used = {"index_coverage": 0.25, "authority": 0.20, "freshness": 0.15,
                         "schema": 0.10, "engagement": 0.10, "brand_recall": 0.20}
    else:
        score = (
            (index_coverage * 0.30)
            + (authority_score * 0.25)
            + (freshness_score * 0.20)
            + (schema_score * 0.15)
            + (engagement_score * 0.10)
        )
        weights_used = {"index_coverage": 0.30, "authority": 0.25, "freshness": 0.20,
                         "schema": 0.15, "engagement": 0.10}

    return {
        "overall_score": int(round(score)),
        "scoring_version": SCORING_VERSION,
        "weights_used": weights_used,
        "breakdown": {
            "index_coverage": round(index_coverage, 1),
            "authority": round(authority_score, 1),
            "freshness": round(freshness_score, 1),
            "schema": round(schema_score, 1),
            "engagement": round(engagement_score, 1),
            **({"brand_recall": round(brand_recall_score, 1)} if brand_recall_score is not None else {}),
        },
        "diagnostics": {
            "authority_legs": authority["legs"],
            "meta_health": round(meta_health_score, 1),  # Madde 2.3: ayri, durust alt gosterge
            "schema_presence_ratio": schema["presence_ratio"],
            "schema_distinct_types": schema["distinct_types"],
            "schema_critical_home": schema["critical_home"],
            "schema_critical_content": schema["critical_content"],
        },
    }
