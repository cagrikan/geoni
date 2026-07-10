"""
GEONI Scanner - Scoring Engine
Computes the AI Visibility Score (0-100) from crawl + indexing + brand-recall data.

Formula "v3" (Algoritma Iyilestirme Paketi, Temmuz 2026):

  7 boyutlu (brand recall mevcutsa):
    Score = (IndexCoverage * 0.20) + (Authority * 0.15) + (Freshness * 0.10)
          + (Schema * 0.10) + (AIAccess * 0.12) + (Engagement * 0.08)
          + (BrandRecall * 0.25)

  6 boyutlu fallback (brand recall hesaplanamazsa):
    Score = (IndexCoverage * 0.28) + (Authority * 0.22) + (Freshness * 0.14)
          + (Schema * 0.14) + (AIAccess * 0.14) + (Engagement * 0.08)

v3 degisiklikleri:
  - AI Erisimi yeni boyut: robots.txt'te arama/egitim botu izinleri +
    llms.txt + sitemap. Bu sinyaller zaten hesaplaniyordu (indexing.py)
    ama skora hic girmiyordu — GEO'nun en temel kosulu skorun parcasi oldu.
  - Tazelik artik gercek tarihlerden: sitemap <lastmod> + JSON-LD
    datePublished/dateModified. Yil-metni araması yalnizca fallback.
  - Otorite: Tavily "farkli domain" bacagi markanin KENDI domain'ini
    saymaz; Wikipedia ikili bacak olmaktan cikip +15 bonusa dondu
    (Wikipedia sayfasi olmayan kucuk isletme otoritenin ucte birini
    kaybetmesin diye).
  - Etkilesim: otoriteyle ayni sinyali (domain cesitliligi) cifte saymayi
    birakti; sosyal/dizin varligi + haber orani olctu.
  - Indekslenebilirlik: Google site: orneklemesi bot korumasina takilirsa
    diye kendi kontrolumuzdeki durust sinyalle (noindex/canonical)
    harmanlanir.

Madde 2.3/2.4 duzeltmeleri (v2) korunur: Bing yok, sema gercek JSON-LD,
meta sagligi ayri gosterge.
"""

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

SCORING_VERSION = "v3"

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

# Sosyal ag + is dizini domainleri: etkilesim boyutunun yeni sinyali (v3).
# Markanin buralarda gorunmesi, AI motorlarinin alintiladigi "varlik izi"dir.
SOCIAL_DIRECTORY_DOMAINS = {
    "linkedin.com", "instagram.com", "facebook.com", "x.com", "twitter.com",
    "youtube.com", "tiktok.com", "g2.com", "capterra.com", "producthunt.com",
    "trustpilot.com", "sikayetvar.com", "tripadvisor.com", "glassdoor.com",
    "crunchbase.com", "github.com", "medium.com",
}


def _indexability_score(pages: list[dict]) -> float:
    """
    Kendi kontrolumuzdeki durust indekslenebilirlik sinyali (v3):
    sayfalarin noindex TASIMAMASI (%70) + canonical varligi (%30).
    Google `site:` orneklemesi bot korumasina takildiginda skoru sifira
    cokertmemesi icin index boyutuyla harmanlanir.
    """
    if not pages:
        return 0.0
    indexable = sum(1 for p in pages if not p.get("noindex"))
    canonical = sum(1 for p in pages if p.get("canonical_url"))
    return min(100.0, (indexable / len(pages)) * 70 + (canonical / len(pages)) * 30)


def compute_index_coverage(crawl_result: dict, indexing_status: dict) -> dict:
    """v3: Google site: orneklemesi (0.5) + indekslenebilirlik (0.5) harmani."""
    pages = crawl_result.get("pages", [])
    total_pages = max(len(pages), 1)
    indexed = indexing_status.get("indexed_count", 0)
    google_coverage = min(100.0, (indexed / total_pages) * 100)
    indexability = _indexability_score(pages)
    return {
        "score": google_coverage * 0.5 + indexability * 0.5,
        "google_coverage": round(google_coverage, 1),
        "indexability": round(indexability, 1),
    }


