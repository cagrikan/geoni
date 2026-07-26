"""
GEONI Scanner - Crawler Service
Discovers pages under a domain using Playwright, extracts metadata,
respects robots.txt and sitemap.xml, with depth/page/time limits.
"""

import asyncio
import json
import logging
import os
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from ssrf_guard import assert_public_host, BlockedHostError, safe_get

# NOT: playwright import'u crawl_domain icine alindi (lazy) — sitemap/robots
# yardimcilarini (fetch_sitemap, _parse_sitemap_xml) playwright kurulu olmayan
# ortamlarda (offline birim testleri) import edebilmek icin.

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_PER_PAGE = 10  # seconds
# Fable 2026-07-24 (denetim): buyuk/SPA siteler (stripe.com, vercel.com) sitemap
# tukenmeden/link kuyrugu bosalmadan HEP bu tavana carpiyordu — canli olcum:
# stripe.com 311.6s/99 sayfa, vercel.com 307.6s/127 sayfa (page_limit=500'e HIC
# yaklasmadan, saf ZAMAN siniri). Musteri "site taraniyor"a 5+ dk bakiyordu.
# KOK NEDEN COZUMU (skorlama formulune DOKUNMADAN): scoring.py'deki TUM boyutlar
# (index_coverage/_indexability_score, freshness, meta_health, ai_access, schema)
# sayfa SAYISINA degil ORANA/ORTALAMAYA dayanir (dogrulandi) — 30-50 sayfalik
# temsili bir ornek, 500 sayfalik tam taramayla ISTATISTIKSEL OLARAK ESDEGER
# skor uretir. Tavan 300->90'a indi (kucuk/orta siteler zaten <32s'de bitiyor,
# ETKILENMEZ — olcum verisi); es zamanlilik 5->8 ile aynı 90sn'de DAHA COK
# sayfa taranir (stripe/vercel oraninda ~90s'de tahmini 25-45 sayfa, hala saglam
# ornek). Sonuc: buyuk siteler 5+dk -> ~90sn'ye iner, skor DEGISMEZ (ayni formul,
# temsili ornek). Env ile override edilebilir (redeploy gerekmeden ayar).
DEFAULT_TOTAL_TIMEOUT = int(os.environ.get("CRAWL_TOTAL_TIMEOUT", "90"))
DEFAULT_DEPTH_LIMIT = 3
DEFAULT_CONCURRENCY = int(os.environ.get("CRAWL_CONCURRENCY", "8"))


def normalize_domain(domain: str) -> str:
    """Strip protocol/path, return bare domain."""
    domain = domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0]
    return domain


async def fetch_robots_txt(client: httpx.AsyncClient, base_url: str) -> RobotFileParser:
    rp = RobotFileParser()
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        resp = await safe_get(client, robots_url, timeout=10)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.parse([])  # no restrictions
    except Exception as e:
        logger.warning(f"Could not fetch robots.txt: {e}")
        rp.parse([])
    return rp


def _parse_sitemap_xml(text: str) -> dict:
    """
    O9: Bir sitemap XML'ini ayristirir — iki tur destekli:
      - <urlset>:       gercek sayfa URL'leri (+ opsiyonel lastmod)
      - <sitemapindex>: alt-sitemap URL'leri (Yoast/WordPress her zaman index uretir)
    Onceki surum yalnizca duz <loc> findall yapiyordu; index dosyasinda alt-sitemap
    URL'lerini sayfa saniyordu (XML sayfasi crawl ediliyor, gercek URL'ler hic
    kesfedilmiyordu). Ayrica <url> blogu bazinda ayristirma ile lastmod DOGRU loc'a
    hizalanir (eski ayri-findall lastmod'u olmayan girdilerde kayiyordu).

    Saf/offline test edilebilir (ag yok). Donus:
      {"is_index": bool, "entries": [{"loc": str, "lastmod": str|None}, ...]}
    """
    is_index = "<sitemapindex" in text.lower()
    block_tag = "sitemap" if is_index else "url"
    entries: list[dict] = []
    for m in re.finditer(rf"<{block_tag}\b[^>]*>(.*?)</{block_tag}>", text,
                         re.DOTALL | re.IGNORECASE):
        block = m.group(1)
        loc_m = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.DOTALL | re.IGNORECASE)
        if not loc_m:
            continue
        loc = loc_m.group(1).strip()
        if not loc:
            continue
        lm_m = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.DOTALL | re.IGNORECASE)
        entries.append({"loc": loc, "lastmod": lm_m.group(1).strip() if lm_m else None})
    # Bloklu ayristirma bos dondurduyse (bicimsiz/tek satir XML) kaba <loc>'a duş
    if not entries:
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.DOTALL | re.IGNORECASE):
            loc = loc.strip()
            if loc:
                entries.append({"loc": loc, "lastmod": None})
    return {"is_index": is_index, "entries": entries}


