"""
GEONI - Anthropic Admin API integration (Usage & Cost).

Optional: only active if ANTHROPIC_ADMIN_KEY is configured (a separate
Admin API key from Claude Console, distinct from the regular ANTHROPIC_API_KEY
used for scans). Provides real USD cost data for the admin panel - the only
one of the four external AI motors (OpenAI, Anthropic, Google, Perplexity)
that exposes a real cost/usage endpoint via API key at all.

Docs: https://platform.claude.com/docs/en/build-with-claude/usage-cost-api
"""

import os
import logging
from datetime import datetime, timedelta, timezone
import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_ADMIN_KEY = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
ANTHROPIC_VERSION = "2023-06-01"


async def get_anthropic_cost_summary() -> dict | None:
    """Real USD cost for today and the last 7 days from Anthropic's Cost Report API.
    Returns None if no admin key is configured (feature simply not enabled)."""
    if not ANTHROPIC_ADMIN_KEY:
        return None

    now = datetime.now(timezone.utc)
    starting_at = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    total_cents = 0.0
    today_cents = 0.0

    try:
        async with httpx.AsyncClient() as client:
            page = None
            for _ in range(10):  # safety cap - cost_report is daily-bucketed, should never need this many pages for a 7d window
                params = {"starting_at": starting_at.strftime(fmt), "ending_at": now.strftime(fmt)}
                if page:
                    params["page"] = page
                r = await client.get(
                    "https://api.anthropic.com/v1/organizations/cost_report",
                    headers={"anthropic-version": ANTHROPIC_VERSION, "x-api-key": ANTHROPIC_ADMIN_KEY},
                    params=params,
                    timeout=15,
                )
                if r.status_code != 200:
                    logger.warning(f"Anthropic cost report failed: {r.status_code} {r.text[:200]}")
                    return None
                body = r.json()
                for bucket in body.get("data", []):
                    bucket_start = bucket.get("starting_at", "")
                    for item in bucket.get("results", []):
                        amount = float(item.get("amount") or 0)
                        total_cents += amount
                        if bucket_start >= today_start.strftime(fmt):
                            today_cents += amount
                if not body.get("has_more"):
                    break
                page = body.get("next_page")
                if not page:
                    break
    except Exception as e:
        logger.warning(f"Anthropic cost report error: {e}")
        return None

    return {
        "usd_today": round(today_cents / 100, 4),
        "usd_week": round(total_cents / 100, 4),
    }