# ── AI Erisimi: yeni boyut (v3) ─────────────────────────────────────────────

def compute_ai_access_score(indexing_status: dict, crawl_result: dict) -> dict:
    """
    GEO'nun en temel kosulu: AI botlari siteye erisebiliyor mu?
    - Arama/alintilanma botlari (OAI-SearchBot, PerplexityBot, ...) %60 —
      gercek zamanli AI yanitlarinda gorunmeyi dogrudan bunlar belirler.
    - Egitim botlari (GPTBot, ClaudeBot, ...) %20 — modelin kalici bilgisine
      girme sansi.
    - llms.txt varligi %10, sitemap varligi %10 — niyet beyanlari.
    """
    bot_access = indexing_status.get("bot_access", {}) or {}
    arama = bot_access.get("arama", {}) or {}
    egitim = bot_access.get("egitim", {}) or {}

    arama_ratio = (sum(1 for v in arama.values() if v) / len(arama)) if arama else 1.0
    egitim_ratio = (sum(1 for v in egitim.values() if v) / len(egitim)) if egitim else 1.0
    llms_txt = bool(indexing_status.get("llms_txt"))
    sitemap_found = bool(crawl_result.get("sitemap_found"))

    score = (
        arama_ratio * 60
        + egitim_ratio * 20
        + (10 if llms_txt else 0)
        + (10 if sitemap_found else 0)
    )
    return {
        "score": min(100.0, score),
        "arama_ratio": round(arama_ratio, 2),
        "egitim_ratio": round(egitim_ratio, 2),
        "llms_txt": llms_txt,
        "sitemap_found": sitemap_found,
    }


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


def _external_domains(web_results: list, own_domain: str = "") -> set[str]:
    """Tavily sonuclarindaki farkli domainler — markanin KENDI domain'i haric (v3)."""
    own = (own_domain or "").lower().removeprefix("www.")
    domains = set()
    for r in web_results or []:
        netloc = urlparse(r.get("url", "")).netloc.lower().removeprefix("www.")
        if netloc and (not own or (netloc != own and not netloc.endswith("." + own))):
            domains.add(netloc)
    return domains


def _tavily_mentions_score(web_results: list, own_domain: str = "") -> float | None:
    """
    Tavily sonuclarinda markanin kac farkli DIS domain'de gectigine dayali skor.
    v3: kendi domain'i sayilmaz — site kendi kendine "bahsedilme" puani alamaz.
    """
    if not web_results:
        return None
    distinct = _external_domains(web_results, own_domain)
    if not distinct:
        return 0.0  # sonuc var ama hepsi kendi sitesi: dis dunyada iz yok
    return min(100.0, len(distinct) * 15.0)  # ~7 farkli dis domain = 100


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


def _norm_label(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


async def _wikidata_presence(name: str) -> bool:
    """
    Wikidata'da varlik (entity) kontrolu. Wikipedia sayfasi olmayan ama
    Wikidata kaydi olan markalar icin ikincil otorite sinyali — ayrica
    sattigimiz 'Wikidata Kaydi' hizmetinin skoru gercekten oynatmasini
    saglar (hizmet-skor tutarliligi). Ad eslesmesi normalize edilerek
    yapilir ("GEONI" ~ "Geoni.ai" gibi ek/uzanti farklari eslesir).
    """
    if not name or not name.strip():
        return False
    norm_name = _norm_label(name)
    if len(norm_name) < 3:
        return False
    for lang in ("tr", "en"):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.wikidata.org/w/api.php",
                    params={"action": "wbsearchentities", "search": name.strip(),
                            "language": lang, "format": "json", "limit": 5},
                    timeout=8, headers=HEADERS,
                )
                if resp.status_code != 200:
                    continue
                for item in resp.json().get("search", []):
                    norm_label = _norm_label(item.get("label", ""))
                    if norm_label and (norm_name in norm_label or norm_label in norm_name):
                        return True
        except Exception as e:
            logger.info(f"Wikidata check failed ({lang}) for '{name}': {e}")
    return False


