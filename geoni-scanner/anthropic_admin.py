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
    """Real USD cost today/last-7-days/month-to-date from Anthropic's Cost
    Report API, fetched once from the start of the current month so all
    three figures (and the daily chart) come from a single query.
    Returns None if no admin key is configured (feature simply not enabled)."""
    if not ANTHROPIC_ADMIN_KEY:
        return None

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    daily_cents = {}

    try:
        async with httpx.AsyncClient() as client:
            page = None
            for _ in range(10):  # safety cap - one page per ~31 buckets, a month-to-date window fits in a handful
                params = {"starting_at": month_start.strftime(fmt), "ending_at": now.strftime(fmt)}
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
                    date_key = bucket.get("starting_at", "")[:10]
                    if not date_key:
                        continue
                    for item in bucket.get("results", []):
                        amount = float(item.get("amount") or 0)
                        daily_cents[date_key] = daily_cents.get(date_key, 0) + amount
                if not body.get("has_more"):
                    break
                page = body.get("next_page")
                if not page:
                    break
    except Exception as e:
        logger.warning(f"Anthropic cost report error: {e}")
        return None

    today_key = now.strftime("%Y-%m-%d")
    week_start_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    usd_today = daily_cents.get(today_key, 0) / 100
    usd_week = sum(c for d, c in daily_cents.items() if d >= week_start_key) / 100
    usd_month = sum(daily_cents.values()) / 100

    return {
        "usd_today": round(usd_today, 4),
        "usd_week": round(usd_week, 4),
        "usd_month": round(usd_month, 4),
        "daily": [{"date": d, "usd": round(c / 100, 4)} for d, c in sorted(daily_cents.items())],
    }
