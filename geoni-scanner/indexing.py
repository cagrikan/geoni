"""
GEONI Scanner - Indexing Checker Service
Checks whether crawled pages are indexed by Google, and whether the domain
allows AI crawlers via robots.txt — with a critical distinction between
EGITIM (training) crawlers and ARAMA (search/citation) crawlers (Madde 2.5).

Onceki surumun hatasi: yalnizca GPTBot/ClaudeBot/Googlebot kontrol ediliyordu.
GPTBot bir egitim crawler'idir; ChatGPT'nin gercek zamanli aramasinda
gorunmeyi OAI-SearchBot ve ChatGPT-User belirler. Bir site GPTBot'u
engelleyip OAI-SearchBot'a izin verebilir — bu durumda site "AI'ya kapali"
degil, ChatGPT aramasinda pekala gorunur olabilir.

Madde 2.4: Bing `site:` sorgusu bot korumasi nedeniyle guvenilir calismadigi
sahada dogrulandigi icin tamamen kaldirildi. Otorite/etkilesim boyutlari
artik scoring.py'de Open PageRank + Tavily + Wikipedia uc-dayanakli yapiya
tasindi (bkz. scoring.py estimate_authority_score).
"""

import asyncio
import logging
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

from ssrf_guard import assert_public_host, BlockedHostError, safe_get

logger = logging.getLogger(__name__)

# Egitim (training) crawler'lar: modelin egitim verisine bu sitenin
# icerigini eklemesini saglar. Arama/alintilanma ile DOGRUDAN ilgili degildir.
TRAINING_CRAWLER_AGENTS = {
    "gptbot":          "GPTBot",
    "claudebot":       "ClaudeBot",
    "google_extended": "Google-Extended",
    "ccbot":           "CCBot",          # Common Crawl - bircok modelin egitim kaynagi
    "anthropic_ai":    "anthropic-ai",
}

# Arama/alintilanma (search & citation) crawler'lari: kullanicinin gercek
# zamanli AI yanitlarinda (ChatGPT arama, Perplexity, Claude arama) gorunmeyi
# BU botlarin erisimi belirler.
SEARCH_CRAWLER_AGENTS = {
    "oai_searchbot":     "OAI-SearchBot",
    "chatgpt_user":      "ChatGPT-User",
    "perplexitybot":     "PerplexityBot",
    "perplexity_user":   "Perplexity-User",
    "claude_searchbot":  "Claude-SearchBot",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GeoniBot/1.0; +https://geoni.ai/bot)"
}


async def check_robots_ai_access(domain: str) -> dict:
    """
    robots.txt'i egitim ve arama crawler'lari icin ayri ayri kontrol eder.
    Robots.txt yoksa/erisilemezse varsayilan olarak tum botlara izin verildigi
    kabul edilir (RFC/fiili standart davranis).
    """
    robots_url = f"https://{domain}/robots.txt"
    egitim = {key: True for key in TRAINING_CRAWLER_AGENTS}
    arama = {key: True for key in SEARCH_CRAWLER_AGENTS}
    robots_found = False

    try:
        # SSRF-guvenli: safe_get her redirect hop'unu dogrular (apex<->www gibi
        # mesru kanonik redirect'ler korunur, ic adrese sicrama engellenir).
        async with httpx.AsyncClient() as client:
            resp = await safe_get(client, robots_url, timeout=10, headers=HEADERS)
            if resp.status_code == 200:
                robots_found = True
                rp = RobotFileParser()
                rp.parse(resp.text.splitlines())

                for key, agent in TRAINING_CRAWLER_AGENTS.items():
                    try:
                        egitim[key] = rp.can_fetch(agent, f"https://{domain}/")
                    except Exception:
                        pass

                for key, agent in SEARCH_CRAWLER_AGENTS.items():
                    try:
                        arama[key] = rp.can_fetch(agent, f"https://{domain}/")
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"Could not check robots.txt for {domain}: {e}")

    return {
        "robots_found": robots_found,
        "egitim": egitim,
        "arama": arama,
        # Geriye donuk uyumluluk icin: platform bazinda "en azindan bir arama
        # botu izinli mi" ozeti. Bu, "AI'da gorunurluk" acisindan egitim
        # botlarindan cok daha anlamli bir sinyaldir.
        "openai": arama["oai_searchbot"] or arama["chatgpt_user"],
        "anthropic": arama["claude_searchbot"] or egitim["claudebot"],
        "perplexity": arama["perplexitybot"] or arama["perplexity_user"],
    }


