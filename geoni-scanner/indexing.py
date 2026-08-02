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
import os
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

from ssrf_guard import assert_public_host, BlockedHostError, safe_get

logger = logging.getLogger(__name__)

# T5: Brave Search API anahtari. Claude'un retrieval indeksi Brave'dir; bu
# anahtar MUHTEMELEN YOK — o durumda check_brave_index None doner ve hicbir
# davranis degismez (scoring.compute_index_coverage brave bacagini atlar).
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

# M6: Cloudflare / bot-koruma challenge sayfalarinin kaba imzalari. robots.txt
# 200 donse bile govde bir "insan dogrulama" sayfasiysa AI crawler'lari fiilen
# bloklanmis olabilir (robots temiz gorunup sitenin bloklu olmasi).
_BOT_CHALLENGE_MARKERS = (
    "just a moment", "cf-browser-verification", "challenge-platform",
    "checking your browser", "attention required", "cf-chl", "_cf_chl",
    "enable javascript and cookies to continue",
)

# Egitim (training) crawler'lar: modelin egitim verisine bu sitenin
# icerigini eklemesini saglar. Arama/alintilanma ile DOGRUDAN ilgili degildir.
TRAINING_CRAWLER_AGENTS = {
    "gptbot":          "GPTBot",
    "claudebot":       "ClaudeBot",
    "google_extended": "Google-Extended",
    "ccbot":           "CCBot",          # Common Crawl - bircok modelin egitim kaynagi
    "applebot_extended":  "Applebot-Extended",  # Apple Intelligence (2026 guncel)
    "meta_externalagent": "meta-externalagent",  # Meta AI (2026 guncel)
    "bytespider":         "Bytespider",          # ByteDance / TikTok AI (2026-08-02 eklendi)
    "cohere_ai":          "cohere-ai",           # Cohere modelleri (2026-08-02 eklendi)
    # "anthropic-ai" cikarildi: Anthropic'in artik kullanmadigi eski ad (gurultu).
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
    "claude_user":       "Claude-User",   # Anthropic kullanici-baslatan getirme (2026 guncel)
    # Site sahibinin KENDI istegiyle Vertex AI ajanlarinin taramasi (2026-08-02).
    "google_cloudvertexbot": "Google-CloudVertexBot",
}

# 🪤 ROBOTS.TXT ILE ENGELLENEMEYEN getiriciler (2026-08-02). Bunlar KULLANICI
# TETIKLEMELI: biri sohbette linki actigi icin gidip sayfayi cekerler ve
# robots.txt'i TASARIM GEREGI yok sayarlar. Raporda "izin verildi/verilmedi"
# demek YANILTICI olurdu — site sahibi bunlari robots ile durduramaz, ancak
# sunucu tarafi erisim denetimiyle durdurabilir.
# Kaynak: developers.google.com/crawling (Google'in kanonik referansi, 2025-11-20
# tasindi) + OpenAI ChatGPT-User dokumantasyonu.
ROBOTS_BAGLAMAZ = {
    "chatgpt_user":          "ChatGPT-User",       # OpenAI, kullanici tetikli
    "claude_user":           "Claude-User",        # Anthropic, kullanici tetikli
    "perplexity_user":       "Perplexity-User",    # Perplexity, kullanici tetikli
    "google_agent":          "Google-Agent",       # Google ajansal gezinme (Mariner)
    "google_notebooklm":     "Google-NotebookLM",  # kullanicinin ekledigi kaynak URL'i
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GeoniBot/1.0; +https://geoni.ai/bot)"
}


def _looks_like_bot_challenge(status_code: int, headers: dict, body: str) -> bool:
    """
    M6: Yanit bir bot-koruma/challenge sayfasi mi? (saf/offline test edilebilir)
    - 403/503 (ozellikle Cloudflare Server basligiyla) veya
    - govdede insan-dogrulama challenge imzasi.
    "Kesin blok" degil "OLABILIR" sinyali; soft bulgu uretir.
    """
    server = str((headers or {}).get("server", "")).lower()
    if status_code in (403, 503):
        return True
    if status_code == 429 and "cloudflare" in server:
        return True
    low = (body or "")[:2000].lower()
    return any(m in low for m in _BOT_CHALLENGE_MARKERS)


