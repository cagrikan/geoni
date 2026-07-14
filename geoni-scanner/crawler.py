"""
GEONI Scanner - Crawler Service
Discovers pages under a domain using Playwright, extracts metadata,
respects robots.txt and sitemap.xml, with depth/page/time limits.
"""

import asyncio
import json
import logging
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from playwright.async_api import async_playwright

from ssrf_guard import assert_public_host, BlockedHostError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_PER_PAGE = 10  # seconds
DEFAULT_TOTAL_TIMEOUT = 300    # 5 minutes
DEFAULT_DEPTH_LIMIT = 3
DEFAULT_CONCURRENCY = 5


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
        resp = await client.get(robots_url, timeout=10)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.parse([])  # no restrictions
    except Exception as e:
        logger.warning(f"Could not fetch robots.txt: {e}")
        rp.parse([])
    return rp


async def fetch_sitemap(client: httpx.AsyncClient, base_url: str, limit: int = 200) -> dict:
    """
    Sitemap'ten URL listesi + lastmod tarihlerini ceker. lastmod tarihleri
    tazelik skorunun (scoring.py v3) gercek-tarih sinyalidir; eskiden hic
    okunmuyordu ve tazelik title icindeki yil metnine bakiyordu.
    """
    sitemap_url = urljoin(base_url, "/sitemap.xml")
    urls: list[str] = []
    lastmods: list[str] = []
    found = False
    try:
        resp = await client.get(sitemap_url, timeout=10)
        if resp.status_code == 200:
            # Very simple XML parse without extra deps
            import re
            found = True
            urls = re.findall(r"<loc>(.*?)</loc>", resp.text)[:limit]
            lastmods = re.findall(r"<lastmod>(.*?)</lastmod>", resp.text)[:limit]
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


async def extract_page_metadata(page) -> dict:
    """Extract title, meta description, h1, canonical, and JSON-LD schema types from a loaded Playwright page."""
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

    return {
        "title": title or "",
        "meta_description": meta_description or "",
        "h1": h1,
        "canonical_url": canonical_url or "",
        "schema_types": schema_types,
        "schema_dates": schema_dates,
        "noindex": noindex,
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

    async with httpx.AsyncClient(follow_redirects=True) as client:
        robots = await fetch_robots_txt(client, base_url)
        sitemap = await fetch_sitemap(client, base_url, limit=page_limit)
        for su in sitemap["urls"]:
            if su not in visited:
                queue.append((su, 1))

    semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="GeoniBot/1.0 (+https://geoni.ai/bot)"
        )

        async def visit(url: str, depth: int):
            if (
                url in visited
                or len(visited) >= page_limit
                or depth > DEFAULT_DEPTH_LIMIT
                or time.monotonic() - start_time > DEFAULT_TOTAL_TIMEOUT
            ):
                return []

            parsed = urlparse(url)
            if parsed.netloc and domain not in parsed.netloc:
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
                    metadata = await extract_page_metadata(page)
                    results.append({"url": url, **metadata})

                    if depth < DEFAULT_DEPTH_LIMIT:
                        hrefs = await page.eval_on_selector_all(
                            "a[href]", "els => els.map(e => e.href)"
                        )
                        for href in hrefs:
                            href_parsed = urlparse(href)
                            if href_parsed.netloc and domain in href_parsed.netloc:
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
            for links in batch_results:
                for link in links:
                    if link not in visited:
                        queue.append((link, batch[0][1] + 1))

        await browser.close()

    crawl_time_ms = int((time.monotonic() - start_time) * 1000)

    return {
        "domain": domain,
        "total_pages": len(results),
        "crawl_time_ms": crawl_time_ms,
        "pages": results,
        "sitemap_found": sitemap["found"],
        "sitemap_lastmods": sitemap["lastmods"],
    }