async def estimate_authority_score(domain: str, brand_name: str = "", web_results: list | None = None) -> dict:
    """
    v3 otorite skoru: Open PageRank (0.55) + Tavily dis-domain bahsi (0.45)
    agirlikli ortalamasi; Wikipedia varligi ikili bacak degil +15 bonus.
    Eski yapida Wikipedia 0/100 esit-agirlikli bacakti — Wikipedia sayfasi
    olmayan her kucuk isletme otoritenin ucte birini bastan kaybediyordu.
    Kullanilamayan bacaklar atlanir (kalanlar uzerinden yeniden normalize).
    Hicbiri kullanilamazsa muhafazakar varsayilan (30.0) donulur.
    """
    legs: dict[str, float] = {}
    weights = {"open_pagerank": 0.55, "tavily_mentions": 0.45}

    opr = await _open_pagerank_score(domain)
    if opr is not None:
        legs["open_pagerank"] = opr

    tavily_leg = _tavily_mentions_score(web_results or [], own_domain=domain)
    if tavily_leg is not None:
        legs["tavily_mentions"] = tavily_leg

    wiki = await _wikipedia_presence_score(brand_name or domain)

    if legs:
        total_w = sum(weights[k] for k in legs)
        score = sum(legs[k] * weights[k] for k in legs) / total_w
    else:
        score = 30.0  # muhafazakar fallback

    if wiki > 0:
        score = min(100.0, score + 15.0)  # Wikipedia bonusu (guclu sinyal)
        legs["wikipedia_bonus"] = 15.0
    elif await _wikidata_presence(brand_name or domain):
        # Wikipedia yoksa Wikidata varligi ikincil bonus — 'Wikidata Kaydi'
        # hizmetini alan musterinin skoru gorunur sekilde yukselir.
        score = min(100.0, score + 8.0)
        legs["wikidata_bonus"] = 8.0

    return {"score": score, "legs": legs}