async def fetch_sitemap(client: httpx.AsyncClient, base_url: str, limit: int = 200,
                        _max_child_sitemaps: int = 10, deadline: float | None = None) -> dict:
    """
    Sitemap'ten URL listesi + lastmod tarihlerini ceker. lastmod tarihleri
    tazelik skorunun (scoring.py v3) gercek-tarih sinyalidir; eskiden hic
    okunmuyordu ve tazelik title icindeki yil metnine bakiyordu.

    O9: sitemap_index.xml (sitemapindex) destegi — index ise alt-sitemap'ler
    (bounded, en cok _max_child_sitemaps) tek tek cozulur. lastmods artik urls
    ile 1:1 hizali doner (eksikse "" placeholder; scoring bunu tolere eder).
    """
    sitemap_url = urljoin(base_url, "/sitemap.xml")
    urls: list[str] = []
    lastmods: list[str] = []
    found = False
    try:
        resp = await safe_get(client, sitemap_url, timeout=10)
        if resp.status_code == 200:
            found = True
            parsed = _parse_sitemap_xml(resp.text)
            if parsed["is_index"]:
                # O9: alt-sitemap URL'lerini (bounded) coz; her hop safe_get'ten
                # gecer (SSRF). Bir alt-sitemap hatasi digerlerini bozmaz.
                child_locs = [e["loc"] for e in parsed["entries"]][:_max_child_sitemaps]
                for cu in child_locs:
                    if len(urls) >= limit:
                        break
                    # SURE SINIRI KESFI DE KAPSAR. Toplam sinir (CRAWL_TOTAL_TIMEOUT)
                    # yalnizca crawl DONGUSUNDE kontrol ediliyordu; robots+sitemap
                    # kesfi kapsam disindaydi. En kotu durumda 1 kok + 10 alt
                    # sitemap x 10sn = ~110sn, tek kontrol yapilmadan yanabiliyordu.
                    # Uretimde crawl fazinin 311sn surdugu tarama gorulduğu icin
                    # (PHASE_TIMING, 2026-07-26) bu bosluk kapatildi: butcesi
                    # bitmisse elde ne varsa onunla devam et — bos donme.
                    if deadline is not None and time.monotonic() > deadline:
                        logger.info(f"Sitemap kesfi sure sinirinda kesildi ({len(urls)} URL toplandi)")
                        break
                    try:
                        cr = await safe_get(client, cu, timeout=10)
                        if cr.status_code == 200:
                            for e in _parse_sitemap_xml(cr.text)["entries"]:
                                if len(urls) >= limit:
                                    break
                                urls.append(e["loc"])
                                lastmods.append(e["lastmod"] or "")
                    except Exception as e:
                        logger.info(f"Alt-sitemap cekilemedi ({cu}): {e}")
            else:
                for e in parsed["entries"][:limit]:
                    urls.append(e["loc"])
                    lastmods.append(e["lastmod"] or "")
    except Exception as e:
        logger.info(f"No sitemap.xml found or error fetching it: {e}")
    return {"found": found, "urls": urls, "lastmods": lastmods}


def _extract_schema_info(raw_blocks: list[str]) -> dict:
    """
    Parse <script type="application/ld+json"> block contents and return the
    distinct @type values found (e.g. ["Organization", "FAQPage"]) plus any
    datePublished/dateModified values (tazelik skoru v3 icin gercek tarih
    sinyali). Tolerant of arrays, @graph wrappers, and malformed JSON.
    """
    types: set[str] = set()
    dates: set[str] = set()

    def _collect(node):
        if isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                types.add(t)
            elif isinstance(t, list):
                types.update(x for x in t if isinstance(x, str))
            for date_key in ("datePublished", "dateModified"):
                d = node.get(date_key)
                if isinstance(d, str) and d.strip():
                    dates.add(d.strip())
            if "@graph" in node and isinstance(node["@graph"], list):
                for item in node["@graph"]:
                    _collect(item)
        elif isinstance(node, list):
            for item in node:
                _collect(item)

    for raw in raw_blocks:
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
            _collect(data)
        except Exception:
            continue

    return {"types": sorted(types), "dates": sorted(dates)}


