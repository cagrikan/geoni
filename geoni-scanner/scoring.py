"""
GEONI Scanner - Scoring Engine
Computes the AI Visibility Score (0-100) from crawl + indexing data.

Formula (weights per product spec):
  Score = (IndexCoverage * 0.30)
        + (Authority      * 0.25)
        + (Freshness      * 0.20)
        + (SchemaScore    * 0.15)
        + (Engagement     * 0.10)

For MVP, Authority and Engagement use lightweight heuristics
(domain age via WHOIS-free signal + backlink proxy via Bing results count)
rather than paid third-party APIs (Moz/Ahrefs). These can be swapped in later
without changing the public function signature.
"""

import logging
import re
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GeoniBot/1.0; +https://geoni.ai/bot)"
}


def compute_index_coverage(crawl_result: dict, indexing_status: dict) -> float:
    total_pages = max(len(crawl_result.get("pages", [])), 1)
    indexed = indexing_status.get("indexed_count", 0)
    return min(100.0, (indexed / total_pages) * 100)


async def estimate_authority_score(domain: str) -> float:
    """
    Lightweight authority proxy: number of distinct referring pages found
    via a backlink-style search query. Capped/normalized to 0-100.
    Replace with Moz/Ahrefs API call when budget allows.
    """
    try:
        url = f"https://www.bing.com/search?q=link:{domain}&count=20"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=10, headers=HEADERS)
            if resp.status_code == 200:
                mentions = resp.text.count(domain)
                # normalize: 0 mentions -> 20 baseline, 50+ mentions -> 100
                return min(100.0, 20 + mentions * 1.5)
    except Exception as e:
        logger.warning(f"Authority estimation failed for {domain}: {e}")

    return 30.0  # conservative fallback


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


def compute_schema_score(pages: list[dict]) -> float:
    """
    Schema.org completeness proxy: presence of canonical_url and meta_description
    as a stand-in for structured-data hygiene (full JSON-LD parsing can be added
    by extending the crawler to capture <script type="application/ld+json">).
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


def compute_engagement_score(indexing_status: dict) -> float:
    """
    Engagement proxy: combination of Google + Bing visible result counts
    as a rough signal of social/citation presence. Replace with real
    social-mention API integration later.
    """
    google = indexing_status.get("google", 0)
    bing = indexing_status.get("bing", 0)
    combined = google + bing
    return min(100.0, 20 + combined * 0.8)


async def compute_ai_visibility_score(crawl_result: dict, indexing_status: dict) -> dict:
    """
    Compute the full AI Visibility Score (0-100) and component breakdown.
    """
    domain = crawl_result.get("domain", "")
    pages = crawl_result.get("pages", [])

    index_coverage = compute_index_coverage(crawl_result, indexing_status)
    authority_score = await estimate_authority_score(domain)
    freshness_score = compute_freshness_score(pages)
    schema_score = compute_schema_score(pages)
    engagement_score = compute_engagement_score(indexing_status)

    score = (
        (index_coverage * 0.30)
        + (authority_score * 0.25)
        + (freshness_score * 0.20)
        + (schema_score * 0.15)
        + (engagement_score * 0.10)
    )

    return {
        "overall_score": int(round(score)),
        "breakdown": {
            "index_coverage": round(index_coverage, 1),
            "authority": round(authority_score, 1),
            "freshness": round(freshness_score, 1),
            "schema": round(schema_score, 1),
            "engagement": round(engagement_score, 1),
        },
    }