async def check_llms_txt(domain: str) -> bool:
    """
    llms.txt varligini kontrol eder (yeni bir standart, AI erisimi icin
    site sahibinin niyetini beyan ettigi dosya). Varligi hem bir sinyal
    hem de raporda 'oneri' maddesi uretir (Madde 2.5).
    """
    llms_url = f"https://{domain}/llms.txt"
    try:
        # SSRF-guvenli redirect takibi (bkz. check_robots_ai_access).
        async with httpx.AsyncClient() as client:
            resp = await safe_get(client, llms_url, timeout=8, headers=HEADERS)
            # T3: SPA soft-404 -> her path'e 200 + index.html doner. HTML iceren
            # yaniti llms.txt sayma (yanlis pozitif hem skoru hem "llms_robots"
            # bilet satisini yaniltir). content-type html ya da <!doctype/<html reddet.
            text = resp.text.strip()
            ctype = resp.headers.get("content-type", "").lower()
            is_html = "text/html" in ctype or text[:200].lower().lstrip().startswith(("<!doctype", "<html"))
            return resp.status_code == 200 and len(text) > 0 and not is_html
    except Exception as e:
        logger.info(f"llms.txt check failed for {domain}: {e}")
        return False


async def check_google_indexed(domain: str, sample_size: int = 5) -> int:
    """
    Best-effort check of how many pages from `domain` appear indexed in Google,
    using the public search results page for a `site:` query.
    Returns an estimated count (capped to sample_size for MVP speed).
    """
    query = f"site:{domain}"
    url = f"https://www.google.com/search?q={query}&num=10"

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=10, headers=HEADERS)
            if resp.status_code == 200:
                count = resp.text.count(domain)
                return min(count, sample_size * 20)  # rough cap
    except Exception as e:
        logger.warning(f"Google index check failed for {domain}: {e}")

    return 0


async def check_indexing_status(pages: list[dict]) -> dict:
    """
    Check indexing status across Google and AI crawler access
    (egitim vs arama ayrimiyla, Madde 2.5), artı llms.txt sinyali.

    NOT (Madde 2.4): Bing kontrolu kaldirildi — bot korumasi nedeniyle
    guvenilir sonuc uretmiyordu ve iki skor boyutunu sessizce bozuyordu.

    Returns:
        {
          "indexed_count": int,
          "google": int,
          "bot_access": {"egitim": {...}, "arama": {...}, "robots_found": bool},
          "llms_txt": bool,
          "openai": bool,      # geriye donuk uyumluluk (arama botlarina gore)
          "anthropic": bool,   # geriye donuk uyumluluk
          "perplexity": bool,
        }
    """
    if not pages:
        return {
            "indexed_count": 0, "google": 0,
            "bot_access": {"egitim": {}, "arama": {}, "robots_found": False},
            "llms_txt": False, "openai": False, "anthropic": False, "perplexity": False,
        }

    from urllib.parse import urlparse
    domain = urlparse(pages[0]["url"]).netloc or urlparse(pages[0]["url"]).path

    # SSRF savunma-derinligi: domain crawl asamasinda dogrulaniyor ama indexing
    # bagimsiz cagrilabilir; host public degilse (ic-IP/metadata) hic istek atma.
    host = (domain or "").split(":")[0]
    try:
        await asyncio.to_thread(assert_public_host, host)
    except BlockedHostError:
        logger.warning(f"indexing atlandi, public olmayan host: {host}")
        return {
            "indexed_count": 0, "google": 0,
            "bot_access": {"egitim": {}, "arama": {}, "robots_found": False},
            "llms_txt": False, "openai": False, "anthropic": False, "perplexity": False,
        }

    google_count, ai_access, llms_txt = await asyncio.gather(
        check_google_indexed(domain),
        check_robots_ai_access(domain),
        check_llms_txt(domain),
    )

    indexed_count = google_count  # Bing kaldirildigi icin artik yalnizca Google

    return {
        "indexed_count": min(indexed_count, len(pages)),
        "google": google_count,
        "bot_access": {
            "egitim": ai_access["egitim"],
            "arama": ai_access["arama"],
            "robots_found": ai_access["robots_found"],
        },
        "llms_txt": llms_txt,
        "openai": ai_access.get("openai", True),
        "anthropic": ai_access.get("anthropic", True),
        "perplexity": ai_access.get("perplexity", True),
    }