async def extract_page_metadata(page, is_home: bool = False) -> dict:
    """Extract title, meta description, h1, canonical, and JSON-LD schema types from a loaded Playwright page.
    is_home=True'da ayrica site_assets (logo/sameAs/arama ucu) cikarilir — schema.org
    zenginlestirmesi icin (yalnizca ana sayfada, ek maliyet yok)."""
    try:
        title = await page.title()
    except Exception:
        title = ""

    meta_description = ""
    h1 = ""
    canonical_url = ""
    schema_types: list[str] = []
    schema_dates: list[str] = []
    noindex = False

    try:
        meta_description = await page.eval_on_selector(
            "meta[name='description']", "el => el.content"
        )
    except Exception:
        pass

    try:
        robots_meta = await page.eval_on_selector(
            "meta[name='robots']", "el => el.content"
        )
        noindex = "noindex" in (robots_meta or "").lower()
    except Exception:
        pass

    try:
        h1 = await page.eval_on_selector("h1", "el => el.textContent")
        h1 = h1.strip() if h1 else ""
    except Exception:
        pass

    try:
        canonical_url = await page.eval_on_selector(
            "link[rel='canonical']", "el => el.href"
        )
    except Exception:
        pass

    try:
        raw_ld_blocks = await page.eval_on_selector_all(
            "script[type='application/ld+json']", "els => els.map(e => e.textContent)"
        )
        schema_info = _extract_schema_info(raw_ld_blocks or [])
        schema_types = schema_info["types"]
        schema_dates = schema_info["dates"]
    except Exception:
        pass

    site_assets: dict = {}
    if is_home:
        # P1: schema.org zenginlestirmesi — logo (og:image/icon), sosyal profiller
        # (sameAs), arama ucu (SearchAction). Tumu try icinde; crawl'i asla bozma.
        try:
            site_assets = await page.evaluate(r"""() => {
                const abs = (u) => { try { return new URL(u, location.href).href } catch { return null } };
                let logo = (document.querySelector("meta[property='og:image']") || {}).content
                    || (document.querySelector("link[rel='apple-touch-icon']") || {}).href
                    || (document.querySelector("link[rel~='icon']") || {}).href || null;
                logo = logo ? abs(logo) : null;
                const hosts = ['instagram.com','facebook.com','twitter.com','x.com','linkedin.com','youtube.com','tiktok.com'];
                const sameAs = [...new Set([...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => {
                    try { const hn = new URL(h).hostname.replace(/^www\./,''); return hosts.some(s => hn === s || hn.endsWith('.'+s)); } catch { return false }
                }))].slice(0, 8);
                const hasSearch = !!document.querySelector("form[action*='search'], form[action*='ara'], input[type='search'], [role='search']");
                return { logo, sameAs, has_search: hasSearch };
            }""")
        except Exception:
            site_assets = {}

    return {
        "title": title or "",
        "meta_description": meta_description or "",
        "h1": h1,
        "canonical_url": canonical_url or "",
        "schema_types": schema_types,
        "schema_dates": schema_dates,
        "noindex": noindex,
        "site_assets": site_assets,
    }


