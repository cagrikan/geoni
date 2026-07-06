"""
GEONI - Tavily usage/limit integration.

Unlike OpenAI/Anthropic/AWS, Tavily exposes real usage and plan-limit
numbers directly via the regular API key - no admin key, no scraping,
no manual entry needed.

Docs: https://docs.tavily.com/documentation/api-reference/endpoint/usage
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_API_KEY_2 = os.environ.get("TAVILY_API_KEY_2", "")


async def _fetch_usage(key: str) -> dict | None:
    if not key:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.tavily.com/usage",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if r.status_code != 200:
                logger.warning(f"Tavily usage fetch failed: {r.status_code} {r.text[:200]}")
                return None
            return r.json()
    except Exception as e:
        logger.warning(f"Tavily usage fetch error: {e}")
        return None


async def get_tavily_usage_summary() -> dict:
    """Real usage/limit for both Tavily accounts, keyed tavily-1/tavily-2 to
    match the provider labels already used for call-count tracking."""
    accounts = {}
    for label, key in (("tavily-1", TAVILY_API_KEY), ("tavily-2", TAVILY_API_KEY_2)):
        data = await _fetch_usage(key)
        if not data:
            continue
        acct = data.get("account") or {}
        accounts[label] = {
            "plan": acct.get("current_plan"),
            "plan_usage": acct.get("plan_usage"),
            "plan_limit": acct.get("plan_limit"),
            "paygo_usage": acct.get("paygo_usage"),
            "paygo_limit": acct.get("paygo_limit"),
        }
    return accounts