def _parse_any_date(raw: str) -> datetime | None:
    """ISO 8601 ve yaygin sitemap/JSON-LD tarih bicimlerini toleransli parse eder."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def compute_freshness_score(pages: list[dict], sitemap_lastmods: list[str] | None = None) -> dict:
    """
    v3: Tazelik artik gercek tarihlerden hesaplanir:
      1) sitemap <lastmod> degerleri (crawler.py artik okuyor)
      2) sayfalardaki JSON-LD datePublished/dateModified (schema_dates)
    Son 12 ay icinde guncellenen kayit orani skoru belirler.
    Hic gercek tarih yoksa eski yil-metni sezgiseline (fallback) dusulur —
    o sezgisel tek basina neredeyse anlamsizdi ("2026" yazisi araniyordu).
    """
    if not pages:
        return {"score": 50.0, "signal": "none", "dated_count": 0, "recent_ratio": 0.0}

    now = datetime.now(timezone.utc)
    dates: list[datetime] = []
    for raw in (sitemap_lastmods or []):
        d = _parse_any_date(raw)
        if d:
            dates.append(d)
    for page in pages:
        for raw in page.get("schema_dates", []) or []:
            d = _parse_any_date(raw)
            if d:
                dates.append(d)

    if dates:
        recent = sum(1 for d in dates if (now - d).days <= 365)
        very_recent = sum(1 for d in dates if (now - d).days <= 90)
        ratio = recent / len(dates)
        boost = (very_recent / len(dates)) * 10  # son 3 ay aktifligi kucuk bonus
        score = min(100.0, 30 + ratio * 60 + boost)
        return {"score": score, "signal": "real_dates", "dated_count": len(dates),
                "recent_ratio": round(ratio, 2)}

    # Fallback: eski yil-metni sezgiseli
    current_year = now.year
    recent_signals = 0
    for page in pages:
        text_blob = " ".join(
            str(page.get(field, "")) for field in ("title", "meta_description")
        )
        if str(current_year) in text_blob or str(current_year - 1) in text_blob:
            recent_signals += 1
    ratio = recent_signals / len(pages)
    return {"score": min(100.0, 40 + ratio * 60), "signal": "year_text_fallback",
            "dated_count": 0, "recent_ratio": round(ratio, 2)}


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


# ── Etkilesim: sosyal/dizin + haber (v3) ────────────────────────────────────

def compute_engagement_score(web_results: list | None, own_domain: str = "") -> float:
    """
    v3: Etkilesim, otorite boyutuyla ayni sinyali (domain cesitliligi) cifte
    saymayi birakti. Yeni sinyaller:
      - Sosyal ag / is dizini varligi (LinkedIn, Instagram, G2, Trustpilot,
        şikayetvar...): her farkli platform 20 puan, en cok 60.
      - Haber/medya orani: %40.
    Kendi domain sonuclari sayilmaz.
    """
    if not web_results:
        return 20.0  # notr-dusuk baseline, veri yoksa fallback

    external = _external_domains(web_results, own_domain)
    social_hits = {
        d for d in external
        if any(d == sd or d.endswith("." + sd) for sd in SOCIAL_DIRECTORY_DOMAINS)
    }
    social_score = min(60.0, len(social_hits) * 20.0)

    news_hits = sum(
        1 for r in web_results
        if any(nd in (r.get("url") or "") for nd in NEWS_MEDIA_DOMAINS)
    )
    news_ratio = news_hits / max(len(web_results), 1)

    return min(100.0, social_score + news_ratio * 100 * 0.4)


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
    # Onceden yanlislikla topic (faaliyet alani) kullaniliyordu — Wikipedia/
    # Wikidata'da "AI gorunurluk optimizasyonu" gibi alan adlari araniyordu.
    # Dogrusu marka ADI; yoksa domain de kabul edilebilir bir eslesme verir.
    brand_name = (brand_recall_result or {}).get("name") or domain

    index = compute_index_coverage(crawl_result, indexing_status)
    index_coverage = index["score"]
    authority = await estimate_authority_score(domain, brand_name=brand_name, web_results=web_results)
    authority_score = authority["score"]
    freshness = compute_freshness_score(pages, crawl_result.get("sitemap_lastmods"))
    freshness_score = freshness["score"]
    schema = compute_schema_score(pages, domain=domain)
    schema_score = schema["score"]
    ai_access = compute_ai_access_score(indexing_status, crawl_result)
    ai_access_score = ai_access["score"]
    meta_health_score = compute_meta_health_score(pages)
    engagement_score = compute_engagement_score(web_results, own_domain=domain)

    brand_recall_checked = bool(brand_recall_result and brand_recall_result.get("checked"))
    brand_recall_score = brand_recall_result.get("score") if brand_recall_checked else None

    if brand_recall_checked and brand_recall_score is not None:
        score = (
            (index_coverage * 0.20)
            + (authority_score * 0.15)
            + (freshness_score * 0.10)
            + (schema_score * 0.10)
            + (ai_access_score * 0.12)
            + (engagement_score * 0.08)
            + (brand_recall_score * 0.25)
        )
        weights_used = {"index_coverage": 0.20, "authority": 0.15, "freshness": 0.10,
                         "schema": 0.10, "ai_access": 0.12, "engagement": 0.08,
                         "brand_recall": 0.25}
    else:
        score = (
            (index_coverage * 0.28)
            + (authority_score * 0.22)
            + (freshness_score * 0.14)
            + (schema_score * 0.14)
            + (ai_access_score * 0.14)
            + (engagement_score * 0.08)
        )
        weights_used = {"index_coverage": 0.28, "authority": 0.22, "freshness": 0.14,
                         "schema": 0.14, "ai_access": 0.14, "engagement": 0.08}

    return {
        "overall_score": int(round(score)),
        "scoring_version": SCORING_VERSION,
        "weights_used": weights_used,
        "breakdown": {
            "index_coverage": round(index_coverage, 1),
            "authority": round(authority_score, 1),
            "freshness": round(freshness_score, 1),
            "schema": round(schema_score, 1),
            "ai_access": round(ai_access_score, 1),
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
            "google_coverage": index["google_coverage"],
            "indexability": index["indexability"],
            "freshness_signal": freshness["signal"],
            "freshness_dated_count": freshness["dated_count"],
            "freshness_recent_ratio": freshness["recent_ratio"],
            "ai_access": ai_access,
        },
    }