async def crawl_domain(domain: str, page_limit: int = 500) -> dict:
    """
    Crawl `domain` using Playwright with a BFS strategy.
    Respects robots.txt, seeds from sitemap.xml when available,
    enforces depth/page/time limits, extracts page metadata.

    Returns:
        {
          "domain": str,
          "total_pages": int,
          "crawl_time_ms": int,
          "pages": [ { url, title, meta_description, h1, canonical_url }, ... ]
        }
    """
    from playwright.async_api import async_playwright  # lazy (bkz. import notu)

    start_time = time.monotonic()
    domain = normalize_domain(domain)
    # SSRF korumasi: robots/sitemap dahil HERHANGI bir istek atilmadan once
    # host'un public bir adrese cozuldugunu dogrula. Tum crawl cagiranlari
    # (API, monitor, marka) bu tek noktadan gecer.
    await asyncio.to_thread(assert_public_host, domain)
    base_url = f"https://{domain}/"

    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(base_url, 0)]  # (url, depth)
    results: list[dict] = []

    # SSRF: robots/sitemap alt-istekleri redirect KAPALI. Domain yukarida
    # assert_public_host'tan gecti; 30x ile ic adrese sicramasin.
    async with httpx.AsyncClient(follow_redirects=False) as client:
        robots = await fetch_robots_txt(client, base_url)
        # Kesif icin toplam butcenin YARISI: kalan yari gercek sayfa taramasina
        # kalsin. Sitemap bos donerse crawl yine ana sayfadan BFS ile ilerler.
        sitemap = await fetch_sitemap(client, base_url, limit=page_limit,
                                      deadline=start_time + DEFAULT_TOTAL_TIMEOUT * 0.5)
        for su in sitemap["urls"]:
            if su not in visited:
                queue.append((su, 1))

    semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="GeoniBot/1.0 (+https://geoni.ai/bot)"
        )

        # Fable 2026-07-23 (SSRF): page.goto redirect'leri Chromium ic-ele takip eder;
        # kullanicinin domaini 302 ile 169.254.170.2 (ECS credential ucu) / private IP'ye
        # yonlendirebilir. assert_public_host yalniz basta bir kez cagriliyordu -> yetersiz.
        # HER BELGE (navigasyon+redirect) istegini SSRF guard'dan gecir; ic host -> ISTEGI KES.
        async def _ssrf_guard_route(route):
            req = route.request
            if req.resource_type == "document":
                host = (urlparse(req.url).hostname or "")
                try:
                    await asyncio.to_thread(assert_public_host, host)
                except Exception:
                    await route.abort()
                    return
            await route.continue_()
        await context.route("**/*", _ssrf_guard_route)

        site_assets_holder: dict = {}  # P1: ana sayfadan logo/sameAs/arama ucu

        async def visit(url: str, depth: int):
            if (
                url in visited
                or len(visited) >= page_limit
                or depth > DEFAULT_DEPTH_LIMIT
                or time.monotonic() - start_time > DEFAULT_TOTAL_TIMEOUT
            ):
                return []

            parsed = urlparse(url)
            # T3: substring degil exact/subdomain eslesme (acme.com taramasi
            # acme.com.evil.com / notacme.com'a tasmasin).
            dom = domain.lower()
            host = (parsed.netloc or "").split(":")[0].lower()
            if parsed.netloc and not (host == dom or host.endswith("." + dom)):
                return []  # stay on-domain

            if not robots.can_fetch("GeoniBot", url):
                return []

            visited.add(url)
            new_links: list[str] = []

            async with semaphore:
                page = await context.new_page()
                try:
                    await page.goto(
                        url, timeout=DEFAULT_TIMEOUT_PER_PAGE * 1000, wait_until="domcontentloaded"
                    )
                    # SSRF 2. katman: redirect SONRASI gercek adres (page.url) hala public +
                    # on-domain mi? Degilse ic-servis icerigi YAKALANMASIN (route kacsa bile).
                    final = urlparse(page.url)
                    fhost = (final.netloc or "").split(":")[0].lower()
                    if fhost and not (fhost == dom or fhost.endswith("." + dom)):
                        return []  # off-domain/ic adrese yonlendi -> icerigi atla
                    try:
                        await asyncio.to_thread(assert_public_host, fhost)
                    except Exception:
                        return []  # ic/engelli host -> icerigi atla
                    metadata = await extract_page_metadata(page, is_home=(depth == 0))
                    sa = metadata.pop("site_assets", None)
                    if depth == 0 and sa:
                        site_assets_holder.update(sa)  # P1: ana sayfa logo/sameAs
                    results.append({"url": url, **metadata})

                    if depth < DEFAULT_DEPTH_LIMIT:
                        hrefs = await page.eval_on_selector_all(
                            "a[href]", "els => els.map(e => e.href)"
                        )
                        for href in hrefs:
                            hp = (urlparse(href).netloc or "").split(":")[0].lower()
                            if hp and (hp == dom or hp.endswith("." + dom)):
                                clean = href.split("#")[0]
                                if clean not in visited:
                                    new_links.append(clean)
                except Exception as e:
                    logger.warning(f"Failed to crawl {url}: {e}")
                finally:
                    await page.close()

            return new_links

        while queue and len(visited) < page_limit:
            if time.monotonic() - start_time > DEFAULT_TOTAL_TIMEOUT:
                logger.info("Crawl total timeout reached, stopping.")
                break

            batch = queue[:DEFAULT_CONCURRENCY]
            queue = queue[DEFAULT_CONCURRENCY:]

            batch_results = await asyncio.gather(
                *[visit(url, depth) for url, depth in batch]
            )
            # O8: her sayfanin linklerine KENDI derinligi+1 verilir. Eskiden
            # batch[0][1]+1 kullaniliyordu -> batch'teki tum sayfalarin linkleri
            # ilk elemanin derinligini aliyordu (sitemap seed'i depth 1 ile derin
            # sayfa ayni batch'e dusunce limit yanlis uygulaniyordu).
            for (src_url, src_depth), links in zip(batch, batch_results):
                for link in links:
                    if link not in visited:
                        queue.append((link, src_depth + 1))

        await browser.close()

    crawl_time_ms = int((time.monotonic() - start_time) * 1000)

    return {
        "domain": domain,
        "total_pages": len(results),
        "crawl_time_ms": crawl_time_ms,
        "pages": results,
        "sitemap_found": sitemap["found"],
        "sitemap_lastmods": sitemap["lastmods"],
        "site_assets": site_assets_holder,
    }
