"""
Web crawler module using Playwright.
Crawls domains respectfully with robots.txt support.
"""

import asyncio
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright, Browser
import httpx
from config import settings

logger = logging.getLogger(__name__)


@dataclass
class CrawledPage:
    """Represents a crawled page."""
    url: str
    title: Optional[str] = None
    meta_description: Optional[str] = None
    h1_text: Optional[str] = None
    canonical_url: Optional[str] = None
    word_count: int = 0
    has_schema_markup: bool = False
    last_modified: Optional[str] = None
    error: Optional[str] = None


class RobotsParser:
    """Simple robots.txt parser."""
    
    def __init__(self, domain: str):
        self.domain = domain
        self.disallowed = []
        self.delay = 0.5  # Default delay in seconds
        self.load()
    
    def load(self):
        """Load robots.txt from domain."""
        try:
            url = f"https://{self.domain}/robots.txt"
            response = httpx.get(url, timeout=5)
            if response.status_code == 200:
                self._parse_robots_txt(response.text)
        except Exception as e:
            logger.warning(f"Could not fetch robots.txt for {self.domain}: {e}")
    
    def _parse_robots_txt(self, content: str):
        """Parse robots.txt content."""
        lines = content.split('\n')
        user_agent_match = False
        
        for line in lines:
            line = line.strip()
            
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue
            
            # Check User-Agent directive
            if line.lower().startswith('user-agent:'):
                ua = line.split(':', 1)[1].strip()
                user_agent_match = ua == '*' or ua.lower() == 'geoni-scanner'
                continue
            
            # Parse Disallow and Crawl-Delay for matching User-Agent
            if user_agent_match:
                if line.lower().startswith('disallow:'):
                    path = line.split(':', 1)[1].strip()
                    if path:
                        self.disallowed.append(path)
                elif line.lower().startswith('crawl-delay:'):
                    delay_str = line.split(':', 1)[1].strip()
                    try:
                        self.delay = float(delay_str)
                    except ValueError:
                        pass
    
    def is_allowed(self, path: str) -> bool:
        """Check if path is allowed by robots.txt."""
        for disallowed in self.disallowed:
            if path.startswith(disallowed):
                return False
        return True


