"""
GEONI Scanner - Indexing Checker Service
Checks whether crawled pages are indexed by Google, Bing,
and whether the domain allows OpenAI/Anthropic crawlers via robots.txt.

Notes on approach (no paid APIs required for MVP):
- Google/Bing: lightweight `site:` search via their public search HTML.
  This is best-effort and rate-limited; for production scale, swap in
  Google Search Console API / Bing Webmaster API with proper auth.
- OpenAI/Anthropic: parse robots.txt for GPTBot / ClaudeBot / anthropic-ai
  user-agent rules to determine if AI crawlers are allowed.
"""

import asyncio
import logging
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

AI_CRAWLER_AGENTS = {
    "openai": ["GPTBot", "ChatGPT-User", "OAI-SearchBot"],
    "anthropic": ["ClaudeBot", "anthropic-ai", "Claude-Web"],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GeoniBot/1.0; +https://geoni.ai/bot)"
}


async def check_robots_ai_access(domain: str) -> dict:
    """Check robots.txt for AI crawler allow/disallow rules."""
    robots_url = f"https://{domain}/robots.txt"
    allowed = {"openai": True, "anthropic": True}  # default: allowed if no robots.txt

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(robots_url, timeout=10, headers=HEADERS)
            if resp.status_code == 200:
                rp = RobotFileParser()
                rp.parse(resp.text.splitlines())
                for platform, agents in AI_CRAWLER_AGENTS.items():
                    platform_allowed = True
                    for agent in agents:
                        try:
                            if not rp.can_fetch(agent, f"https://{domain}/"):
                                platform_allowed = False
                                break
                        except Exception:
                            pass
                    allowed[platform] = platform_allowed
    except Exception as e:
        logger.warning(f"Could not check robots.txt for {domain}: {e}")

    return allowed


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
                # crude heuristic: count occurrences of the domain in result links
                count = resp.text.count(domain)
                return min(count, sample_size * 20)  # rough cap
    except Exception as e:
        logger.warning(f"Google index check failed for {domain}: {e}")

    return 0


async def check_bing_indexed(domain: str, sample_size: int = 5) -> int:
    """Best-effort Bing `site:` indexed count check."""
    query = f"site:{domain}"
    url = f"https://www.bing.com/search?q={query}&count=10"

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=10, headers=HEADERS)
            if resp.status_code == 200:
                count = resp.text.count(domain)
                return min(count, sample_size * 20)
    except Exception as e:
        logger.warning(f"Bing index check failed for {domain}: {e}")

    return 0


async def check_indexing_status(pages: list[dict]) -> dict:
    """
    Check indexing status across Google, Bing, and AI crawler access (OpenAI/Anthropic).

    Args:
        pages: list of page dicts from crawler (must have a 'url' or rely on domain).

    Returns:
        {
          "indexed_count": int,
          "google": int,
          "bing": int,
          "openai": bool,
          "anthropic": bool
        }
    """
    if not pages:
        return {"indexed_count": 0, "google": 0, "bing": 0, "openai": False, "anthropic": False}

    # Derive domain from first page URL
    from urllib.parse import urlparse
    domain = urlparse(pages[0]["url"]).netloc or urlparse(pages[0]["url"]).path

    google_count, bing_count, ai_access = await asyncio.gather(
        check_google_indexed(domain),
        check_bing_indexed(domain),
        check_robots_ai_access(domain),
    )

    indexed_count = max(google_count, bing_count)  # conservative estimate

    return {
        "indexed_count": min(indexed_count, len(pages)),
        "google": google_count,
        "bing": bing_count,
        "openai": ai_access.get("openai", True),
        "anthropic": ai_access.get("anthropic", True),
    }