async def check_brave_index(domain: str, brand_name: str = "") -> dict | None:
    """
    T5 (ISKELET): Brave Search API ile "domain Brave'de indeksli mi" kontrolu.
    Brave, Claude'un retrieval indeksidir; burada indeksli olmak = Claude
    aramasinda var olmak. BRAVE_API_KEY MUHTEMELEN YOK -> None doner ve
    scoring tarafinda brave bacagi hic devreye girmez (mevcut davranis korunur).

    Donus: {"brave_indexed": bool, "results": int} veya None (olculemedi).
    brand_name verilirse ileride "markayi Brave'de kim aniyor" icin ek sorgu
    yapilabilir; iskelette yalnizca site: indeks sinyali olculur.
    """
    if not BRAVE_API_KEY or not domain:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": f"site:{domain}", "count": 5},
                headers={"Accept": "application/json",
                         "X-Subscription-Token": BRAVE_API_KEY},
                timeout=10,
            )
            if resp.status_code == 200:
                results = ((resp.json().get("web") or {}).get("results")) or []
                return {"brave_indexed": len(results) > 0, "results": len(results)}
            logger.info(f"Brave index check HTTP {resp.status_code} for {domain}")
    except Exception as e:
        logger.info(f"Brave index check failed for {domain}: {e}")
    return None


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
    bot_protection_suspected = False  # M6

    try:
        # SSRF-guvenli: safe_get her redirect hop'unu dogrular (apex<->www gibi
        # mesru kanonik redirect'ler korunur, ic adrese sicrama engellenir).
        async with httpx.AsyncClient() as client:
            resp = await safe_get(client, robots_url, timeout=10, headers=HEADERS)
            # M6: robots yaniti challenge/403 ise bot korumasi AI crawler'larini
            # (Brave/Perplexity) sessizce engelliyor OLABILIR — soft bulgu.
            bot_protection_suspected = _looks_like_bot_challenge(
                resp.status_code, dict(resp.headers), resp.text
            )
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
        # Rapor bunlari "izinli/engelli" DIYE gostermemeli: robots.txt onlari
        # baglamiyor. Istemci ayri bir etiket basar ("robots ile engellenemez").
        "robots_baglamaz": sorted(ROBOTS_BAGLAMAZ.values()),
        "bot_protection_suspected": bot_protection_suspected,  # M6
        # Geriye donuk uyumluluk icin: platform bazinda "en azindan bir arama
        # botu izinli mi" ozeti. Bu, "AI'da gorunurluk" acisindan egitim
        # botlarindan cok daha anlamli bir sinyaldir.
        "openai": arama["oai_searchbot"] or arama["chatgpt_user"],
        "anthropic": arama["claude_searchbot"] or egitim["claudebot"],
        "perplexity": arama["perplexitybot"] or arama["perplexity_user"],
        # Gemini'nin BOT IZNI: Google-Extended, Google'in uretken AI'sinin
        # (Gemini/Vertex grounding) icerigi kullanmasini yoneten robots.txt
        # jetonudur -- "Gemini bu siteyi kullanabilir mi" sorusunun tek dogru
        # karsiligi budur. Eskiden bu alan HIC uretilmiyordu ve main.py bosluga
        # Google SONUC SAYISINI koyuyordu (bkz. asagidaki not) -> rapor
        # "Gemini sitenize erisemiyor" diye YANLIS uyari basiyordu.
        "google": egitim["google_extended"],
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
    Best-effort: `domain`'in Google'da indeksli sayfa sayisi tahmini (site: SERP).

    Y8 (durustluk): Veri merkezinden yapilan bu istek cogunlukla consent/CAPTCHA/
    challenge sayfasi ya da 429 doner. Eski surum consent sayfasinda ham HTML'de
    `domain` string'ini sayarak (`resp.text.count(domain)`) gercek indeks olmayan
    bir sayi uretebiliyordu. Artik:
      - non-200 / bot-challenge / consent sayfasi tespit edilirse 0 (olculemedi),
        challenge sayfasindan SAHTE sayi turetilmez;
      - gecerli SERP'te de ham string tekrari yerine yalnizca `domain`'e giden
        GERCEK sonuc baglantilari (href) sayilir (asiri-sayimi onler).
    NOT: Bu sinyal hala guvenilmezdir (Bing ile ayni gerekce). Kalici cozum
    Google Search Console API ya da Tavily `site:` sorgusudur — bkz. denetim Y8.
    """
    import re
    query = f"site:{domain}"
    url = f"https://www.google.com/search?q={query}&num=10"

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=10, headers=HEADERS)
            # Y8: challenge/consent/429 -> olculemedi (0), sahte sayi uretme.
            if resp.status_code != 200 or _looks_like_bot_challenge(
                resp.status_code, dict(resp.headers), resp.text
            ):
                logger.info(f"Google index check: challenge/non-200 for {domain}, 0 dondu")
                return 0
            # Ham string tekrari degil, domain'e giden gercek sonuc baglantilari.
            links = re.findall(
                rf"https?://(?:[a-z0-9.-]+\.)?{re.escape(domain)}[/\"'?& ]",
                resp.text, re.IGNORECASE,
            )
            count = len(set(links))
            return min(count, sample_size * 20)  # rough cap
    except Exception as e:
        logger.warning(f"Google index check failed for {domain}: {e}")

    return 0


async def check_indexing_status(pages: list[dict], brand_name: str = "", domain: str = "") -> dict:
    """
    Check indexing status across Google and AI crawler access
    (egitim vs arama ayrimiyla, Madde 2.5), artı llms.txt sinyali.

    NOT (Madde 2.4): Bing kontrolu kaldirildi — bot korumasi nedeniyle
    guvenilir sonuc uretmiyordu ve iki skor boyutunu sessizce bozuyordu.

    T5: Brave indeks sinyali (BRAVE_API_KEY varsa) `brave_indexed` alanina
    yazilir; anahtar yoksa None (scoring atlar). M6: robots challenge/403
    tespiti `bot_protection_suspected` olarak tasinir.

    Returns:
        {
          "indexed_count": int,
          "google": int,
          "bot_access": {"egitim": {...}, "arama": {...}, "robots_found": bool},
          "llms_txt": bool,
          "brave_indexed": bool | None,   # T5 (None: anahtar yok/olculemedi)
          "bot_protection_suspected": bool,  # M6
          "openai": bool,      # geriye donuk uyumluluk (arama botlarina gore)
          "anthropic": bool,   # geriye donuk uyumluluk
          "perplexity": bool,
          "google_bot_allowed": bool,  # Google-Extended izni (Gemini)
        }
    """
    from urllib.parse import urlparse
    eff_domain = domain
    if not eff_domain and pages:
        eff_domain = urlparse(pages[0]["url"]).netloc or urlparse(pages[0]["url"]).path

    _empty = {
        "indexed_count": 0, "google": 0,
        "bot_access": {"egitim": {}, "arama": {}, "robots_found": False},
        "llms_txt": False, "brave_indexed": None, "bot_protection_suspected": False,
        "openai": False, "anthropic": False, "perplexity": False,
        "google_bot_allowed": False,
    }
    if not eff_domain:
        return _empty  # gercekten domain yok -> olculemez, tam-bos

    # KOK NEDEN FIX (derin test 2026-07-22): crawl 0 sayfa donse bile (bot-block/
    # JS/timeout) domain varsa robots/llms/google/brave httpx kontrolleri KOSAR
    # (Playwright'a bagimli degil). Eskiden pages bos -> tum kontroller atlanip
    # ai_access sahte-100 + bot_protection_suspected sahte-False kaliyordu -> buyuk/
    # korumali sitelere ongorulemez skor. 0 sayfa + gecerli domain = bot koruma suphesi.
    crawl_blocked = not pages

    # SSRF savunma-derinligi: host public degilse (ic-IP/metadata) hic istek atma.
    host = (eff_domain or "").split(":")[0]
    try:
        await asyncio.to_thread(assert_public_host, host)
    except BlockedHostError:
        logger.warning(f"indexing atlandi, public olmayan host: {host}")
        return _empty

    google_count, ai_access, llms_txt, brave = await asyncio.gather(
        check_google_indexed(eff_domain),
        check_robots_ai_access(eff_domain),
        check_llms_txt(eff_domain),
        check_brave_index(eff_domain, brand_name),
    )

    # crawl bloklandiysa len(pages)=0 ile cap'leme (indexed'i 0'a ezmesin).
    indexed_count = google_count if crawl_blocked else min(google_count, len(pages))

    return {
        "indexed_count": indexed_count,
        "google": google_count,
        "bot_access": {
            "egitim": ai_access["egitim"],
            "arama": ai_access["arama"],
            "robots_found": ai_access["robots_found"],
        },
        "llms_txt": llms_txt,
        # T5: Brave devrede degilse None (scoring 0.5/0.5'e duser).
        "brave_indexed": (brave.get("brave_indexed") if brave else None),
        "bot_protection_suspected": ai_access.get("bot_protection_suspected", False) or crawl_blocked,  # M6 + crawl-block
        "openai": ai_access.get("openai", True),
        "anthropic": ai_access.get("anthropic", True),
        "perplexity": ai_access.get("perplexity", True),
        # DIKKAT: ustteki "google" anahtari Google SONUC SAYISI (int).
        # Bot izni AYRI adla tasinir ki bir daha karistirilmasin.
        "google_bot_allowed": ai_access.get("google", True),
    }