class DomainCrawler:
    """Crawls a single domain using Playwright."""
    
    def __init__(self, domain: str):
        self.domain = domain
        self.base_url = f"https://{self.domain}"
        self.robots = RobotsParser(domain)
        self.visited_urls = set()
        self.pages = []
        self.browser: Optional[Browser] = None
    
    async def crawl(self, max_pages: int = 500) -> List[CrawledPage]:
        """Crawl domain up to max_pages."""
        logger.info(f"Starting crawl of {self.domain} (max {max_pages} pages)")
        
        try:
            async with async_playwright() as p:
                self.browser = await p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                
                # Crawl with BFS strategy
                queue = [self.base_url]
                
                while queue and len(self.pages) < max_pages:
                    url = queue.pop(0)
                    
                    # Skip already visited
                    if url in self.visited_urls:
                        continue
                    
                    # Check robots.txt
                    parsed = urlparse(url)
                    if not self.robots.is_allowed(parsed.path):
                        logger.debug(f"Skipped by robots.txt: {url}")
                        continue
                    
                    # Crawl page
                    page_data = await self._crawl_page(url)
                    self.visited_urls.add(url)
                    
                    if page_data and not page_data.error:
                        self.pages.append(page_data)
                        
                        # Extract new URLs from page
                        new_urls = await self._extract_urls(url)
                        for new_url in new_urls:
                            if (new_url not in self.visited_urls and 
                                len(self.pages) < max_pages):
                                queue.append(new_url)
                    
                    # Respectful delay
                    await asyncio.sleep(self.robots.delay)
                
                await self.browser.close()
        
        except Exception as e:
            logger.error(f"Crawl error for {self.domain}: {e}")
            if self.browser:
                await self.browser.close()
        
        logger.info(f"Crawl complete: {len(self.pages)} pages")
        return self.pages
    
    async def _crawl_page(self, url: str) -> Optional[CrawledPage]:
        """Crawl a single page."""
        context = None
        page = None
        
        try:
            context = await self.browser.new_context(
                user_agent=settings.CRAWLER_USER_AGENT
            )
            page = await context.new_page()
            
            # Navigate with timeout
            await asyncio.wait_for(
                page.goto(url, wait_until='domcontentloaded'),
                timeout=settings.CRAWLER_TIMEOUT_PER_PAGE
            )
            
            # Extract data
            title = await page.title() or ""
            meta_desc = await page.evaluate(
                """() => {
                    let el = document.querySelector('meta[name="description"]');
                    return el ? el.getAttribute('content') : '';
                }"""
            )
            
            h1 = await page.evaluate(
                """() => {
                    let el = document.querySelector('h1');
                    return el ? el.textContent : '';
                }"""
            )
            
            canonical = await page.evaluate(
                """() => {
                    let el = document.querySelector('link[rel="canonical"]');
                    return el ? el.getAttribute('href') : '';
                }"""
            )
            
            has_schema = await page.evaluate(
                """() => {
                    return document.querySelector('script[type="application/ld+json"]') !== null;
                }"""
            )
            
            word_count = await page.evaluate(
                """() => {
                    return document.body.innerText.split(/\\s+/).length;
                }"""
            )
            
            return CrawledPage(
                url=url,
                title=title[:500] if title else None,
                meta_description=meta_desc[:500] if meta_desc else None,
                h1_text=h1[:500] if h1 else None,
                canonical_url=canonical or None,
                word_count=word_count or 0,
                has_schema_markup=has_schema or False
            )
        
        except asyncio.TimeoutError:
            logger.warning(f"Timeout crawling {url}")
            return CrawledPage(url=url, error="Timeout")
        
        except Exception as e:
            logger.error(f"Error crawling {url}: {e}")
            return CrawledPage(url=url, error=str(e))
        
        finally:
            if page:
                await page.close()
            if context:
                await context.close()
    
    async def _extract_urls(self, page_url: str) -> List[str]:
        """Extract URLs from a page."""
        if not self.browser:
            return []
        
        urls = []
        context = None
        page = None
        
        try:
            context = await self.browser.new_context()
            page = await context.new_page()
            await asyncio.wait_for(
                page.goto(page_url),
                timeout=settings.CRAWLER_TIMEOUT_PER_PAGE
            )
            
            # Get all links
            links = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.getAttribute('href'))
                    .filter(href => href);
            }""")
            
            for link in links:
                try:
                    # Normalize URL
                    absolute_url = urljoin(page_url, link)
                    parsed = urlparse(absolute_url)
                    
                    # Only same domain
                    if parsed.netloc == self.domain:
                        # Remove fragment
                        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                        if parsed.query:
                            clean_url += f"?{parsed.query}"
                        urls.append(clean_url)
                
                except Exception:
                    pass
            
            return list(set(urls))[:10]  # Limit to 10 new URLs per page
        
        except Exception as e:
            logger.debug(f"Error extracting URLs from {page_url}: {e}")
            return []
        
        finally:
            if page:
                await page.close()
            if context:
                await context.close()


async def crawl_domain(domain: str, max_pages: int = 500) -> Dict:
    """Crawl a domain and return results."""
    crawler = DomainCrawler(domain)
    pages = await crawler.crawl(max_pages)
    
    return {
        "domain": domain,
        "total_pages": len(pages),
        "pages": [
            {
                "url": p.url,
                "title": p.title,
                "meta_description": p.meta_description,
                "h1_text": p.h1_text,
                "canonical_url": p.canonical_url,
                "word_count": p.word_count,
                "has_schema_markup": p.has_schema_markup,
                "error": p.error
            }
            for p in pages
        ]
    }
